"""Integration tests for browser environment emulation tools."""

from __future__ import annotations

import json

import pytest
from pytest_httpserver import HTTPServer
from werkzeug.wrappers import Response

from agentyc.browser import BrowserProfile, BrowserSession
from agentyc.mcp.server import AgentycServer
from agentyc.tools.service import Tools

_ENV_PAGE = """
<!DOCTYPE html>
<html lang="en">
<body>
  <p>Environment test page</p>
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


async def test_browser_set_user_agent_affects_navigator_and_request_headers(httpserver: HTTPServer, mcp_server: AgentycServer):
	def capture_handler(request) -> Response:
		return Response(
			json.dumps(
				{
					'userAgent': request.headers.get('User-Agent'),
					'acceptLanguage': request.headers.get('Accept-Language'),
				}
			),
			status=200,
			content_type='application/json',
		)

	httpserver.expect_request('/env').respond_with_data(_ENV_PAGE, content_type='text/html')
	httpserver.expect_request('/ua-echo', method='POST').respond_with_handler(capture_handler)

	set_result = await mcp_server._execute_tool(
		'browser_set_user_agent',
		{
			'user_agent': 'AgentycTest/1.0',
			'accept_language': 'fr-FR,fr;q=0.9',
			'platform': 'MacIntel',
		},
	)
	assert set_result == 'Set user agent override to AgentycTest/1.0'

	base_url = f'http://127.0.0.1:{httpserver.port}'
	navigate_result = await mcp_server._navigate(f'{base_url}/env')
	assert not navigate_result.startswith('Error'), f'Navigation failed: {navigate_result}'

	payload = await mcp_server._execute_tool(
		'browser_evaluate',
		{
			'code': """(async function(){
				const response = await fetch('/ua-echo', {method: 'POST'});
				const network = await response.json();
				return JSON.stringify({
					navigatorUserAgent: navigator.userAgent,
					navigatorPlatform: navigator.platform,
					headerUserAgent: network.userAgent,
					headerAcceptLanguage: network.acceptLanguage
				});
			})()"""
		},
	)
	assert 'AgentycTest/1.0' in str(payload), payload
	assert 'MacIntel' in str(payload), payload
	assert 'fr-FR,fr;q=0.9' in str(payload), payload


async def test_browser_set_timezone_and_locale_affect_intl(httpserver: HTTPServer, mcp_server: AgentycServer):
	httpserver.expect_request('/intl').respond_with_data(_ENV_PAGE, content_type='text/html')

	timezone_result = await mcp_server._execute_tool('browser_set_timezone', {'timezone_id': 'America/New_York'})
	assert timezone_result == 'Set timezone override to America/New_York'
	locale_result = await mcp_server._execute_tool('browser_set_locale', {'locale': 'fr-FR'})
	assert locale_result == 'Set locale override to fr-FR'

	base_url = f'http://127.0.0.1:{httpserver.port}'
	navigate_result = await mcp_server._navigate(f'{base_url}/intl')
	assert not navigate_result.startswith('Error'), f'Navigation failed: {navigate_result}'

	payload = await mcp_server._execute_tool(
		'browser_evaluate',
		{
			'code': """(function(){
				const options = Intl.DateTimeFormat().resolvedOptions();
				return JSON.stringify({
					locale: options.locale,
					timeZone: options.timeZone
				});
			})()"""
		},
	)
	assert '"locale":"fr-FR"' in str(payload), payload
	assert '"timeZone":"America/New_York"' in str(payload), payload


async def test_browser_emulate_media_updates_match_media_and_clears(httpserver: HTTPServer, mcp_server: AgentycServer):
	httpserver.expect_request('/media').respond_with_data(_ENV_PAGE, content_type='text/html')

	emulate_result = await mcp_server._execute_tool(
		'browser_emulate_media',
		{'media': 'print', 'color_scheme': 'dark', 'reduced_motion': 'reduce'},
	)
	assert 'media=print' in str(emulate_result), emulate_result

	base_url = f'http://127.0.0.1:{httpserver.port}'
	navigate_result = await mcp_server._navigate(f'{base_url}/media')
	assert not navigate_result.startswith('Error'), f'Navigation failed: {navigate_result}'

	payload = await mcp_server._execute_tool(
		'browser_evaluate',
		{
			'code': """(function(){
				return JSON.stringify({
					print: window.matchMedia('print').matches,
					dark: window.matchMedia('(prefers-color-scheme: dark)').matches,
					reduce: window.matchMedia('(prefers-reduced-motion: reduce)').matches
				});
			})()"""
		},
	)
	assert '"print":true' in str(payload), payload
	assert '"dark":true' in str(payload), payload
	assert '"reduce":true' in str(payload), payload

	clear_result = await mcp_server._execute_tool('browser_emulate_media', {})
	assert clear_result == 'Cleared emulated media'

	cleared_payload = await mcp_server._execute_tool(
		'browser_evaluate',
		{
			'code': """(function(){
				return JSON.stringify({
					print: window.matchMedia('print').matches,
					dark: window.matchMedia('(prefers-color-scheme: dark)').matches,
					reduce: window.matchMedia('(prefers-reduced-motion: reduce)').matches
				});
			})()"""
		},
	)
	assert '"print":false' in str(cleared_payload), cleared_payload

	invalid_result = await mcp_server._execute_tool('browser_emulate_media', {'color_scheme': 'neon'})
	assert str(invalid_result).startswith('Error [invalid_argument]'), invalid_result
