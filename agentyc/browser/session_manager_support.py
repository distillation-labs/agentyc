"""Shared helper functions for SessionManager."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from cdp_use.cdp.target import SessionID

from agentyc.browser.session_models import CDPSession, Target

if TYPE_CHECKING:
	from agentyc.browser.session_manager import SessionManager

INITIAL_TARGET_ATTACH_WAIT_TIMEOUT_S = 0.25
INITIAL_TARGET_ATTACH_POLL_INTERVAL_S = 0.05


def apply_target_info(target: Target, target_info: Mapping[str, Any]) -> None:
	target.title = str(target_info.get('title', target.title or 'Unknown title'))
	target.url = str(target_info.get('url', target.url))


def set_target_window_context(target: Target | None, *, window_id: int | None = None, window_bounds: Any = None) -> None:
	if target is None:
		return
	if window_id is not None:
		target.window_id = window_id


async def initialize_existing_targets(manager: SessionManager) -> None:
	"""Discover and initialize all existing targets at startup."""
	cdp_client = manager.browser_session._cdp_client_root
	assert cdp_client is not None

	targets_result = await cdp_client.send.Target.getTargets()
	existing_targets = targets_result.get('targetInfos', [])
	manager.logger.debug(f'[SessionManager] Discovered {len(existing_targets)} existing targets')

	target_ids_to_wait_for = [
		t['targetId'] for t in existing_targets
		if t.get('type', 'unknown') in {'page', 'tab', 'iframe', 'background_page', 'service_worker', 'worker'}
	]

	if not target_ids_to_wait_for:
		return

	def _ready_count() -> int:
		count = 0
		for tid in target_ids_to_wait_for:
			session = manager._get_session_for_target(tid)
			if session:
				target = manager._targets.get(tid)
				if target and target.target_type in ('page', 'tab'):
					if getattr(session, '_lifecycle_events', None) is not None:
						count += 1
				else:
					count += 1
		return count

	loop = asyncio.get_running_loop()
	deadline = loop.time() + INITIAL_TARGET_ATTACH_WAIT_TIMEOUT_S
	while True:
		if _ready_count() == len(target_ids_to_wait_for):
			return
		if loop.time() >= deadline:
			return
		await asyncio.sleep(INITIAL_TARGET_ATTACH_POLL_INTERVAL_S)


async def enable_page_monitoring(manager: SessionManager, cdp_session: CDPSession) -> None:
	"""Enable lifecycle events and network monitoring for a page target."""
	try:
		await cdp_session.cdp_client.send.Page.enable(session_id=cdp_session.session_id)
		await cdp_session.cdp_client.send.Page.setLifecycleEventsEnabled(
			params={'enabled': True}, session_id=cdp_session.session_id
		)
		await cdp_session.cdp_client.send.Network.enable(session_id=cdp_session.session_id)

		from collections import deque
		cdp_session._lifecycle_events = deque(maxlen=50)
		cdp_session._lifecycle_lock = asyncio.Lock()

		def on_lifecycle_event(event, session_id: SessionID | None = None) -> None:
			target_id_from_event = manager.get_target_id_from_session_id(session_id) if session_id else None
			if target_id_from_event == cdp_session.target_id:
				try:
					cdp_session._lifecycle_events.append({
						'name': event.get('name', 'unknown'),
						'loaderId': event.get('loaderId', 'none'),
						'timestamp': asyncio.get_event_loop().time(),
					})
				except Exception:
					pass

		cdp_session.cdp_client.register.Page.lifecycleEvent(on_lifecycle_event)

	except Exception as error:
		error_str = str(error)
		if '-32001' in error_str or 'Session with given id not found' in error_str:
			manager.logger.debug(f'[SessionManager] Target detached before monitoring enabled (normal)')
		else:
			manager.logger.warning(f'[SessionManager] Failed to enable monitoring for {cdp_session.target_id[:8]}...: {error}')
