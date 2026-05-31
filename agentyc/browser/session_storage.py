"""Storage and cookie helpers for BrowserSession CDP sessions."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from cdp_use.cdp.network import Cookie
from cdp_use.cdp.target import TargetID

from agentyc.browser.session_lookup import get_or_create_cdp_session

if TYPE_CHECKING:
	from agentyc.browser.session import BrowserSession


async def export_storage_state(session: BrowserSession, output_path: str | Path | None = None) -> dict[str, Any]:
	"""Export all browser cookies and storage to storage_state format."""
	cookies = await _cdp_get_cookies(session)
	storage_state = {
		'cookies': [
			{
				'name': c['name'],
				'value': c['value'],
				'domain': c['domain'],
				'path': c['path'],
				'expires': c.get('expires', -1),
				'httpOnly': c.get('httpOnly', False),
				'secure': c.get('secure', False),
				'sameSite': c.get('sameSite', 'Lax'),
			}
			for c in cookies
		],
		'origins': [],
	}
	if output_path:
		output_file = Path(output_path).expanduser().resolve()
		output_file.parent.mkdir(parents=True, exist_ok=True)
		output_file.write_text(json.dumps(storage_state, indent=2, ensure_ascii=False), encoding='utf-8')
		session.logger.info(f'💾 Exported {len(cookies)} cookies to {output_file}')
	return storage_state


async def _cdp_get_cookies(session: BrowserSession) -> list[Cookie]:
	cdp_session = await get_or_create_cdp_session(session, target_id=None)
	result = await asyncio.wait_for(
		cdp_session.cdp_client.send.Storage.getCookies(session_id=cdp_session.session_id), timeout=8.0
	)
	return result.get('cookies', [])


async def _cdp_set_cookies(session: BrowserSession, cookies: list[Cookie]) -> None:
	if not session.agent_focus_target_id or not cookies:
		return
	cdp_session = await get_or_create_cdp_session(session, target_id=None)
	await cdp_session.cdp_client.send.Storage.setCookies(
		params=cast(Any, {'cookies': cookies}),
		session_id=cdp_session.session_id,
	)


async def _cdp_clear_cookies(session: BrowserSession) -> None:
	cdp_session = await get_or_create_cdp_session(session)
	await cdp_session.cdp_client.send.Storage.clearCookies(session_id=cdp_session.session_id)


async def _cdp_add_init_script(session: BrowserSession, script: str, target_id: TargetID | None = None) -> str:
	assert session._cdp_client_root is not None
	cdp_session = await session.get_or_create_cdp_session(target_id=target_id, focus=False)
	result = await cdp_session.cdp_client.send.Page.addScriptToEvaluateOnNewDocument(
		params={'source': script, 'runImmediately': True}, session_id=cdp_session.session_id
	)
	identifier = result['identifier']
	if target_id is None:
		session._global_init_script_targets.setdefault(identifier, set())
	else:
		session._target_init_scripts.setdefault(str(target_id), set()).add(identifier)
	return identifier


async def _cdp_remove_init_script(session: BrowserSession, identifier: str, target_id: TargetID | None = None) -> None:
	cdp_session = await session.get_or_create_cdp_session(target_id=target_id, focus=False)
	await cdp_session.cdp_client.send.Page.removeScriptToEvaluateOnNewDocument(
		params={'identifier': identifier}, session_id=cdp_session.session_id
	)
	if target_id is None:
		session._global_init_script_targets.pop(identifier, None)
	else:
		target_scripts = session._target_init_scripts.get(str(target_id))
		if target_scripts:
			target_scripts.discard(identifier)
			if not target_scripts:
				session._target_init_scripts.pop(str(target_id), None)


async def _cdp_set_viewport(
	session: BrowserSession,
	width: int,
	height: int,
	device_scale_factor: float = 1.0,
	mobile: bool = False,
	target_id: str | None = None,
) -> None:
	if target_id:
		cdp_session = await get_or_create_cdp_session(session, target_id, focus=False)
	elif session.agent_focus_target_id:
		try:
			cdp_session = await get_or_create_cdp_session(session, session.agent_focus_target_id, focus=False)
		except ValueError:
			session.logger.warning('Cannot set viewport: focused target has no sessions')
			return
	else:
		session.logger.warning('Cannot set viewport: no target_id provided and agent_focus not initialized')
		return

	await cdp_session.cdp_client.send.Emulation.setDeviceMetricsOverride(
		params={'width': width, 'height': height, 'deviceScaleFactor': device_scale_factor, 'mobile': mobile},
		session_id=cdp_session.session_id,
	)


async def _cdp_get_origins(session: BrowserSession) -> list[dict[str, Any]]:
	origins = []
	cdp_session = await get_or_create_cdp_session(session, target_id=None)
	try:
		await cdp_session.cdp_client.send.DOMStorage.enable(session_id=cdp_session.session_id)
		try:
			frames_result = await cdp_session.cdp_client.send.Page.getFrameTree(session_id=cdp_session.session_id)
			unique_origins = set()

			def _extract_origins(frame_tree: Any) -> None:
				frame = frame_tree.get('frame', {})
				origin = frame.get('securityOrigin')
				if origin and origin != 'null':
					unique_origins.add(origin)
				for child in frame_tree.get('childFrames', []):
					_extract_origins(child)

			async def _get_storage_items(origin: str, is_local_storage: bool) -> list[dict[str, str]] | None:
				storage_type = 'localStorage' if is_local_storage else 'sessionStorage'
				try:
					result = await cdp_session.cdp_client.send.DOMStorage.getDOMStorageItems(
						params={'storageId': {'securityOrigin': origin, 'isLocalStorage': is_local_storage}},
						session_id=cdp_session.session_id,
					)
					items = []
					for item in result.get('entries', []):
						if len(item) == 2:
							items.append({'name': item[0], 'value': item[1]})
					return items if items else None
				except Exception as e:
					session.logger.debug(f'Failed to get {storage_type} for {origin}: {e}')
					return None

			_extract_origins(frames_result.get('frameTree', {}))
			for origin in unique_origins:
				origin_data = {'origin': origin}
				local_storage = await _get_storage_items(origin, is_local_storage=True)
				if local_storage:
					origin_data['localStorage'] = local_storage
				session_storage = await _get_storage_items(origin, is_local_storage=False)
				if session_storage:
					origin_data['sessionStorage'] = session_storage
				if 'localStorage' in origin_data or 'sessionStorage' in origin_data:
					origins.append(origin_data)
		finally:
			await cdp_session.cdp_client.send.DOMStorage.disable(session_id=cdp_session.session_id)
	except Exception as e:
		session.logger.warning(f'Failed to get origins: {e}')
	return origins


async def _cdp_get_storage_state(session: BrowserSession) -> dict[str, Any]:
	return {'cookies': await _cdp_get_cookies(session), 'origins': await _cdp_get_origins(session)}
