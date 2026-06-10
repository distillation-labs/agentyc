"""Reconnect helpers for BrowserSession."""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING

from agentyc.browser.events import (
	BrowserErrorEvent,
	BrowserReconnectedEvent,
	BrowserReconnectingEvent,
)

if TYPE_CHECKING:
	from agentyc.browser.session import BrowserSession


async def reconnect(session: BrowserSession) -> None:
	"""Re-establish the CDP WebSocket connection to an already-running browser."""
	from agentyc.browser import session_connection

	assert session.cdp_url, 'Cannot reconnect without a CDP URL'
	old_focus_target_id = session.agent_focus_target_id

	if session._cdp_client_root:
		try:
			await session._cdp_client_root.stop()
		except Exception as e:
			session.logger.debug(f'Error stopping old CDP client during reconnect: {e}')
		session._cdp_client_root = None

	if session.session_manager:
		try:
			await session.session_manager.clear()
		except Exception as e:
			session.logger.debug(f'Error clearing SessionManager during reconnect: {e}')
		session.session_manager = None

	session.agent_focus_target_id = None
	session._cdp_client_root = await session_connection._create_root_cdp_client(session)
	await session_connection._initialize_session_manager(session)

	page_targets = session.session_manager.get_all_page_targets()
	if session.is_shared_browser_runtime:
		session._assign_shared_browser_ownership(page_targets)
	else:
		for target in page_targets:
			session.session_manager.set_target_ownership(target.target_id, session.runtime_metadata, source='current_runtime')

	owned_page_targets = session.get_owned_page_targets() if session.is_shared_browser_runtime else page_targets

	restored = False
	if old_focus_target_id:
		for target in owned_page_targets:
			if target.target_id == old_focus_target_id:
				await session.get_or_create_cdp_session(old_focus_target_id, focus=True)
				await session._apply_runtime_markers_to_target(old_focus_target_id)
				await session._cdp_get_window_context(old_focus_target_id)
				restored = True
				session.logger.debug(f'🔄 Restored agent focus to previous target {old_focus_target_id[:8]}...')
				break

	if not restored:
		if owned_page_targets:
			fallback_id = owned_page_targets[0].target_id
			await session.get_or_create_cdp_session(fallback_id, focus=True)
			await session._apply_runtime_markers_to_target(fallback_id)
			await session._cdp_get_window_context(fallback_id)
			session.logger.debug(f'🔄 Agent focus set to fallback target {fallback_id[:8]}...')
		else:
			target_id = await session._cdp_create_new_page(
				'about:blank',
				background=session.browser_profile.shared_browser_focus_policy == 'preserve',
			)
			await session.get_or_create_cdp_session(target_id, focus=True)
			await session._apply_runtime_markers_to_target(target_id)
			await session._cdp_get_window_context(target_id)
			session.logger.debug(f'🔄 Created new blank page during reconnect: {target_id[:8]}...')

	await session_connection._setup_proxy_auth(session)
	_attach_ws_drop_callback(session)
	publish_browser_event(session_id=session.id, label='Browser reconnected')


async def _auto_reconnect(session: BrowserSession, max_attempts: int = 3) -> None:
	"""Attempt to reconnect with exponential backoff."""
	async with session._reconnect_lock:
		if session._reconnecting:
			return
		session._reconnecting = True
		session._reconnect_event.clear()

	start_time = time.time()
	delays = [1.0, 2.0, 4.0]
	try:
		for attempt in range(1, max_attempts + 1):
			session.event_bus.dispatch(
				BrowserReconnectingEvent(
					cdp_url=session.cdp_url or '',
					attempt=attempt,
					max_attempts=max_attempts,
				)
			)
			session.logger.warning(f'🔄 WebSocket reconnection attempt {attempt}/{max_attempts}...')
			try:
				await asyncio.wait_for(reconnect(session), timeout=15.0)
				downtime = time.time() - start_time
				session.event_bus.dispatch(
					BrowserReconnectedEvent(
						cdp_url=session.cdp_url or '',
						attempt=attempt,
						downtime_seconds=downtime,
					)
				)
				session.logger.info(f'🔄 WebSocket reconnected after {downtime:.1f}s (attempt {attempt})')
				return
			except Exception as e:
				session.logger.warning(f'🔄 Reconnection attempt {attempt} failed: {type(e).__name__}: {e}')
				if attempt < max_attempts:
					delay = delays[attempt - 1] if attempt - 1 < len(delays) else delays[-1]
					await asyncio.sleep(delay)

		session.logger.error(f'🔄 All {max_attempts} reconnection attempts failed')
		session.event_bus.dispatch(
			BrowserErrorEvent(
				error_type='ReconnectionFailed',
				message=f'Failed to reconnect after {max_attempts} attempts ({time.time() - start_time:.1f}s)',
				details={'cdp_url': session.cdp_url or '', 'max_attempts': max_attempts},
			)
		)
	finally:
		session._reconnecting = False
		session._reconnect_event.set()


def _attach_ws_drop_callback(session: BrowserSession) -> None:
	"""Attach a done callback to the CDPClient message handler task to detect WS drops."""
	if not session._cdp_client_root or not hasattr(session._cdp_client_root, '_message_handler_task'):
		return

	task = session._cdp_client_root._message_handler_task
	if task is None or task.done():
		return

	def _on_message_handler_done(fut: asyncio.Future) -> None:
		if session._intentional_stop or session._reconnecting or not session.cdp_url:
			return

		exc = fut.exception() if not fut.cancelled() else None
		session.logger.warning(
			f'🔌 CDP WebSocket message handler exited unexpectedly'
			f'{f": {type(exc).__name__}: {exc}" if exc else " (connection closed)"}'
		)
		try:
			loop = asyncio.get_running_loop()
			session._reconnect_task = loop.create_task(_auto_reconnect(session))
		except RuntimeError:
			session.logger.error('🔌 No event loop available for auto-reconnect')

	task.add_done_callback(_on_message_handler_done)


__all__ = ['_attach_ws_drop_callback', '_auto_reconnect', 'reconnect']
