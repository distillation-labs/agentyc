"""Event-driven CDP session management.

Manages CDP sessions by listening to Target.attachedToTarget and Target.detachedFromTarget
events, ensuring the session pool always reflects the current browser state.
"""

import asyncio
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from cdp_use.cdp.target import AttachedToTargetEvent, DetachedFromTargetEvent, SessionID, TargetID

from agentyc.browser.session_manager_events import (
	handle_target_attached as handle_target_attached_helper,
	handle_target_detached as handle_target_detached_helper,
	handle_target_info_changed as handle_target_info_changed_helper,
	start_monitoring as start_monitoring_helper,
)
from agentyc.browser.session_manager_recovery import (
	ensure_valid_focus as ensure_valid_focus_helper,
	recover_agent_focus as recover_agent_focus_helper,
	recover_focus_on_demand as recover_focus_on_demand_helper,
)
from agentyc.browser.session_manager_support import (
	apply_target_info,
	enable_page_monitoring,
	initialize_existing_targets,
	set_target_window_context,
)
from agentyc.browser.session_models import CDPSession, Target
from agentyc.utils import create_task_with_error_handling

if TYPE_CHECKING:
	from agentyc.browser.session import BrowserSession


class SessionManager:
	"""Event-driven CDP session manager.

	Automatically synchronizes the CDP session pool with browser state via CDP events.

	Key features:
	- Sessions added/removed automatically via Target attach/detach events
	- Multiple sessions can attach to the same target
	- Targets only removed when ALL sessions detach
	- No stale sessions - pool always reflects browser reality

	SessionManager is the SINGLE SOURCE OF TRUTH for all targets and sessions.
	"""

	def __init__(self, browser_session: 'BrowserSession'):
		self.browser_session = browser_session
		self.logger = browser_session.logger

		# All targets (entities: pages, iframes, workers)
		self._targets: dict[TargetID, 'Target'] = {}

		# All sessions (communication channels)
		self._sessions: dict[SessionID, 'CDPSession'] = {}

		# Mapping: target -> sessions attached to it, kept in attach order so callers
		# can deterministically prefer the newest live session for a target.
		self._target_sessions: dict[TargetID, list[SessionID]] = {}

		# Reverse mapping: session -> target it belongs to
		self._session_to_target: dict[SessionID, TargetID] = {}

		self._lock = asyncio.Lock()
		self._recovery_lock = asyncio.Lock()

		# Focus recovery coordination - event-driven instead of polling
		self._recovery_in_progress: bool = False
		self._recovery_complete_event: asyncio.Event | None = None
		self._recovery_task: asyncio.Task | None = None


	def _apply_target_info(self, target: Target, target_info: Mapping[str, Any]) -> None:
		apply_target_info(target, target_info)



	def set_target_window_context(self, target_id: TargetID, *, window_id: int | None = None, window_bounds=None) -> None:
		set_target_window_context(self._targets.get(target_id), window_id=window_id, window_bounds=window_bounds)

	async def start_monitoring(self) -> None:
		"""Start monitoring Target attach/detach events."""
		await start_monitoring_helper(self)

	def _get_session_for_target(self, target_id: TargetID) -> 'CDPSession | None':
		"""Internal: Get ANY valid session for a target (picks first available).

		⚠️ INTERNAL API - Use browser_session.get_or_create_cdp_session() instead!
		This method has no validation, no focus management, no recovery.

		Args:
			target_id: Target ID to get session for

		Returns:
			CDPSession if exists, None if target has detached
		"""
		session_ids = self._target_sessions.get(target_id, [])
		if not session_ids:
			# Check if this is the focused target - indicates stale focus that needs cleanup
			if self.browser_session.agent_focus_target_id == target_id:
				self.logger.warning(
					f'[SessionManager] ⚠️ Attempted to get session for stale focused target {target_id[:8]}... '
					f'Clearing stale focus and triggering recovery.'
				)

				# Clear stale focus immediately (defense in depth)
				self.browser_session.agent_focus_target_id = None

				# Trigger recovery if not already in progress
				if not self._recovery_in_progress:
					self.logger.warning('[SessionManager] Recovery was not in progress! Triggering now.')
					self._recovery_task = create_task_with_error_handling(
						self._recover_agent_focus(target_id),
						name='recover_agent_focus_from_stale_get',
						logger_instance=self.logger,
						suppress_exceptions=False,
					)
			return None
		target = self._targets.get(target_id)
		if target and target.target_type in ('page', 'tab'):
			latest_session_id = session_ids[-1]
			cdp_session = self._sessions.get(latest_session_id)
			if cdp_session is not None and hasattr(cdp_session, '_lifecycle_events'):
				return cdp_session
			return None

		return self._sessions.get(session_ids[-1])

	def get_all_page_targets(self) -> list:
		"""Get all page/tab targets using owned data.

		Returns:
			List of Target objects for all page/tab targets
		"""
		page_targets = []
		for target in self._targets.values():
			if target.target_type in ('page', 'tab'):
				page_targets.append(target)
		return page_targets

	def is_target_owned_by_current_runtime(self, target_id: TargetID) -> bool:
		"""Return True when a shared-browser target belongs to this runtime.

		Non-shared sessions can access any tracked target.
		"""
		if not self.browser_session.is_shared_browser_runtime:
			return True
		target = self.get_target(target_id)
		if target is None or target.ownership is None or target.ownership.runtime is None:
			return False
		return target.ownership.runtime.runtime_id == self.browser_session.runtime_metadata.runtime_id

	def get_owned_page_targets(self) -> list:
		"""Get page/tab targets owned by the current runtime.

		In non-shared mode this is equivalent to get_all_page_targets().
		"""
		page_targets = self.get_all_page_targets()
		if not self.browser_session.is_shared_browser_runtime:
			return page_targets
		return [target for target in page_targets if self.is_target_owned_by_current_runtime(target.target_id)]

	async def validate_session(self, target_id: TargetID) -> bool:
		"""Check if a target still has active sessions.

		Args:
			target_id: Target ID to validate

		Returns:
			True if target has active sessions, False if it should be removed
		"""
		if target_id not in self._target_sessions:
			return False
		return len(self._target_sessions[target_id]) > 0

	async def clear(self) -> None:
		"""Clear all owned data structures for cleanup."""
		async with self._lock:
			# Clear owned data (single source of truth)
			self._targets.clear()
			self._sessions.clear()
			self._target_sessions.clear()
			self._session_to_target.clear()

		self.logger.info('[SessionManager] Cleared all owned data (targets, sessions, mappings)')

	async def is_target_valid(self, target_id: TargetID) -> bool:
		"""Check if a target is still valid and has active sessions.

		Args:
			target_id: Target ID to validate

		Returns:
			True if target is valid and has active sessions, False otherwise
		"""
		if target_id not in self._target_sessions:
			return False
		return len(self._target_sessions[target_id]) > 0

	def get_target_id_from_session_id(self, session_id: SessionID) -> TargetID | None:
		"""Look up which target a session belongs to.

		Args:
			session_id: The session ID to look up

		Returns:
			Target ID if found, None otherwise
		"""
		return self._session_to_target.get(session_id)

	def get_target(self, target_id: TargetID) -> 'Target | None':
		"""Get target from owned data.

		Args:
			target_id: Target ID to get

		Returns:
			Target object if found, None otherwise
		"""
		return self._targets.get(target_id)

	def get_all_targets(self) -> dict[TargetID, 'Target']:
		"""Get all targets (read-only access to owned data).

		Returns:
			Dict mapping target_id to Target objects
		"""
		return self._targets

	def get_all_target_ids(self) -> list[TargetID]:
		"""Get all target IDs from owned data.

		Returns:
			List of all target IDs
		"""
		return list(self._targets.keys())

	def get_all_sessions(self) -> dict[SessionID, 'CDPSession']:
		"""Get all sessions (read-only access to owned data).

		Returns:
			Dict mapping session_id to CDPSession objects
		"""
		return self._sessions

	def get_session(self, session_id: SessionID) -> 'CDPSession | None':
		"""Get session from owned data.

		Args:
			session_id: Session ID to get

		Returns:
			CDPSession object if found, None otherwise
		"""
		return self._sessions.get(session_id)

	def get_all_sessions_for_target(self, target_id: TargetID) -> list['CDPSession']:
		"""Get ALL sessions attached to a target from owned data.

		Args:
			target_id: Target ID to get sessions for

		Returns:
			List of all CDPSession objects for this target
		"""
		session_ids = self._target_sessions.get(target_id, [])
		return [self._sessions[sid] for sid in session_ids if sid in self._sessions]

	def get_target_sessions_mapping(self) -> dict[TargetID, list[SessionID]]:
		"""Get target->sessions mapping (read-only access).

		Returns:
			Dict mapping target_id to session_ids in attach order
		"""
		return self._target_sessions

	def get_focused_target(self) -> 'Target | None':
		"""Get the target that currently has agent focus.

		Convenience method that uses browser_session.agent_focus_target_id.

		Returns:
			Target object if agent has focus, None otherwise
		"""
		if not self.browser_session.agent_focus_target_id:
			return None
		return self.get_target(self.browser_session.agent_focus_target_id)

	async def ensure_valid_focus(self, timeout: float = 3.0) -> bool:
		"""Ensure agent_focus_target_id points to a valid, attached CDP session.

		If the focus target is stale (detached), this method waits for automatic recovery.
		Uses event-driven coordination instead of polling for efficiency.

		Args:
			timeout: Maximum time to wait for recovery in seconds (default: 3.0)

		Returns:
			True if focus is valid or successfully recovered, False if no focus or recovery failed
		"""
		return await ensure_valid_focus_helper(self, timeout=timeout)

	async def _recover_focus_on_demand(self, timeout: float = 3.0) -> bool:
		"""Recover focus only when a caller explicitly needs a usable page."""
		return await recover_focus_on_demand_helper(self, timeout=timeout)

	async def _handle_target_attached(self, event: AttachedToTargetEvent) -> None:
		"""Handle Target.attachedToTarget event."""
		await handle_target_attached_helper(self, event)

	async def _handle_target_info_changed(self, event: dict) -> None:
		"""Handle Target.targetInfoChanged event."""
		await handle_target_info_changed_helper(self, event)

	async def _handle_target_detached(self, event: DetachedFromTargetEvent) -> None:
		"""Handle Target.detachedFromTarget event."""
		await handle_target_detached_helper(self, event)

	async def _recover_agent_focus(self, crashed_target_id: TargetID) -> None:
		"""Auto-recover agent focus when the focused target crashes or detaches."""
		await recover_agent_focus_helper(self, crashed_target_id)

	async def _initialize_existing_targets(self) -> None:
		await initialize_existing_targets(self)

	async def _enable_page_monitoring(self, cdp_session: 'CDPSession') -> None:
		await enable_page_monitoring(self, cdp_session)
