"""Integration tests for label-targeted dropdown helpers."""

from __future__ import annotations

import pytest
from pytest_httpserver import HTTPServer

from agentyc.browser import BrowserProfile, BrowserSession
from agentyc.mcp.server import AgentycServer
from agentyc.tools.service import Tools

_DROPDOWN_PAGE = """
<!DOCTYPE html>
<html lang="en">
<body>
  <form>
    <label for="plan">Plan</label>
    <select id="plan" aria-label="Plan">
      <option value="starter">Starter</option>
      <option value="pro">Pro</option>
      <option value="enterprise">Enterprise</option>
    </select>
  </form>
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
	return server


async def test_browser_get_dropdown_options_can_target_select_by_label(
	httpserver: HTTPServer, browser_session: BrowserSession, mcp_server: AgentycServer
):
	httpserver.expect_request('/dropdown-by-label').respond_with_data(_DROPDOWN_PAGE, content_type='text/html')
	base_url = f'http://127.0.0.1:{httpserver.port}'
	nav_result = await mcp_server._navigate(f'{base_url}/dropdown-by-label')
	assert not nav_result.startswith('Error'), f'Navigation failed: {nav_result}'

	result = await mcp_server._execute_tool('browser_get_dropdown_options', {'label': 'Plan'})
	assert not str(result).startswith('Error'), f'browser_get_dropdown_options failed: {result}'
	assert 'Starter' in str(result)
	assert 'Enterprise' in str(result)


async def test_browser_select_option_can_target_select_by_label(
	httpserver: HTTPServer, browser_session: BrowserSession, mcp_server: AgentycServer
):
	httpserver.expect_request('/select-by-label').respond_with_data(_DROPDOWN_PAGE, content_type='text/html')
	base_url = f'http://127.0.0.1:{httpserver.port}'
	nav_result = await mcp_server._navigate(f'{base_url}/select-by-label')
	assert not nav_result.startswith('Error'), f'Navigation failed: {nav_result}'

	result = await mcp_server._execute_tool(
		'browser_select_option',
		{'label': 'Plan', 'text': 'Pro'},
	)
	assert not str(result).startswith('Error'), f'browser_select_option failed: {result}'

	value = await mcp_server._evaluate('(function(){ return document.getElementById("plan").value; })()')
	assert value == 'pro'
