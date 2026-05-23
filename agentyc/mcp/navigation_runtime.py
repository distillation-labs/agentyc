"""Navigation-oriented MCP runtime helpers."""

from __future__ import annotations

import asyncio
import json
from typing import Any


async def _page_contains_visible_text(self, text: str) -> bool:
	if self.browser_session is None:
		return False

	cdp_session = await self.browser_session.get_or_create_cdp_session(target_id=None, focus=False)
	result = await cdp_session.cdp_client.send.Runtime.evaluate(
		params={
			'expression': f"""(function() {{
				const root = document.body || document.documentElement;
				if (!root) return false;
				const visibleText = String(root.innerText || root.textContent || '').toLowerCase();
				return visibleText.includes({json.dumps(text.lower())});
			}})()""",
			'returnByValue': True,
		},
		session_id=cdp_session.session_id,
	)
	return bool(result.get('result', {}).get('value'))


def _element_triggers_form_navigation(element: Any) -> bool:
	tag_name = str(getattr(element, 'tag_name', '') or '').lower()
	attributes = getattr(element, 'attributes', {}) or {}
	if tag_name == 'button':
		button_type = str(attributes.get('type') or 'submit').lower()
		if button_type != 'submit':
			return False
	elif tag_name == 'input':
		input_type = str(attributes.get('type') or '').lower()
		if input_type not in {'submit', 'image'}:
			return False
	else:
		return False

	parent = getattr(element, 'parent_node', None)
	while parent is not None:
		if str(getattr(parent, 'tag_name', '') or '').lower() == 'form':
			return True
		parent = getattr(parent, 'parent_node', None)
	return False


async def _wait_for_click_navigation_settle(
	self,
	*,
	pre_click_url: str,
	detect_timeout: float = 0.4,
	readiness_timeout: float = 3.0,
) -> str | None:
	if self.browser_session is None or self.browser_session.agent_focus_target_id is None:
		return None

	loop = asyncio.get_event_loop()
	deadline = loop.time() + detect_timeout
	settled_url = None
	while loop.time() < deadline:
		current_url = await self.browser_session.get_current_page_url()
		if current_url and current_url != pre_click_url:
			settled_url = current_url
			break
		await asyncio.sleep(0.05)
	if settled_url is None:
		return None

	cdp_session = await self.browser_session.get_or_create_cdp_session(
		target_id=self.browser_session.agent_focus_target_id,
		focus=False,
	)
	readiness_deadline = loop.time() + readiness_timeout
	while loop.time() < readiness_deadline:
		if await self.browser_session._navigation_ready_via_dom(
			cdp_session=cdp_session,
			url=settled_url,
			wait_until='load',
		):
			return settled_url
		await asyncio.sleep(0.05)
	raise RuntimeError(f'Navigation timed out after {readiness_timeout}s waiting for load on {settled_url}')


def _page_appears_empty(state: Any) -> bool:
	dom_state = getattr(state, 'dom_state', None)
	if dom_state is None:
		return True
	if getattr(dom_state, '_root', None) is None:
		return True
	render = getattr(dom_state, 'llm_representation', None)
	if callable(render):
		try:
			return not str(render()).strip()
		except Exception:
			return False
	return False


async def _page_is_site_unavailable(self) -> bool:
	if self.browser_session is None:
		return False
	if not hasattr(self.browser_session, 'get_or_create_cdp_session'):
		return False
	try:
		cdp_session = await self.browser_session.get_or_create_cdp_session(target_id=None, focus=False)
		result = await cdp_session.cdp_client.send.Runtime.evaluate(
			params={
				'expression': """(function() {
					const errorCode = document.querySelector('.error-code');
					return {
						locationHref: String(window.location.href || ''),
						hasMainFrameError: !!document.getElementById('main-frame-error'),
						errorCode: errorCode ? String(errorCode.textContent || '') : '',
					};
				})()""",
				'returnByValue': True,
			},
			session_id=cdp_session.session_id,
		)
	except Exception:
		return False
	payload = result.get('result', {}).get('value')
	if not isinstance(payload, dict):
		return False
	location_href = str(payload.get('locationHref') or '')
	error_code = str(payload.get('errorCode') or '')
	return (
		location_href.startswith('chrome-error://')
		or bool(payload.get('hasMainFrameError'))
		or error_code.upper().startswith('ERR_')
	)


async def _recover_click_navigation_if_unavailable(
	self,
	*,
	target_url: str,
	wait_before_retry: float = 1.0,
) -> str | None:
	if self.browser_session is None:
		return None
	if not hasattr(self.browser_session, 'get_browser_state_summary'):
		return None
	if not target_url.lower().startswith(('http://', 'https://')):
		return None

	state = await self.browser_session.get_browser_state_summary(include_screenshot=False)
	if not (_page_appears_empty(state) or await _page_is_site_unavailable(self)):
		return None

	self.browser_session.logger.warning(
		f'⚠️ Empty DOM detected after click navigation to {target_url}, waiting {wait_before_retry:.1f}s before retrying...'
	)
	await asyncio.sleep(wait_before_retry)
	state = await self.browser_session.get_browser_state_summary(include_screenshot=False)
	if not (_page_appears_empty(state) or await _page_is_site_unavailable(self)):
		return None

	self.browser_session.logger.warning(f'⚠️ Still empty after click navigation to {target_url}, retrying direct navigation...')
	action_result = await self._run_tool_action('navigate', {'url': target_url, 'new_tab': False})
	if action_result.error:
		return str(action_result.error)

	state = await self.browser_session.get_browser_state_summary(include_screenshot=False)
	if _page_appears_empty(state) or await _page_is_site_unavailable(self):
		return f'Navigation failed - site unavailable: {target_url}'
	return None
