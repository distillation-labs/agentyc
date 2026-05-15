"""Target/session, CDP utility, storage, frame, and screenshot helpers for BrowserSession."""

from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from cdp_use.cdp.network import Cookie
from cdp_use.cdp.page import CaptureScreenshotParameters
from cdp_use.cdp.target import TargetID

from agentyc.browser.session_models import CDPSession
from agentyc.dom.views import EnhancedDOMTreeNode, TargetInfo
from agentyc.utils import is_new_tab_page

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


async def _cdp_get_all_pages(
	session: BrowserSession,
	include_http: bool = True,
	include_about: bool = True,
	include_pages: bool = True,
	include_iframes: bool = False,
	include_workers: bool = False,
	include_chrome: bool = False,
	include_chrome_extensions: bool = False,
	include_chrome_error: bool = False,
) -> list[TargetInfo]:
	if not session.session_manager:
		return []
	result = []
	for target_id, target in session.session_manager.get_all_targets().items():
		target_info: TargetInfo = {
			'targetId': target.target_id,
			'type': target.target_type,
			'title': target.title,
			'url': target.url,
			'attached': True,
			'canAccessOpener': False,
		}
		if _is_valid_target(
			target_info,
			include_http=include_http,
			include_about=include_about,
			include_pages=include_pages,
			include_iframes=include_iframes,
			include_workers=include_workers,
			include_chrome=include_chrome,
			include_chrome_extensions=include_chrome_extensions,
			include_chrome_error=include_chrome_error,
		):
			result.append(target_info)
	return result


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


async def _cdp_grant_permissions(session: BrowserSession, permissions: list[str], origin: str | None = None) -> None:
	params: dict[str, Any] = {'permissions': permissions}
	if origin:
		params['origin'] = origin
	await get_or_create_cdp_session(session)
	raise NotImplementedError('Not implemented yet')


async def _cdp_set_geolocation(session: BrowserSession, latitude: float, longitude: float, accuracy: float = 100) -> None:
	await session.cdp_client.send.Emulation.setGeolocationOverride(
		params={'latitude': latitude, 'longitude': longitude, 'accuracy': accuracy}
	)


async def _cdp_clear_geolocation(session: BrowserSession) -> None:
	await session.cdp_client.send.Emulation.clearGeolocationOverride()


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


async def _cdp_navigate(session: BrowserSession, url: str, target_id: TargetID | None = None) -> None:
	assert session._cdp_client_root is not None, 'CDP client not initialized - browser may not be connected yet'
	assert session.agent_focus_target_id is not None, 'Agent focus not initialized - browser may not be connected yet'
	target_id_to_use = target_id or session.agent_focus_target_id
	cdp_session = await get_or_create_cdp_session(session, target_id_to_use, focus=True)
	await cdp_session.cdp_client.send.Page.navigate(params={'url': url}, session_id=cdp_session.session_id)


def _is_valid_target(
	target_info: TargetInfo,
	include_http: bool = True,
	include_chrome: bool = False,
	include_chrome_extensions: bool = False,
	include_chrome_error: bool = False,
	include_about: bool = True,
	include_iframes: bool = True,
	include_pages: bool = True,
	include_workers: bool = False,
) -> bool:
	target_type = target_info.get('type', '')
	url = target_info.get('url', '')
	url_allowed, type_allowed = False, False
	if is_new_tab_page(url):
		url_allowed = True
	if url.startswith('chrome-error://') and include_chrome_error:
		url_allowed = True
	if url.startswith('chrome://') and include_chrome:
		url_allowed = True
	if url.startswith('chrome-extension://') and include_chrome_extensions:
		url_allowed = True
	if url == 'about:blank' and include_about:
		url_allowed = True
	if (url.startswith('http://') or url.startswith('https://')) and include_http:
		url_allowed = True
	if target_type in ('service_worker', 'shared_worker', 'worker') and include_workers:
		type_allowed = True
	if target_type in ('page', 'tab') and include_pages:
		type_allowed = True
	if target_type in ('iframe', 'webview') and include_iframes:
		type_allowed = True
		if not url:
			url_allowed = True
	return url_allowed and type_allowed


async def get_all_frames(session: BrowserSession) -> tuple[dict[str, dict], dict[str, str]]:
	all_frames: dict[str, dict] = {}
	target_sessions: dict[str, str] = {}
	include_cross_origin = session.browser_profile.cross_origin_iframes
	targets = await _cdp_get_all_pages(
		session,
		include_http=True,
		include_about=True,
		include_pages=True,
		include_iframes=include_cross_origin,
		include_workers=False,
		include_chrome=False,
		include_chrome_extensions=False,
		include_chrome_error=include_cross_origin,
	)
	for target in targets:
		target_id = target['targetId']
		if not include_cross_origin and target.get('type') == 'iframe':
			continue
		if not include_cross_origin:
			if session.agent_focus_target_id and target_id != session.agent_focus_target_id:
				continue
			try:
				cdp_session = await get_or_create_cdp_session(session, session.agent_focus_target_id, focus=False)
			except ValueError:
				continue
		else:
			try:
				cdp_session = await get_or_create_cdp_session(session, target_id, focus=False)
			except ValueError:
				continue

		target_sessions[target_id] = cdp_session.session_id
		try:
			frame_tree_result = await cdp_session.cdp_client.send.Page.getFrameTree(session_id=cdp_session.session_id)

			def process_frame_tree(node: Any, parent_frame_id: str | None = None) -> None:
				frame = node.get('frame', {})
				current_frame_id = frame.get('id')
				if not current_frame_id:
					return
				actual_parent_id = frame.get('parentId') or parent_frame_id
				frame_info = {
					**frame,
					'frameTargetId': target_id,
					'parentFrameId': actual_parent_id,
					'childFrameIds': [],
					'isCrossOrigin': False,
					'isValidTarget': _is_valid_target(
						target,
						include_http=True,
						include_about=True,
						include_pages=True,
						include_iframes=True,
						include_workers=False,
						include_chrome=False,
						include_chrome_extensions=False,
						include_chrome_error=False,
					),
				}
				cross_origin_type = frame.get('crossOriginIsolatedContextType')
				if cross_origin_type and cross_origin_type != 'NotIsolated':
					frame_info['isCrossOrigin'] = True
				if target.get('type') == 'iframe':
					frame_info['isCrossOrigin'] = True
				if not include_cross_origin and frame_info.get('isCrossOrigin'):
					return
				child_frames = node.get('childFrames', [])
				for child in child_frames:
					child_frame = child.get('frame', {})
					child_frame_id = child_frame.get('id')
					if child_frame_id:
						frame_info['childFrameIds'].append(child_frame_id)
				if current_frame_id in all_frames:
					existing = all_frames[current_frame_id]
					if target.get('type') == 'iframe':
						existing['frameTargetId'] = target_id
						existing['isCrossOrigin'] = True
				else:
					all_frames[current_frame_id] = frame_info
				if include_cross_origin or not frame_info.get('isCrossOrigin'):
					for child in child_frames:
						process_frame_tree(child, current_frame_id)

			process_frame_tree(frame_tree_result.get('frameTree', {}))
		except Exception as e:
			session.logger.debug(f'Failed to get frame tree for target {target_id}: {e}')

	if include_cross_origin:
		await _populate_frame_metadata(session, all_frames, target_sessions)
	return all_frames, target_sessions


async def _populate_frame_metadata(session: BrowserSession, all_frames: dict[str, dict], target_sessions: dict[str, str]) -> None:
	for frame_id_iter, frame_info in all_frames.items():
		parent_frame_id = frame_info.get('parentFrameId')
		if parent_frame_id and parent_frame_id in all_frames:
			parent_frame_info = all_frames[parent_frame_id]
			parent_target_id = parent_frame_info.get('frameTargetId')
			frame_info['parentTargetId'] = parent_target_id
			if parent_target_id in target_sessions:
				assert parent_target_id is not None
				parent_session_id = target_sessions[parent_target_id]
				try:
					await session.cdp_client.send.DOM.enable(session_id=parent_session_id)
					frame_owner = await session.cdp_client.send.DOM.getFrameOwner(
						params={'frameId': frame_id_iter}, session_id=parent_session_id
					)
					if frame_owner:
						frame_info['backendNodeId'] = frame_owner.get('backendNodeId')
						frame_info['nodeId'] = frame_owner.get('nodeId')
				except Exception:
					pass


async def find_frame_target(
	session: BrowserSession, frame_id: str, all_frames: dict[str, dict] | None = None
) -> dict[str, Any] | None:
	if all_frames is None:
		all_frames, _ = await get_all_frames(session)
	return all_frames.get(frame_id)


async def cdp_client_for_target(session: BrowserSession, target_id: TargetID) -> CDPSession:
	return await get_or_create_cdp_session(session, target_id, focus=False)


async def cdp_client_for_frame(session: BrowserSession, frame_id: str) -> CDPSession:
	if not session.browser_profile.cross_origin_iframes:
		return await get_or_create_cdp_session(session)
	all_frames, target_sessions = await get_all_frames(session)
	frame_info = await find_frame_target(session, frame_id, all_frames)
	if frame_info:
		target_id = frame_info.get('frameTargetId')
		if target_id in target_sessions:
			assert target_id is not None
			return await get_or_create_cdp_session(session, target_id, focus=False)
	raise ValueError(f"Frame with ID '{frame_id}' not found in any target")


async def cdp_client_for_node(session: BrowserSession, node: EnhancedDOMTreeNode) -> CDPSession:
	if node.session_id and session.session_manager:
		try:
			cdp_session = session.session_manager.get_session(node.session_id)
			if cdp_session:
				target = session.session_manager.get_target(cdp_session.target_id)
				session.logger.debug(f'✅ Using session from node.session_id for node {node.backend_node_id}: {target.url}')
				return cdp_session
		except Exception as e:
			session.logger.debug(f'Failed to get session by session_id {node.session_id}: {e}')

	if node.frame_id:
		try:
			cdp_session = await cdp_client_for_frame(session, node.frame_id)
			target = session.session_manager.get_target(cdp_session.target_id)
			session.logger.debug(f'✅ Using session from node.frame_id for node {node.backend_node_id}: {target.url}')
			return cdp_session
		except Exception as e:
			session.logger.debug(f'Failed to get session for frame {node.frame_id}: {e}')

	if node.target_id:
		try:
			cdp_session = await get_or_create_cdp_session(session, target_id=node.target_id, focus=False)
			target = session.session_manager.get_target(cdp_session.target_id)
			session.logger.debug(f'✅ Using session from node.target_id for node {node.backend_node_id}: {target.url}')
			return cdp_session
		except Exception as e:
			session.logger.debug(f'Failed to get session for target {node.target_id}: {e}')

	if session.agent_focus_target_id:
		target = session.session_manager.get_target(session.agent_focus_target_id)
		try:
			cdp_session = await get_or_create_cdp_session(session, session.agent_focus_target_id, focus=False)
			if target:
				session.logger.warning(
					f'⚠️ Node {node.backend_node_id} has no session/frame/target info. Using agent_focus session: {target.url}'
				)
			return cdp_session
		except ValueError:
			pass

	session.logger.error(f'❌ No session info for node {node.backend_node_id} and no agent_focus available. Using main session.')
	return await get_or_create_cdp_session(session)


async def take_screenshot(
	session: BrowserSession,
	path: str | None = None,
	full_page: bool = False,
	format: str = 'png',
	quality: int | None = None,
	clip: dict[str, Any] | None = None,
) -> bytes:
	cdp_session = await get_or_create_cdp_session(session)
	await session._set_collaboration_overlay_visibility(False, target_id=cdp_session.target_id)
	params: CaptureScreenshotParameters = {'format': format, 'captureBeyondViewport': full_page}
	if quality is not None and format == 'jpeg':
		params['quality'] = quality
	if clip:
		params['clip'] = {
			'x': clip['x'],
			'y': clip['y'],
			'width': clip['width'],
			'height': clip['height'],
			'scale': 1,
		}
	params = CaptureScreenshotParameters(**params)
	try:
		result = await cdp_session.cdp_client.send.Page.captureScreenshot(params=params, session_id=cdp_session.session_id)
	finally:
		await session._set_collaboration_overlay_visibility(True, target_id=cdp_session.target_id)
	if not result or 'data' not in result:
		raise Exception('Screenshot failed - no data returned')
	screenshot_data = base64.b64decode(result['data'])
	if path:
		Path(path).write_bytes(screenshot_data)
	return screenshot_data
