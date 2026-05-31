"""Integration tests for browser_set_extra_headers."""

from __future__ import annotations

import json

import pytest
from pytest_httpserver import HTTPServer
from werkzeug.wrappers import Response

from agentyc.browser import BrowserProfile, BrowserSession
from agentyc.mcp.server import AgentycServer
from agentyc.tools.service import Tools

_HEADERS_PAGE = """
<!DOCTYPE html>
<html lang="en">
<body>
  <p>Header test page</p>
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


async def test_browser_set_extra_headers_applies_and_clears_request_headers(httpserver: HTTPServer, mcp_server: AgentycServer):
	captured_headers: list[str | None] = []

	def capture_handler(request) -> Response:
		header_value = request.headers.get('X-Agentyc-Test')
		captured_headers.append(header_value)
		return Response(
			json.dumps({'header': header_value}),
			status=200,
			content_type='application/json',
		)

	httpserver.expect_request('/headers').respond_with_data(_HEADERS_PAGE, content_type='text/html')
	httpserver.expect_request('/echo', method='POST').respond_with_handler(capture_handler)
	httpserver.expect_request('/echo', method='POST').respond_with_handler(capture_handler)

	base_url = f'http://127.0.0.1:{httpserver.port}'
	navigate_result = await mcp_server._navigate(f'{base_url}/headers')
	assert not navigate_result.startswith('Error'), f'Navigation failed: {navigate_result}'

	set_result = await mcp_server._execute_tool(
		'browser_set_extra_headers',
		{'headers': {'X-Agentyc-Test': 'breakthrough'}},
	)
	assert set_result == 'Set 1 extra HTTP header(s)'

	first_response = await mcp_server._execute_tool(
		'browser_evaluate',
		{
			'code': """(async function(){
				const response = await fetch('/echo', {method: 'POST'});
				return await response.text();
			})()"""
		},
	)
	assert 'breakthrough' in str(first_response), first_response

	clear_result = await mcp_server._execute_tool('browser_set_extra_headers', {'headers': {}})
	assert clear_result == 'Cleared extra HTTP headers'

	second_response = await mcp_server._execute_tool(
		'browser_evaluate',
		{
			'code': """(async function(){
				const response = await fetch('/echo', {method: 'POST'});
				return await response.text();
			})()"""
		},
	)
	assert '"header": null' in str(second_response), second_response
	assert captured_headers == ['breakthrough', None]


async def test_browser_set_extra_headers_requires_active_page(mcp_server: AgentycServer):
	mcp_server.browser_session.agent_focus_target_id = None
	result = await mcp_server._execute_tool('browser_set_extra_headers', {'headers': {'X-Agentyc-Test': 'value'}})
	assert result == 'Error: No browser page active'
