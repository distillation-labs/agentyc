"""Shared-browser collaboration and windowing helpers for BrowserSession."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from cdp_use.cdp.browser import Bounds
from cdp_use.cdp.target import TargetID
from cdp_use.cdp.target.commands import CreateTargetParameters

from agentyc.browser.collaboration import (
	apply_title_prefix,
	build_runtime_marker_script,
	build_runtime_metadata_probe_script,
)
from agentyc.browser.page import Page
from agentyc.browser.session_models import BrowserWindowBounds, RuntimeOwnershipMetadata, Target, TargetOwnershipMetadata
from agentyc.browser.views import TabInfo
from agentyc.browser.windowing import build_create_target_params, normalize_window_bounds, window_context_from_cdp
from agentyc.dom.views import TargetInfo
from agentyc.utils import _log_pretty_url, is_new_tab_page

if TYPE_CHECKING:
	from agentyc.browser.session import BrowserSession


def _tab_display_title(session: BrowserSession, target: Target) -> str:
	display_title = target.display_title or target.title
	if (
		target.ownership
		and target.ownership.runtime
		and target.ownership.runtime.runtime_id == session.runtime_metadata.runtime_id
	):
		return apply_title_prefix(display_title, session.runtime_metadata)
	return display_title


def _assign_shared_browser_ownership(session: BrowserSession, page_targets: list[Target]) -> None:
	if not session.session_manager:
		return
	for target in page_targets:
		ownership = target.ownership
		if ownership and ownership.runtime and ownership.runtime.runtime_id == session.runtime_metadata.runtime_id:
			session.session_manager.set_target_ownership(target.target_id, session.runtime_metadata, source='current_runtime')
			continue
		if ownership and ownership.runtime is not None:
			continue
		session.session_manager.set_target_human_ownership(target.target_id)


async def _apply_runtime_markers_to_target(
	session: BrowserSession,
	target_id: TargetID,
	*,
	include_title_prefix: bool = True,
) -> None:
	if not session.session_manager:
		return
	target = session.session_manager.get_target(target_id)
	if not target or target.target_type not in ('page', 'tab'):
		return
	if (
		target.ownership
		and target.ownership.runtime
		and target.ownership.runtime.runtime_id != session.runtime_metadata.runtime_id
	):
		return
	if target.ownership and target.ownership.owner_kind == 'human':
		return
	try:
		cdp_session = await session.get_or_create_cdp_session(target_id, focus=False)
		script = build_runtime_marker_script(
			runtime=session.runtime_metadata,
			target_id=target_id,
			include_title_prefix=include_title_prefix,
		)
		await cdp_session.cdp_client.send.Runtime.evaluate(
			params={'expression': script, 'returnByValue': True},
			session_id=cdp_session.session_id,
		)
		session.session_manager.set_target_ownership(target_id, session.runtime_metadata, source='current_runtime')
		managed_target = session.session_manager.get_target(target_id)
		if managed_target and managed_target.ownership:
			managed_target.ownership.title_prefix_applied = include_title_prefix
	except Exception as exc:
		session.logger.debug(f'Failed to apply runtime markers to target {target_id[-8:]}: {exc}')


async def get_target_runtime_metadata(
	session: BrowserSession,
	target_id: TargetID | None = None,
) -> dict[str, Any] | None:
	resolved_target_id = target_id or session.agent_focus_target_id
	if not resolved_target_id:
		return None
	target = session.session_manager.get_target(resolved_target_id) if session.session_manager else None
	if target and target.ownership and target.ownership.source != 'detected_runtime':
		# For ownership set directly (own tabs), the cache is authoritative.
		# For 'detected_runtime' ownership (title-based detection), skip the cache and probe JS —
		# title detection only recovers the short label, not the full runtime_id.
		return {
			'ownership': target.ownership.model_dump(mode='json'),
			'runtime': target.ownership.runtime.model_dump(mode='json') if target.ownership.runtime else None,
			'targetId': str(resolved_target_id),
			'version': target.ownership.marker_version,
		}
	try:
		cdp_session = await session.get_or_create_cdp_session(resolved_target_id, focus=False)
		result = await cdp_session.cdp_client.send.Runtime.evaluate(
			params={'expression': build_runtime_metadata_probe_script(), 'returnByValue': True},
			session_id=cdp_session.session_id,
		)
		return result.get('result', {}).get('value')
	except Exception:
		return None


async def _detect_target_ownership(session: BrowserSession, target: Target) -> TargetOwnershipMetadata | None:
	metadata = await get_target_runtime_metadata(session, target.target_id)
	ownership_payload = metadata.get('ownership') if isinstance(metadata, dict) else None
	runtime_payload = metadata.get('runtime') if isinstance(metadata, dict) else None
	if isinstance(ownership_payload, dict):
		try:
			ownership = TargetOwnershipMetadata.model_validate(ownership_payload)
			if session.session_manager:
				session.session_manager.set_target_ownership(target.target_id, ownership)
			return ownership
		except Exception:
			pass
	if isinstance(runtime_payload, dict):
		try:
			runtime = RuntimeOwnershipMetadata.model_validate(runtime_payload)
			ownership = TargetOwnershipMetadata.for_runtime(
				target_id=target.target_id,
				runtime=runtime,
				current_runtime_id=session.runtime_metadata.runtime_id,
				source='detected_runtime',
			)
			if session.session_manager:
				session.session_manager.set_target_ownership(target.target_id, ownership)
			return ownership
		except Exception:
			pass
	return None


async def get_tabs(session: BrowserSession) -> list[TabInfo]:
	"""Get information about all open tabs using cached target data."""
	tabs: list[TabInfo] = []

	if not session.session_manager:
		return tabs

	for i, target in enumerate(session.session_manager.get_all_page_targets()):
		target_id = target.target_id
		url = target.url
		title = target.title
		ownership = target.ownership

		try:
			if is_new_tab_page(url) or url.startswith('chrome://'):
				if is_new_tab_page(url):
					title = ''
				elif not title:
					title = url

			if (not title or title == '') and (url.endswith('.pdf') or 'pdf' in url):
				try:
					from urllib.parse import urlparse

					filename = urlparse(url).path.split('/')[-1]
					if filename:
						title = filename
				except Exception:
					pass

		except Exception as e:
			session.logger.debug(f'⚠️ Failed to get target info for tab #{i}: {_log_pretty_url(url)} - {type(e).__name__}')
			if is_new_tab_page(url):
				title = ''
			elif url.startswith('chrome://'):
				title = url
			else:
				title = ''

		if (
			(ownership is None or ownership.source == 'detected_runtime')
			and not is_new_tab_page(url)
			and not url.startswith('chrome://')
		):
			detected_ownership = await _detect_target_ownership(session, target)
			if detected_ownership is not None:
				ownership = detected_ownership
				target = session.session_manager.get_target(target_id) or target

		tabs.append(
			TabInfo(
				target_id=target_id,
				url=url,
				title=title,
				display_title=_tab_display_title(session, target),
				parent_target_id=None,
				ownership=ownership,
				window_bounds=target.window_bounds,
			)
		)

	return tabs


async def get_window_bounds(session: BrowserSession, target_id: TargetID | None = None) -> dict[str, Any] | None:
	resolved_target_id = target_id or session.agent_focus_target_id
	if not resolved_target_id:
		return None
	return await _cdp_get_window_context(session, resolved_target_id)


async def set_window_bounds(
	session: BrowserSession,
	bounds: BrowserWindowBounds | dict[str, Any],
	target_id: TargetID | None = None,
) -> dict[str, Any] | None:
	resolved_target_id = target_id or session.agent_focus_target_id
	if not resolved_target_id:
		return None
	return await _cdp_set_window_bounds(session, resolved_target_id, bounds)


async def create_collaborative_page(
	session: BrowserSession,
	url: str = 'about:blank',
	*,
	new_window: bool | None = None,
	window_bounds: BrowserWindowBounds | dict[str, Any] | None = None,
) -> Page:
	resolved_new_window = new_window
	if resolved_new_window is None:
		resolved_new_window = session.browser_profile.shared_browser_mode == 'window'
	resolved_bounds = window_bounds or session.browser_profile.shared_browser_window_bounds
	browser_context_id: str | None = getattr(session, '_browser_context_id', None)
	target_id = await _cdp_create_new_page(
		session,
		url=url,
		background=session.browser_profile.shared_browser_focus_policy == 'preserve',
		new_window=resolved_new_window,
		window_bounds=resolved_bounds,
		browser_context_id=browser_context_id,
	)
	await _apply_runtime_markers_to_target(session, target_id)
	await _cdp_get_window_context(session, target_id)
	return Page(session, target_id)


async def get_current_target_info(session: BrowserSession) -> TargetInfo | None:
	"""Get info about the current active target using cached session data."""
	if not session.agent_focus_target_id:
		return None

	target = session.session_manager.get_target(session.agent_focus_target_id)
	return {
		'targetId': target.target_id,
		'url': target.url,
		'title': _tab_display_title(session, target),
		'type': target.target_type,
		'attached': True,
		'canAccessOpener': False,
	}


async def get_current_page_url(session: BrowserSession) -> str:
	"""Get the URL of the current page."""
	if session.agent_focus_target_id:
		target = session.session_manager.get_target(session.agent_focus_target_id)
		return target.url
	return 'about:blank'


async def get_current_page_title(session: BrowserSession) -> str:
	"""Get the title of the current page."""
	if session.agent_focus_target_id:
		target = session.session_manager.get_target(session.agent_focus_target_id)
		return _tab_display_title(session, target)
	return 'Unknown page title'


async def new_page(session: BrowserSession, url: str | None = None) -> Page:
	"""Create a new page (tab)."""
	params: CreateTargetParameters = {'url': url or 'about:blank'}
	result = await session.cdp_client.send.Target.createTarget(params)
	return Page(session, result['targetId'])


async def get_current_page(session: BrowserSession) -> Page | None:
	"""Get the current page wrapper for the focused target."""
	target_info = await get_current_target_info(session)
	if not target_info:
		return None
	return Page(session, target_info['targetId'])


async def must_get_current_page(session: BrowserSession) -> Page:
	"""Get the current page wrapper for the focused target."""
	page = await get_current_page(session)
	if not page:
		raise RuntimeError('No current target found')
	return page


async def get_pages(session: BrowserSession) -> list[Page]:
	"""Get all available pages using SessionManager (source of truth)."""
	page_targets = session.session_manager.get_all_page_targets() if session.session_manager else []
	return [Page(session, target.target_id) for target in page_targets]


async def close_page(session: BrowserSession, page: Page | str) -> None:
	"""Close a page by Page object or target ID."""
	if isinstance(page, Page):
		target_id = page._target_id
	else:
		target_id = str(page)
	await session.cdp_client.send.Target.closeTarget({'targetId': target_id})


async def _cdp_close_page(session: BrowserSession, target_id: TargetID) -> None:
	"""Close a page/tab using CDP Target.closeTarget."""
	await session.cdp_client.send.Target.closeTarget(params={'targetId': target_id})


async def _cdp_create_new_page(
	session: BrowserSession,
	url: str = 'about:blank',
	background: bool = False,
	new_window: bool = False,
	window_bounds: BrowserWindowBounds | dict[str, Any] | None = None,
	browser_context_id: str | None = None,
) -> str:
	"""Create a new page/tab using CDP Target.createTarget. Returns target ID."""
	params = CreateTargetParameters(
		**build_create_target_params(
			url=url,
			background=background,
			new_window=new_window,
			window_bounds=normalize_window_bounds(window_bounds),
			browser_context_id=browser_context_id,
		)
	)
	if session._cdp_client_root:
		result = await session._cdp_client_root.send.Target.createTarget(params=params)
	else:
		result = await session.cdp_client.send.Target.createTarget(params=params)
	return result['targetId']


async def _cdp_get_window_context(session: BrowserSession, target_id: TargetID) -> dict[str, Any] | None:
	assert session._cdp_client_root is not None
	try:
		result = await session._cdp_client_root.send.Browser.getWindowForTarget(params={'targetId': target_id})
		window_context = window_context_from_cdp(result)
		if window_context and session.session_manager:
			session.session_manager.set_target_window_context(
				target_id,
				window_id=window_context.window_id,
				window_bounds=window_context.bounds,
			)
			return window_context.model_dump(mode='json')
	except Exception as exc:
		session.logger.debug(f'Failed to fetch window context for {target_id[-8:]}: {exc}')
	return None


async def _cdp_set_window_bounds(
	session: BrowserSession,
	target_id: TargetID,
	bounds: BrowserWindowBounds | dict[str, Any],
) -> dict[str, Any] | None:
	assert session._cdp_client_root is not None
	normalized_bounds = normalize_window_bounds(bounds)
	if normalized_bounds is None:
		return None
	window_context = await _cdp_get_window_context(session, target_id)
	if not window_context:
		return None
	window_id = window_context.get('window_id') or window_context.get('windowId')
	if window_id is None:
		return None
	params = cast(
		Any,
		{'windowId': window_id, 'bounds': Bounds(**normalized_bounds.model_dump(by_alias=True, exclude_none=True))},
	)
	await session._cdp_client_root.send.Browser.setWindowBounds(params=params)
	await _cdp_get_window_context(session, target_id)
	target = session.session_manager.get_target(target_id) if session.session_manager else None
	if target and target.window_bounds:
		return target.window_bounds.model_dump(mode='json', by_alias=True)
	return None
