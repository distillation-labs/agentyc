import asyncio
import json
from typing import Protocol, cast

import pytest
from pytest_httpserver import HTTPServer

from agentyc.browser import BrowserProfile, BrowserSession
from agentyc.browser.events import CloseTabEvent
from agentyc.mcp.server import AgentycServer
from agentyc.tools.service import Tools


class _BrowserToolServer(Protocol):
	async def _wait_for_element(
		self,
		text: str | None = None,
		ref: str | None = None,
		appear: bool = True,
		timeout_seconds: float = 10,
	) -> str: ...

	async def _list_tabs(self) -> str: ...

	async def _navigate(self, url: str, new_tab: bool = False) -> str: ...

	async def _switch_tab(self, tab_id: str) -> str: ...

	async def _close_tab(self, tab_id: str) -> str: ...

	async def _get_browser_state(
		self,
		include_screenshot: bool = False,
		mode: str = 'auto',
		focus_ref: str | None = None,
		since_hash: str | None = None,
	) -> tuple[str, str | None]: ...


def _base_url(httpserver: HTTPServer) -> str:
	return f'http://127.0.0.1:{httpserver.port}'


@pytest.fixture
async def browser_session():
	session = BrowserSession(
		browser_profile=BrowserProfile(
			headless=True,
			user_data_dir=None,
		)
	)
	await session.start()
	yield session
	await session.stop()


@pytest.fixture
def mcp_server(browser_session: BrowserSession) -> _BrowserToolServer:
	server = AgentycServer()
	server.browser_session = browser_session
	server.tools = Tools()
	return cast(_BrowserToolServer, server)


async def test_wait_for_element_detects_visible_noninteractive_text(
	httpserver: HTTPServer, browser_session: BrowserSession, mcp_server: _BrowserToolServer
):
	httpserver.expect_request('/wait-text').respond_with_data(
		"""
		<!DOCTYPE html>
		<html>
		<head><title>Wait Text</title></head>
		<body>
			<div id="status" aria-live="polite"></div>
			<script>
				setTimeout(() => {
					document.getElementById('status').textContent = 'Toast ready';
				}, 150);
			</script>
		</body>
		</html>
		""",
		content_type='text/html',
	)

	await browser_session.navigate_to(f'{_base_url(httpserver)}/wait-text')

	result = await mcp_server._wait_for_element(text='Toast ready', timeout_seconds=2)

	assert result.startswith('Element "Toast ready" appeared after ')


async def test_switch_tab_recovers_after_closing_active_tab(
	httpserver: HTTPServer, browser_session: BrowserSession, mcp_server: _BrowserToolServer
):
	base_url = _base_url(httpserver)
	httpserver.expect_request('/main').respond_with_data(
		"""
		<!DOCTYPE html>
		<html>
		<head><title>Main Page</title></head>
		<body><h1>Main Page</h1></body>
		</html>
		""",
		content_type='text/html',
	)
	httpserver.expect_request('/detail').respond_with_data(
		"""
		<!DOCTYPE html>
		<html>
		<head><title>Detail Page</title></head>
		<body><h1>Detail Page</h1></body>
		</html>
		""",
		content_type='text/html',
	)

	await browser_session.navigate_to(f'{base_url}/main')

	initial_tabs = json.loads(await mcp_server._list_tabs())
	main_tab_id = next(tab['tab_id'] for tab in initial_tabs if tab['url'] == f'{base_url}/main')

	open_result = await mcp_server._navigate(f'{base_url}/detail', new_tab=True)
	assert open_result == f'Opened new tab with URL: {base_url}/detail'

	tabs = json.loads(await mcp_server._list_tabs())
	detail_tab_id = next(tab['tab_id'] for tab in tabs if tab['url'] == f'{base_url}/detail')

	switch_detail = await mcp_server._switch_tab(detail_tab_id)
	assert switch_detail == f'Switched to tab {detail_tab_id}: {base_url}/detail'
	detail_state_json, _ = await mcp_server._get_browser_state(mode='min')
	detail_state = json.loads(detail_state_json)
	assert 'Detail Page' in detail_state['title']

	close_result = await mcp_server._close_tab(detail_tab_id)
	assert close_result == f'Closed tab # {detail_tab_id}, now on {base_url}/main'

	switch_back = await mcp_server._switch_tab(main_tab_id)
	assert switch_back == f'Switched to tab {main_tab_id}: {base_url}/main'
	assert await browser_session.get_current_page_url() == f'{base_url}/main'


async def test_closing_last_tab_does_not_respawn_until_recovery_is_requested():
	session = BrowserSession(
		browser_profile=BrowserProfile(
			headless=True,
			keep_alive=True,
			user_data_dir=None,
		)
	)
	await session.start()
	try:
		assert session.agent_focus_target_id is not None
		initial_pages = await session._cdp_get_all_pages()
		assert len(initial_pages) == 1

		close_event = session.event_bus.dispatch(CloseTabEvent(target_id=session.agent_focus_target_id))
		await close_event
		await close_event.event_result(raise_if_any=True, raise_if_none=False)
		await asyncio.sleep(0.4)

		assert session.agent_focus_target_id is None
		pages_after_close = await session._cdp_get_all_pages()
		assert pages_after_close == []

		focus_valid = await session.session_manager.ensure_valid_focus(timeout=3.0)
		assert focus_valid is True
		assert session.agent_focus_target_id is not None

		recovered_pages = await session._cdp_get_all_pages()
		assert len(recovered_pages) == 1
		assert recovered_pages[0]['url'] == 'about:blank'
	finally:
		await session.stop()
