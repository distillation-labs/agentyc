"""Target/session, CDP utility, storage, frame, and screenshot helpers for BrowserSession."""

from __future__ import annotations

import base64
from pathlib import Path
from typing import TYPE_CHECKING, Any

from cdp_use.cdp.page import CaptureScreenshotParameters
from cdp_use.cdp.target import TargetID

from agentyc.browser.session_lookup import (
	get_most_recently_opened_target_id,
	get_or_create_cdp_session,
	get_target_id_from_tab_id,
	get_target_id_from_url,
	set_extra_headers,
)
from agentyc.browser.session_models import CDPSession
from agentyc.browser.session_storage import (
	_cdp_add_init_script,
	_cdp_clear_cookies,
	_cdp_clear_geolocation,
	_cdp_get_cookies,
	_cdp_get_origins,
	_cdp_get_storage_state,
	_cdp_grant_permissions,
	_cdp_remove_init_script,
	_cdp_set_cookies,
	_cdp_set_geolocation,
	_cdp_set_viewport,
	export_storage_state,
)
from agentyc.dom.views import EnhancedDOMTreeNode, TargetInfo
from agentyc.utils import is_new_tab_page

if TYPE_CHECKING:
	from agentyc.browser.session import BrowserSession


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


async def get_all_frames(
	session: BrowserSession,
	*,
	include_backend_node_ids: bool = True,
) -> tuple[dict[str, dict], dict[str, str]]:
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

	if include_cross_origin and include_backend_node_ids:
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


async def cdp_client_for_frame(
	session: BrowserSession,
	frame_id: str,
	*,
	all_frames: dict[str, dict] | None = None,
	target_sessions: dict[str, str] | None = None,
) -> CDPSession:
	if not session.browser_profile.cross_origin_iframes:
		return await get_or_create_cdp_session(session)
	if all_frames is None or target_sessions is None:
		all_frames, target_sessions = await get_all_frames(session, include_backend_node_ids=False)
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
	result = await cdp_session.cdp_client.send.Page.captureScreenshot(params=params, session_id=cdp_session.session_id)
	if not result or 'data' not in result:
		raise Exception('Screenshot failed - no data returned')
	screenshot_data = base64.b64decode(result['data'])
	if path:
		Path(path).write_bytes(screenshot_data)
	return screenshot_data


__all__ = [
	'_cdp_add_init_script',
	'_cdp_clear_cookies',
	'_cdp_clear_geolocation',
	'_cdp_get_all_pages',
	'_cdp_get_cookies',
	'_cdp_get_origins',
	'_cdp_get_storage_state',
	'_cdp_grant_permissions',
	'_cdp_navigate',
	'_cdp_remove_init_script',
	'_cdp_set_cookies',
	'_cdp_set_geolocation',
	'_cdp_set_viewport',
	'_is_valid_target',
	'cdp_client_for_frame',
	'cdp_client_for_node',
	'cdp_client_for_target',
	'export_storage_state',
	'find_frame_target',
	'get_all_frames',
	'get_most_recently_opened_target_id',
	'get_or_create_cdp_session',
	'get_target_id_from_tab_id',
	'get_target_id_from_url',
	'set_extra_headers',
	'take_screenshot',
]
