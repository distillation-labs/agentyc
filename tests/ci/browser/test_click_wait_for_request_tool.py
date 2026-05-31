"""Integration tests for fused browser_click request waiting."""

from __future__ import annotations

import json

import pytest
from pytest_httpserver import HTTPServer

from agentyc.browser import BrowserProfile, BrowserSession
from agentyc.mcp.server import AgentycServer
from agentyc.tools.service import Tools

_NETWORK_WAIT_PAGE = """
<!DOCTYPE html>
<html lang="en">
<head><title>Network waits</title></head>
<body>
  <main>
    <button id="post-data">Post data</button>
    <p id="result">Idle</p>
  </main>
  <script>
    document.getElementById('post-data').addEventListener('click', async () => {
      const response = await fetch('/wait-api', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-Debug-Token': 'alpha'
        },
        body: JSON.stringify({message: 'hello'})
      });
      document.getElementById('result').textContent = 'POST ' + response.status;
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


async def _post_button_ref(server: AgentycServer) -> str:
	state_json, _ = await server._get_browser_state(mode='full', include_screenshot=False)
	payload = json.loads(state_json)
	return next(element['ref'] for element in payload['interactive_elements'] if element.get('text') == 'Post data')


async def test_browser_click_can_wait_for_fast_request_in_same_tool_call(
	httpserver: HTTPServer, browser_session: BrowserSession, mcp_server: AgentycServer
):
	httpserver.expect_request('/network-waits').respond_with_data(_NETWORK_WAIT_PAGE, content_type='text/html')
	httpserver.expect_request('/wait-api', method='POST').respond_with_data(
		'{"saved":true}',
		status=201,
		content_type='application/json',
		headers={'X-Debug-Result': 'saved'},
	)
	base_url = f'http://127.0.0.1:{httpserver.port}'
	nav_result = await mcp_server._navigate(f'{base_url}/network-waits')
	assert not nav_result.startswith('Error'), f'Navigation failed: {nav_result}'

	result = await mcp_server._execute_tool(
		'browser_click',
		{
			'label': 'Post data',
			'wait_for_request': {
				'url_substring': '/wait-api',
				'method': 'POST',
				'include_headers': True,
				'timeout_seconds': 5.0,
			},
		},
	)
	assert not str(result).startswith('Error'), f'click wait_for_request failed: {result}'
	payload = json.loads(str(result))
	assert payload['method'] == 'POST'
	assert payload['url'].endswith('/wait-api')
	assert str(payload.get('req_headers', {}).get('X-Debug-Token', '')).lower() == 'alpha'


async def test_browser_click_wait_for_request_surfaces_timeout(
	httpserver: HTTPServer, browser_session: BrowserSession, mcp_server: AgentycServer
):
	httpserver.expect_request('/network-waits-timeout').respond_with_data(_NETWORK_WAIT_PAGE, content_type='text/html')
	httpserver.expect_request('/wait-api', method='POST').respond_with_data(
		'{"saved":true}',
		status=201,
		content_type='application/json',
		headers={'X-Debug-Result': 'saved'},
	)
	base_url = f'http://127.0.0.1:{httpserver.port}'
	nav_result = await mcp_server._navigate(f'{base_url}/network-waits-timeout')
	assert not nav_result.startswith('Error'), f'Navigation failed: {nav_result}'

	result = await mcp_server._execute_tool(
		'browser_click',
		{
			'ref': await _post_button_ref(mcp_server),
			'wait_for_request': {
				'url_regex': 'no-match$',
				'method': 'POST',
				'timeout_seconds': 0.5,
			},
		},
	)
	assert str(result).startswith('Error [timeout]'), result
