"""Integration tests for label-targeted browser_type usage."""

from __future__ import annotations

import pytest
from pytest_httpserver import HTTPServer

from agentyc.browser import BrowserProfile, BrowserSession
from agentyc.mcp.server import AgentycServer
from agentyc.tools.service import Tools

_TYPE_PAGE = """
<!DOCTYPE html>
<html lang="en">
<body>
  <form>
    <label for="email">Email address</label>
    <input id="email" type="email" aria-label="Email address" placeholder="name@example.com">
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


async def test_browser_type_can_target_input_by_label(
	httpserver: HTTPServer, browser_session: BrowserSession, mcp_server: AgentycServer
):
	httpserver.expect_request('/type-by-label').respond_with_data(_TYPE_PAGE, content_type='text/html')
	base_url = f'http://127.0.0.1:{httpserver.port}'
	nav_result = await mcp_server._navigate(f'{base_url}/type-by-label')
	assert not nav_result.startswith('Error'), f'Navigation failed: {nav_result}'

	result = await mcp_server._execute_tool(
		'browser_type',
		{'label': 'Email address', 'text': 'hello@example.com'},
	)
	assert not str(result).startswith('Error'), f'browser_type failed: {result}'
	assert 'Typed <email> into element' in str(result)

	value = await mcp_server._evaluate('(function(){ return document.getElementById("email").value; })()')
	assert value == 'hello@example.com'


async def test_browser_type_by_label_returns_explicit_error_for_missing_target(
	httpserver: HTTPServer, browser_session: BrowserSession, mcp_server: AgentycServer
):
	httpserver.expect_request('/type-by-label-missing').respond_with_data(_TYPE_PAGE, content_type='text/html')
	base_url = f'http://127.0.0.1:{httpserver.port}'
	nav_result = await mcp_server._navigate(f'{base_url}/type-by-label-missing')
	assert not nav_result.startswith('Error'), f'Navigation failed: {nav_result}'

	result = await mcp_server._execute_tool(
		'browser_type',
		{'label': 'Password', 'text': 'secret'},
	)
	assert str(result).startswith('Error [invalid_argument]'), result
