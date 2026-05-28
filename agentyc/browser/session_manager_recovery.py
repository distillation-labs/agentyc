"""Focus-recovery helpers for :mod:`agentyc.browser.session_manager`."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from cdp_use.cdp.target import TargetID

from agentyc.utils import is_new_tab_page

if TYPE_CHECKING:
	from agentyc.browser.session_manager import SessionManager


async def ensure_valid_focus(manager: 'SessionManager', timeout: float = 3.0) -> bool:
	"""Ensure agent focus points to a valid, attached CDP session."""
	if not manager.browser_session.agent_focus_target_id:
		if manager._recovery_in_progress and manager._recovery_complete_event:
			try:
				await asyncio.wait_for(manager._recovery_complete_event.wait(), timeout=timeout)
				focus_id = manager.browser_session.agent_focus_target_id
				if focus_id and manager._get_session_for_target(focus_id):
					return True
				return await recover_focus_on_demand(manager, timeout=timeout)
			except TimeoutError:
				manager.logger.error(f'[SessionManager] ❌ Timed out waiting for recovery after {timeout}s')
				return False
		return await recover_focus_on_demand(manager, timeout=timeout)

	cdp_session = manager._get_session_for_target(manager.browser_session.agent_focus_target_id)
	if cdp_session:
		is_valid = await manager.validate_session(manager.browser_session.agent_focus_target_id)
		if is_valid:
			return True

	stale_target_id = manager.browser_session.agent_focus_target_id
	manager.logger.warning(
		f'[SessionManager] ⚠️ Stale agent_focus detected (target {stale_target_id[:8] if stale_target_id else "None"}... detached), '
		f'waiting for recovery...'
	)

	if not manager._recovery_in_progress:
		manager.logger.warning(
			'[SessionManager] ⚠️ Recovery not in progress for stale focus! This indicates a bug - recovery should have been triggered.'
		)
		return await recover_focus_on_demand(manager, timeout=timeout)

	if manager._recovery_complete_event:
		try:
			start_time = asyncio.get_event_loop().time()
			await asyncio.wait_for(manager._recovery_complete_event.wait(), timeout=timeout)
			elapsed = asyncio.get_event_loop().time() - start_time
			focus_id = manager.browser_session.agent_focus_target_id
			if focus_id and manager._get_session_for_target(focus_id):
				manager.logger.info(
					f'[SessionManager] ✅ Agent focus recovered to {manager.browser_session.agent_focus_target_id[:8]}... '
					f'after {elapsed * 1000:.0f}ms'
				)
				return True

			manager.logger.error(
				f'[SessionManager] ❌ Recovery completed but focus still invalid after {elapsed * 1000:.0f}ms'
			)
			return await recover_focus_on_demand(manager, timeout=timeout)
		except TimeoutError:
			manager.logger.error(
				f'[SessionManager] ❌ Recovery timed out after {timeout}s '
				f'(was: {stale_target_id[:8] if stale_target_id else "None"}..., '
				f'now: {manager.browser_session.agent_focus_target_id[:8] if manager.browser_session.agent_focus_target_id else "None"})'
			)
			return False

	manager.logger.error('[SessionManager] ❌ Recovery event not initialized')
	return False


async def recover_focus_on_demand(manager: 'SessionManager', timeout: float = 3.0) -> bool:
	"""Recover focus only when a caller explicitly needs a usable page."""
	async with manager._recovery_lock:
		focus_id = manager.browser_session.agent_focus_target_id
		if focus_id:
			cdp_session = manager._get_session_for_target(focus_id)
			if cdp_session and await manager.validate_session(focus_id):
				return True

		if manager.browser_session._cdp_client_root is None:
			manager.logger.debug('[SessionManager] Cannot recover focus on demand - browser is shutting down')
			return False

		page_targets = manager.get_owned_page_targets()
		preferred_targets = [target for target in page_targets if not is_new_tab_page(target.url)]
		candidate_targets = preferred_targets or page_targets

		for target in reversed(candidate_targets):
			target_id = target.target_id
			cdp_session = manager._get_session_for_target(target_id)
			if not cdp_session:
				continue
			if not await manager.validate_session(target_id):
				continue

			manager.browser_session.agent_focus_target_id = target_id
			target_url = target.url or 'about:blank'
			from agentyc.browser.events import AgentFocusChangedEvent

			manager.browser_session.event_bus.dispatch(AgentFocusChangedEvent(target_id=target_id, url=target_url))
			return True

		manager.logger.info('[SessionManager] No live tabs remain, creating a new tab on demand')
		new_target_id = await manager.browser_session._cdp_create_new_page(
			'about:blank',
			background=manager.browser_session.browser_profile.shared_browser_focus_policy == 'preserve',
		)

		from agentyc.browser.events import AgentFocusChangedEvent, TabCreatedEvent

		manager.browser_session.event_bus.dispatch(TabCreatedEvent(url='about:blank', target_id=new_target_id))

		loop = asyncio.get_event_loop()
		deadline = loop.time() + max(timeout, 0.1)
		while loop.time() < deadline:
			await asyncio.sleep(0.1)
			cdp_session = manager._get_session_for_target(new_target_id)
			if not cdp_session:
				continue
			if not await manager.validate_session(new_target_id):
				continue
			manager.browser_session.agent_focus_target_id = new_target_id
			manager.browser_session.event_bus.dispatch(AgentFocusChangedEvent(target_id=new_target_id, url='about:blank'))
			return True

		manager.logger.error(f'[SessionManager] ❌ Failed to establish a session for on-demand tab {new_target_id[:8]}...')
		return False


async def recover_agent_focus(manager: 'SessionManager', crashed_target_id: TargetID) -> None:
	"""Auto-recover agent focus when the focused target crashes or detaches."""
	try:
		async with manager._recovery_lock:
			if manager._recovery_in_progress:
				manager.logger.debug('[SessionManager] Recovery already in progress, waiting for it to complete')
				if manager._recovery_complete_event:
					try:
						await asyncio.wait_for(manager._recovery_complete_event.wait(), timeout=5.0)
					except TimeoutError:
						manager.logger.error('[SessionManager] Timed out waiting for ongoing recovery')
				return

			manager._recovery_in_progress = True
			manager._recovery_complete_event = asyncio.Event()

			if manager.browser_session._cdp_client_root is None:
				manager.logger.debug('[SessionManager] Skipping focus recovery - browser shutting down (no CDP client)')
				return

			if (
				manager.browser_session.agent_focus_target_id
				and manager.browser_session.agent_focus_target_id != crashed_target_id
			):
				manager.logger.debug(
					f'[SessionManager] Agent focus already recovered by concurrent operation '
					f'(now: {manager.browser_session.agent_focus_target_id[:8]}...), skipping recovery'
				)
				return

			current_focus_desc = (
				f'{manager.browser_session.agent_focus_target_id[:8]}...'
				if manager.browser_session.agent_focus_target_id
				else 'None (already cleared)'
			)
			manager.logger.warning(
				f'[SessionManager] Agent focus target {crashed_target_id[:8]}... detached! '
				f'Current focus: {current_focus_desc}. Auto-recovering by switching to another target...'
			)

		page_targets = manager.get_owned_page_targets()
		new_target_id = None
		is_existing_tab = False

		if page_targets:
			preferred_targets = [target for target in page_targets if not is_new_tab_page(target.url)]
			candidate_targets = preferred_targets or page_targets
			new_target_id = candidate_targets[-1].target_id
			is_existing_tab = True
			manager.logger.info(f'[SessionManager] Switching agent_focus to existing tab {new_target_id[:8]}...')
		else:
			manager.logger.info('[SessionManager] No tabs remain after detach; leaving focus empty until a future action needs a page')
			return

		new_session = None
		for _ in range(20):
			await asyncio.sleep(0.1)
			new_session = manager._get_session_for_target(new_target_id)
			if new_session:
				break

		if new_session:
			manager.browser_session.agent_focus_target_id = new_target_id
			manager.logger.info(f'[SessionManager] ✅ Agent focus recovered: {new_target_id[:8]}...')

			if is_existing_tab and manager.browser_session.browser_profile.shared_browser_focus_policy == 'activate':
				try:
					assert manager.browser_session._cdp_client_root is not None
					await manager.browser_session._cdp_client_root.send.Target.activateTarget(params={'targetId': new_target_id})
					manager.logger.debug(f'[SessionManager] Activated tab {new_target_id[:8]}... in browser UI')
				except Exception as e:
					manager.logger.debug(f'[SessionManager] Failed to activate tab visually: {e}')

			target = manager.get_target(new_target_id)
			target_url = target.url if target else 'about:blank'
			from agentyc.browser.events import AgentFocusChangedEvent

			manager.browser_session.event_bus.dispatch(AgentFocusChangedEvent(target_id=new_target_id, url=target_url))
			return

		manager.logger.error(
			f'[SessionManager] ❌ Failed to get session for {new_target_id[:8]}... after 2s, creating emergency fallback tab'
		)
		fallback_target_id = await manager.browser_session._cdp_create_new_page(
			'about:blank',
			background=manager.browser_session.browser_profile.shared_browser_focus_policy == 'preserve',
		)
		manager.logger.warning(f'[SessionManager] Created emergency fallback tab {fallback_target_id[:8]}...')

		for _ in range(20):
			await asyncio.sleep(0.1)
			fallback_session = manager._get_session_for_target(fallback_target_id)
			if fallback_session:
				manager.browser_session.agent_focus_target_id = fallback_target_id
				manager.logger.warning(f'[SessionManager] ⚠️ Agent focus set to emergency fallback: {fallback_target_id[:8]}...')

				from agentyc.browser.events import AgentFocusChangedEvent, TabCreatedEvent

				manager.browser_session.event_bus.dispatch(TabCreatedEvent(url='about:blank', target_id=fallback_target_id))
				manager.browser_session.event_bus.dispatch(
					AgentFocusChangedEvent(target_id=fallback_target_id, url='about:blank')
				)
				return

		manager.logger.critical(
			'[SessionManager] 🚨 CRITICAL: Failed to recover agent_focus even with fallback! Agent may be in broken state.'
		)
	except Exception as e:
		manager.logger.error(f'[SessionManager] ❌ Error during agent_focus recovery: {type(e).__name__}: {e}')
	finally:
		if manager._recovery_complete_event:
			manager._recovery_complete_event.set()
		manager._recovery_in_progress = False
		manager._recovery_task = None
		manager.logger.debug('[SessionManager] Recovery state reset')
