"""Integration tests for the browser_wait_for_tab MCP tool."""

from __future__ import annotations

import json

import pytest
from pytest_httpserver import HTTPServer

from agentyc.browser import BrowserProfile, BrowserSession
from agentyc.mcp.server import AgentycServer
from agentyc.tools.service import Tools

_POPUP_PARENT_PAGE = """
<!DOCTYPE html>
<html lang="en">
<head><title>Wait for tab</title></head>
<body>
  <main>
    <button id="delayed-popup">Open delayed popup</button>
    <div id="status">Idle</div>
  </main>
  <script>
    document.getElementById('delayed-popup').addEventListener('click', () => {
      document.getElementById('status').textContent = 'Opening soon';
      setTimeout(() => {
        window.open('/popup-child?mode=wait', '_blank', 'noopener');
      }, 150);
    });
  </script>
</body>
</html>
"""

_POPUP_FUSION_PAGE = """
<!DOCTYPE html>
<html lang="en">
<head><title>Click wait for tab</title></head>
<body>
  <main>
    <button id="fusion-popup">Open fused popup</button>
  </main>
  <script>
    document.getElementById('fusion-popup').addEventListener('click', () => {
      setTimeout(() => {
        window.open('/popup-child?mode=fused', '_blank', 'noopener');
      }, 150);
    });
  </script>
</body>
</html>
"""

_NO_POPUP_PAGE = """
<!DOCTYPE html>
<html lang="en">
<head><title>No popup</title></head>
<body>
  <main>
    <button id="no-popup">Stay here</button>
  </main>
  <script>
    document.getElementById('no-popup').addEventListener('click', () => {
      document.body.dataset.clicked = 'true';
    });
  </script>
</body>
</html>
"""

_POPUP_CHILD_PAGE = """
<!DOCTYPE html>
<html lang="en">
<head><title>Popup child</title></head>
<body>
  <main>Popup child ready</main>
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


async def _button_ref(server: AgentycServer, text: str) -> str:
	state_json, _ = await server._get_browser_state(mode='full', include_screenshot=False)
	payload = json.loads(state_json)
	return next(element['ref'] for element in payload['interactive_elements'] if element.get('text') == text)


async def test_browser_wait_for_tab_matches_delayed_popup_and_switches_focus(
	httpserver: HTTPServer, browser_session: BrowserSession, mcp_server: AgentycServer
):
	httpserver.expect_request('/popup-parent').respond_with_data(_POPUP_PARENT_PAGE, content_type='text/html')
	httpserver.expect_request('/popup-child').respond_with_data(_POPUP_CHILD_PAGE, content_type='text/html')
	base_url = f'http://127.0.0.1:{httpserver.port}'

	nav_result = await mcp_server._navigate(f'{base_url}/popup-parent')
	assert not nav_result.startswith('Error'), f'Navigation failed: {nav_result}'

	click_result = await mcp_server._click(ref=await _button_ref(mcp_server, 'Open delayed popup'))
	assert not click_result.startswith('Error'), f'Click failed: {click_result}'

	result = await mcp_server._execute_tool(
		'browser_wait_for_tab',
		{'url_substring': '/popup-child?mode=wait', 'timeout_seconds': 5.0},
	)
	assert not str(result).startswith('Error'), f'wait_for_tab failed: {result}'

	payload = json.loads(str(result))
	assert payload['url'].endswith('/popup-child?mode=wait')
	assert await browser_session.get_current_page_url() == f'{base_url}/popup-child?mode=wait'


async def test_browser_click_can_wait_for_tab_in_same_tool_call(
	httpserver: HTTPServer, browser_session: BrowserSession, mcp_server: AgentycServer
):
	httpserver.expect_request('/popup-fusion').respond_with_data(_POPUP_FUSION_PAGE, content_type='text/html')
	httpserver.expect_request('/popup-child').respond_with_data(_POPUP_CHILD_PAGE, content_type='text/html')
	base_url = f'http://127.0.0.1:{httpserver.port}'

	nav_result = await mcp_server._navigate(f'{base_url}/popup-fusion')
	assert not nav_result.startswith('Error'), f'Navigation failed: {nav_result}'

	result = await mcp_server._execute_tool(
		'browser_click',
		{
			'label': 'Open fused popup',
			'wait_for_tab': True,
			'expected_tab_url_substring': '/popup-child?mode=fused',
			'tab_timeout_seconds': 5.0,
		},
	)
	assert not str(result).startswith('Error'), f'click wait_for_tab failed: {result}'
	assert 'switched to tab' in str(result)
	assert '/popup-child?mode=fused' in str(result)
	assert await browser_session.get_current_page_url() == f'{base_url}/popup-child?mode=fused'


async def test_browser_wait_for_tab_times_out_when_popup_never_opens(
	httpserver: HTTPServer, browser_session: BrowserSession, mcp_server: AgentycServer
):
	httpserver.expect_request('/no-popup').respond_with_data(_NO_POPUP_PAGE, content_type='text/html')
	base_url = f'http://127.0.0.1:{httpserver.port}'

	nav_result = await mcp_server._navigate(f'{base_url}/no-popup')
	assert not nav_result.startswith('Error'), f'Navigation failed: {nav_result}'

	click_result = await mcp_server._click(ref=await _button_ref(mcp_server, 'Stay here'))
	assert not click_result.startswith('Error'), f'Click failed: {click_result}'

	result = await mcp_server._execute_tool(
		'browser_wait_for_tab',
		{'timeout_seconds': 0.5},
	)
	assert str(result).startswith('Error [timeout]'), result
