"""Target/session lookup helpers for BrowserSession."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, cast

from cdp_use.cdp.target import TargetID

from agentyc.browser.session_models import CDPSession

if TYPE_CHECKING:
	from agentyc.browser.session import BrowserSession


async def get_or_create_cdp_session(session: BrowserSession, target_id: TargetID | None = None, focus: bool = True) -> CDPSession:
	"""Get CDP session for a target from the event-driven pool."""
	assert session._cdp_client_root is not None, 'Root CDP client not initialized'
	assert session.session_manager is not None, 'SessionManager not initialized'

	if target_id is None:
		focus_valid = await session.session_manager.ensure_valid_focus(timeout=5.0)
		if not focus_valid:
			raise ValueError(
				'No valid agent focus available - target may have detached and recovery failed. '
				'This indicates browser is in an unstable state.'
			)
		assert session.agent_focus_target_id is not None, 'Focus validation passed but agent_focus_target_id is None'
		target_id = session.agent_focus_target_id

	cdp_session = session.session_manager._get_session_for_target(target_id)
	if not cdp_session:
		session.logger.debug(f'[SessionManager] Waiting for target {target_id[:8]}... to attach...')
		for attempt in range(20):
			await asyncio.sleep(0.1)
			cdp_session = session.session_manager._get_session_for_target(target_id)
			if cdp_session:
				session.logger.debug(f'[SessionManager] Target appeared after {attempt * 100}ms')
				break
		if not cdp_session:
			raise ValueError(f'Target {target_id} not found - may have detached or never existed')

	is_valid = await session.session_manager.validate_session(target_id)
	if not is_valid:
		raise ValueError(f'Target {target_id} has detached - no active sessions')

	if focus and session.agent_focus_target_id != target_id:
		target = session.session_manager.get_target(target_id)
		target_type = target.target_type if target else 'unknown'
		if target_type == 'page':
			current_focus = session.agent_focus_target_id[:8] if session.agent_focus_target_id else 'None'
			session.logger.debug(f'[SessionManager] Switching focus: {current_focus}... → {target_id[:8]}...')
			session.agent_focus_target_id = target_id
		else:
			current_focus = session.agent_focus_target_id[:8] if session.agent_focus_target_id else 'None'
			session.logger.debug(
				f'[SessionManager] Ignoring focus request for {target_type} target {target_id[:8]}... '
				f'(agent_focus stays on {current_focus}...)'
			)

	if focus:
		try:
			await asyncio.wait_for(
				cdp_session.cdp_client.send.Runtime.runIfWaitingForDebugger(session_id=cdp_session.session_id), timeout=3.0
			)
		except Exception:
			pass

	return cdp_session


async def set_extra_headers(session: BrowserSession, headers: dict[str, str], target_id: TargetID | None = None) -> None:
	"""Set extra HTTP headers using CDP Network.setExtraHTTPHeaders."""
	if target_id is None:
		if not session.agent_focus_target_id:
			return
		target_id = session.agent_focus_target_id

	cdp_session = await get_or_create_cdp_session(session, target_id, focus=False)
	await cdp_session.cdp_client.send.Network.enable(session_id=cdp_session.session_id)
	await cdp_session.cdp_client.send.Network.setExtraHTTPHeaders(
		params={'headers': cast(Any, headers)}, session_id=cdp_session.session_id
	)


async def get_target_id_from_tab_id(session: BrowserSession, tab_id: str) -> TargetID:
	if not session.session_manager:
		raise RuntimeError('SessionManager not initialized')
	for full_target_id in session.session_manager.get_all_target_ids():
		if full_target_id.endswith(tab_id):
			if await session.session_manager.is_target_valid(full_target_id):
				return full_target_id
			session.logger.debug(f'Found stale target {full_target_id}, skipping')
	raise ValueError(f'No TargetID found ending in tab_id=...{tab_id}')


async def get_target_id_from_url(session: BrowserSession, url: str) -> TargetID:
	if not session.session_manager:
		raise RuntimeError('SessionManager not initialized')
	for target_id, target in session.session_manager.get_all_targets().items():
		if target.target_type in ('page', 'tab') and target.url == url:
			return target_id
	for target_id, target in session.session_manager.get_all_targets().items():
		if target.target_type in ('page', 'tab') and url in target.url:
			return target_id
	raise ValueError(f'No TargetID found for url={url}')


async def get_most_recently_opened_target_id(session: BrowserSession) -> TargetID:
	page_targets = session.session_manager.get_all_page_targets()
	if not page_targets:
		raise RuntimeError('No page targets available')
	return page_targets[-1].target_id
