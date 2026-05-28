"""Target-attachment lifecycle helpers for :mod:`agentyc.browser.session_manager`."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, cast

from cdp_use.cdp.target import AttachedToTargetEvent, DetachedFromTargetEvent, SessionID

from agentyc.browser.session_models import CDPSession, Target
from agentyc.utils import create_task_with_error_handling

if TYPE_CHECKING:
	from agentyc.browser.session_manager import SessionManager


async def start_monitoring(manager: SessionManager) -> None:
	"""Start monitoring Target attach/detach events."""
	if not manager.browser_session._cdp_client_root:
		raise RuntimeError('CDP client not initialized')

	cdp_client = manager.browser_session._cdp_client_root
	await cdp_client.send.Target.setDiscoverTargets(params={'discover': True, 'filter': [{'type': 'page'}, {'type': 'iframe'}]})

	def on_attached(event: AttachedToTargetEvent, session_id: SessionID | None = None):
		create_task_with_error_handling(
			handle_target_attached(manager, event),
			name='handle_target_attached',
			logger_instance=manager.logger,
			suppress_exceptions=True,
		)

	def on_detached(event: DetachedFromTargetEvent, session_id: SessionID | None = None):
		create_task_with_error_handling(
			handle_target_detached(manager, event),
			name='handle_target_detached',
			logger_instance=manager.logger,
			suppress_exceptions=True,
		)

	def on_target_info_changed(event, session_id: SessionID | None = None):
		create_task_with_error_handling(
			handle_target_info_changed(manager, event),
			name='handle_target_info_changed',
			logger_instance=manager.logger,
			suppress_exceptions=True,
		)

	cdp_client.register.Target.attachedToTarget(on_attached)
	cdp_client.register.Target.detachedFromTarget(on_detached)
	cdp_client.register.Target.targetInfoChanged(on_target_info_changed)

	manager.logger.debug('[SessionManager] Event monitoring started')
	await manager._initialize_existing_targets()


async def handle_target_attached(manager: SessionManager, event: AttachedToTargetEvent) -> None:
	"""Handle Target.attachedToTarget event."""
	target_id = event['targetInfo']['targetId']
	session_id = event['sessionId']
	target_type = event['targetInfo']['type']
	target_info = event['targetInfo']
	waiting_for_debugger = event.get('waitingForDebugger', False)

	manager.logger.debug(
		f'[SessionManager] Target attached: {target_id[:8]}... (session={session_id[:8]}..., '
		f'type={target_type}, waitingForDebugger={waiting_for_debugger})'
	)

	if manager.browser_session._cdp_client_root is None:
		manager.logger.debug(
			f'[SessionManager] Skipping target attach for {target_id[:8]}... - browser shutting down (no CDP client)'
		)
		return

	try:
		await manager.browser_session._cdp_client_root.send.Target.setAutoAttach(
			params={'autoAttach': True, 'waitForDebuggerOnStart': False, 'flatten': True},
			session_id=session_id,
		)
	except Exception as e:
		error_str = str(e)
		if '-32001' not in error_str and 'Session with given id not found' not in error_str:
			manager.logger.debug(f'[SessionManager] Auto-attach failed for {target_type}: {e}')

	async with manager._lock:
		if target_id not in manager._target_sessions:
			manager._target_sessions[target_id] = []
		if session_id not in manager._target_sessions[target_id]:
			manager._target_sessions[target_id].append(session_id)
		manager._session_to_target[session_id] = target_id

		if target_id not in manager._targets:
			target = Target(
				target_id=target_id,
				target_type=target_type,
				url=target_info.get('url', 'about:blank'),
				title='Unknown title',
			)
			manager._apply_target_info(target, cast(Mapping[str, Any], target_info))
			manager._targets[target_id] = target
			manager.logger.debug(f'[SessionManager] Created target {target_id[:8]}... (type={target_type})')
		else:
			existing_target = manager._targets[target_id]
			manager._apply_target_info(existing_target, cast(Mapping[str, Any], target_info))

	assert manager.browser_session._cdp_client_root is not None, 'Root CDP client required'
	cdp_session = CDPSession(
		cdp_client=manager.browser_session._cdp_client_root,
		target_id=target_id,
		session_id=session_id,
	)
	manager._sessions[session_id] = cdp_session

	try:
		await manager.browser_session.configure_attached_network_session(cdp_session)
	except Exception as e:
		manager.logger.debug(f'[SessionManager] Network attach configuration failed: {type(e).__name__}: {e}')

	manager.logger.debug(
		f'[SessionManager] Created session {session_id[:8]}... for target {target_id[:8]}... '
		f'(total sessions: {len(manager._sessions)})'
	)

	if target_type in ('page', 'tab'):
		await manager._enable_page_monitoring(cdp_session)

	if waiting_for_debugger:
		try:
			assert manager.browser_session._cdp_client_root is not None
			await manager.browser_session._cdp_client_root.send.Runtime.runIfWaitingForDebugger(session_id=session_id)
		except Exception as e:
			manager.logger.warning(f'[SessionManager] Failed to resume execution: {e}')


async def handle_target_info_changed(manager: SessionManager, event: dict) -> None:
	"""Handle Target.targetInfoChanged events."""
	target_info = event.get('targetInfo', {})
	target_id = target_info.get('targetId')
	if not target_id:
		return

	async with manager._lock:
		if target_id in manager._targets:
			target = manager._targets[target_id]
			manager._apply_target_info(target, cast(Mapping[str, Any], target_info))


async def handle_target_detached(manager: SessionManager, event: DetachedFromTargetEvent) -> None:
	"""Handle Target.detachedFromTarget events."""
	session_id = event['sessionId']
	target_id = event.get('targetId')

	if not target_id:
		async with manager._lock:
			target_id = manager._session_to_target.get(session_id)

	if not target_id:
		manager.logger.warning(f'[SessionManager] Session detached but target unknown (session={session_id[:8]}...)')
		return

	agent_focus_lost = False
	target_fully_removed = False
	target_type = None

	async with manager._lock:
		if target_id in manager._target_sessions:
			if session_id in manager._target_sessions[target_id]:
				manager._target_sessions[target_id].remove(session_id)

			remaining_sessions = len(manager._target_sessions[target_id])
			manager.logger.debug(
				f'[SessionManager] Session detached: target={target_id[:8]}... '
				f'session={session_id[:8]}... (remaining={remaining_sessions})'
			)

			if remaining_sessions == 0:
				manager.logger.debug(f'[SessionManager] No sessions remain for target {target_id[:8]}..., removing target')
				target_fully_removed = True
				agent_focus_lost = manager.browser_session.agent_focus_target_id == target_id

				if agent_focus_lost:
					manager.logger.debug(
						f'[SessionManager] Clearing stale agent_focus_target_id {target_id[:8]}... '
						f'to prevent operations on detached target'
					)
					manager.browser_session.agent_focus_target_id = None

				target = manager._targets.get(target_id)
				target_type = target.target_type if target else None
				if target_id in manager._targets:
					manager._targets.pop(target_id)
					manager.logger.debug(
						f'[SessionManager] Removed target {target_id[:8]}... (remaining targets: {len(manager._targets)})'
					)

				del manager._target_sessions[target_id]
		else:
			manager.logger.debug(
				f'[SessionManager] Session detached from untracked target: target={target_id[:8]}... '
				f'session={session_id[:8]}... (target was already removed or attach event was missed)'
			)

		if session_id in manager._sessions:
			manager._sessions.pop(session_id)
			manager.logger.debug(
				f'[SessionManager] Removed session {session_id[:8]}... (remaining sessions: {len(manager._sessions)})'
			)
		if session_id in manager._session_to_target:
			del manager._session_to_target[session_id]

	if target_fully_removed:
		if target_type in ('page', 'tab'):
			from agentyc.browser.events import TabClosedEvent

			manager.browser_session.event_bus.dispatch(TabClosedEvent(target_id=target_id))
			manager.logger.debug(f'[SessionManager] Dispatched TabClosedEvent for page target {target_id[:8]}...')
		elif target_type:
			manager.logger.debug(
				f'[SessionManager] Target {target_id[:8]}... fully removed (type={target_type}) - not dispatching TabClosedEvent'
			)

	if agent_focus_lost and not manager._recovery_in_progress:
		manager._recovery_task = create_task_with_error_handling(
			manager._recover_agent_focus(target_id),
			name='recover_agent_focus',
			logger_instance=manager.logger,
			suppress_exceptions=False,
		)
