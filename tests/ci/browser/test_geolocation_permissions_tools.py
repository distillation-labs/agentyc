"""Integration tests for browser_grant_permissions and browser_set_geolocation."""

from __future__ import annotations

import pytest
from pytest_httpserver import HTTPServer

from agentyc.browser import BrowserProfile, BrowserSession
from agentyc.mcp.server import AgentycServer
from agentyc.tools.service import Tools

_GEO_PAGE = """
<!DOCTYPE html>
<html lang="en">
<body>
  <p>Geolocation test page</p>
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


async def test_browser_grant_permissions_and_set_geolocation_enable_geolocation_reads(
	httpserver: HTTPServer, browser_session: BrowserSession, mcp_server: AgentycServer
):
	httpserver.expect_request('/geo').respond_with_data(_GEO_PAGE, content_type='text/html')
	base_url = f'http://127.0.0.1:{httpserver.port}'
	nav_result = await mcp_server._navigate(f'{base_url}/geo')
	assert not nav_result.startswith('Error'), f'Navigation failed: {nav_result}'

	grant_result = await mcp_server._execute_tool(
		'browser_grant_permissions',
		{'permissions': ['geolocation'], 'origin': base_url},
	)
	assert not str(grant_result).startswith('Error'), f'Grant permissions failed: {grant_result}'

	geo_result = await mcp_server._execute_tool(
		'browser_set_geolocation',
		{'latitude': 40.7128, 'longitude': -74.0060, 'accuracy': 25},
	)
	assert not str(geo_result).startswith('Error'), f'Set geolocation failed: {geo_result}'

	position = await mcp_server._execute_tool(
		'browser_evaluate',
		{
			'code': """(async function(){
				return await new Promise(resolve => navigator.geolocation.getCurrentPosition(
					pos => resolve(`${pos.coords.latitude.toFixed(4)},${pos.coords.longitude.toFixed(4)},${pos.coords.accuracy}`),
					err => resolve(`error:${err.code}`)
				));
			})()"""
		},
	)
	assert '40.7128,-74.0060' in str(position), position


async def test_browser_grant_permissions_requires_permissions_list(
	httpserver: HTTPServer, browser_session: BrowserSession, mcp_server: AgentycServer
):
	httpserver.expect_request('/geo-invalid').respond_with_data(_GEO_PAGE, content_type='text/html')
	base_url = f'http://127.0.0.1:{httpserver.port}'
	nav_result = await mcp_server._navigate(f'{base_url}/geo-invalid')
	assert not nav_result.startswith('Error'), f'Navigation failed: {nav_result}'

	result = await mcp_server._execute_tool('browser_grant_permissions', {'permissions': []})
	assert str(result).startswith('Error [invalid_argument]'), result

	invalid_result = await mcp_server._execute_tool('browser_grant_permissions', {'permissions': ['not-a-permission']})
	assert str(invalid_result).startswith('Error [invalid_argument]'), invalid_result
