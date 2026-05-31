"""Integration tests for the browser_wait_for_url MCP tool."""

from __future__ import annotations

import json

import pytest
from pytest_httpserver import HTTPServer

from agentyc.browser import BrowserProfile, BrowserSession
from agentyc.mcp.server import AgentycServer
from agentyc.tools.service import Tools

_ROUTE_PAGE = """
<!DOCTYPE html>
<html lang="en">
<head><title>Wait for URL</title></head>
<body>
  <main>
    <button id="route-change">Finish setup</button>
    <div id="status">Idle</div>
  </main>
  <script>
    document.getElementById('route-change').addEventListener('click', () => {
      setTimeout(() => {
        history.pushState({}, '', '/app/success?step=2');
        document.getElementById('status').textContent = location.pathname + location.search;
      }, 150);
    });
  </script>
</body>
</html>
"""


@pytest.fixture
async def browser_session():
	session = BrowserSession(browser_profile=BrowserProfile(headless=True, user_data_dir=None))
	await session.start()
	yield session
	await session.stop()


@pytest.fixture
def mcp_server(browser_session: BrowserSession):
	server = AgentycServer()
	server.browser_session = browser_session
	server.tools = Tools()
	server._update_session_activity = lambda *_args, **_kwargs: None
	return server


async def _button_ref(server: AgentycServer) -> str:
	state_json, _ = await server._get_browser_state(mode='full', include_screenshot=False)
	payload = json.loads(state_json)
	return next(element['ref'] for element in payload['interactive_elements'] if element.get('text') == 'Finish setup')


async def test_browser_wait_for_url_matches_history_api_route_change(
	httpserver: HTTPServer, browser_session: BrowserSession, mcp_server: AgentycServer
):
	httpserver.expect_request('/route-wait').respond_with_data(_ROUTE_PAGE, content_type='text/html')
	base_url = f'http://127.0.0.1:{httpserver.port}'
	nav_result = await mcp_server._navigate(f'{base_url}/route-wait')
	assert not nav_result.startswith('Error'), f'Navigation failed: {nav_result}'

	click_result = await mcp_server._click(ref=await _button_ref(mcp_server))
	assert not click_result.startswith('Error'), f'Click failed: {click_result}'

	result = await mcp_server._execute_tool(
		'browser_wait_for_url',
		{'url_substring': '/app/success', 'timeout_seconds': 5.0},
	)
	assert not str(result).startswith('Error'), f'wait_for_url failed: {result}'
	assert '/app/success?step=2' in str(result)


async def test_browser_wait_for_url_times_out_when_route_never_matches(
	httpserver: HTTPServer, browser_session: BrowserSession, mcp_server: AgentycServer
):
	httpserver.expect_request('/route-wait-timeout').respond_with_data(_ROUTE_PAGE, content_type='text/html')
	base_url = f'http://127.0.0.1:{httpserver.port}'
	nav_result = await mcp_server._navigate(f'{base_url}/route-wait-timeout')
	assert not nav_result.startswith('Error'), f'Navigation failed: {nav_result}'

	result = await mcp_server._execute_tool(
		'browser_wait_for_url',
		{'url_regex': 'no-match$', 'timeout_seconds': 0.5},
	)
	assert str(result).startswith('Error [timeout]'), result


async def test_browser_click_can_wait_for_url_in_same_tool_call(
	httpserver: HTTPServer, browser_session: BrowserSession, mcp_server: AgentycServer
):
	httpserver.expect_request('/route-wait-click').respond_with_data(_ROUTE_PAGE, content_type='text/html')
	base_url = f'http://127.0.0.1:{httpserver.port}'
	nav_result = await mcp_server._navigate(f'{base_url}/route-wait-click')
	assert not nav_result.startswith('Error'), f'Navigation failed: {nav_result}'

	result = await mcp_server._execute_tool(
		'browser_click',
		{
			'label': 'Finish setup',
			'wait_for_url_substring': '/app/success',
			'url_timeout_seconds': 5.0,
		},
	)
	assert not str(result).startswith('Error'), f'click URL wait failed: {result}'
	assert '/app/success?step=2' in str(result)


async def test_browser_click_wait_for_url_surfaces_timeout(
	httpserver: HTTPServer, browser_session: BrowserSession, mcp_server: AgentycServer
):
	httpserver.expect_request('/route-wait-click-timeout').respond_with_data(_ROUTE_PAGE, content_type='text/html')
	base_url = f'http://127.0.0.1:{httpserver.port}'
	nav_result = await mcp_server._navigate(f'{base_url}/route-wait-click-timeout')
	assert not nav_result.startswith('Error'), f'Navigation failed: {nav_result}'

	result = await mcp_server._execute_tool(
		'browser_click',
		{
			'ref': await _button_ref(mcp_server),
			'wait_for_url_regex': 'no-match$',
			'url_timeout_seconds': 0.5,
		},
	)
	assert str(result).startswith('Error [timeout]'), result
