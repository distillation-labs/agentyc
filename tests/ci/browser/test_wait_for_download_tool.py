"""Integration tests for the browser_wait_for_download MCP tool."""

from __future__ import annotations

import asyncio
import json

import pytest
from pytest_httpserver import HTTPServer

from agentyc.browser import BrowserProfile, BrowserSession
from agentyc.mcp.server import AgentycServer
from agentyc.tools.service import Tools

_DOWNLOAD_PAGE = """
<!DOCTYPE html>
<html lang="en">
<head><title>Download wait tool</title></head>
<body>
  <main>
    <a id="download-link" href="/downloads/report.txt" download aria-label="Download report">Download report</a>
  </main>
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


async def _download_ref(server: AgentycServer) -> str:
	state_json, _ = await server._get_browser_state(mode='full', include_screenshot=False)
	payload = json.loads(state_json)
	return next(element['ref'] for element in payload['interactive_elements'] if element.get('text') == 'Download report')


async def test_browser_wait_for_download_returns_existing_match_after_click(
	httpserver: HTTPServer, browser_session: BrowserSession, mcp_server: AgentycServer
):
	httpserver.expect_request('/downloads-page').respond_with_data(_DOWNLOAD_PAGE, content_type='text/html')
	httpserver.expect_request('/downloads/report.txt').respond_with_data(
		'report downloaded by test\n',
		content_type='text/plain',
		headers={'Content-Disposition': 'attachment; filename="report.txt"'},
	)
	base_url = f'http://127.0.0.1:{httpserver.port}'
	nav_result = await mcp_server._navigate(f'{base_url}/downloads-page')
	assert not nav_result.startswith('Error'), f'Navigation failed: {nav_result}'

	click_result = await mcp_server._click(ref=await _download_ref(mcp_server))
	assert not click_result.startswith('Error'), f'Download click failed: {click_result}'
	await asyncio.sleep(0.2)

	result = await mcp_server._execute_tool(
		'browser_wait_for_download',
		{'expected_name': 'report.txt', 'timeout_seconds': 5.0},
	)
	assert not str(result).startswith('Error'), f'wait_for_download failed: {result}'
	payload = json.loads(str(result))
	assert payload['name'] == 'report.txt'
	assert payload['size_bytes'] > 0


async def test_browser_click_can_wait_for_download_in_same_tool_call(
	httpserver: HTTPServer, browser_session: BrowserSession, mcp_server: AgentycServer
):
	httpserver.expect_request('/downloads-page-click').respond_with_data(_DOWNLOAD_PAGE, content_type='text/html')
	httpserver.expect_request('/downloads/report.txt').respond_with_data(
		'report downloaded by click fusion test\n',
		content_type='text/plain',
		headers={'Content-Disposition': 'attachment; filename="report.txt"'},
	)
	base_url = f'http://127.0.0.1:{httpserver.port}'
	nav_result = await mcp_server._navigate(f'{base_url}/downloads-page-click')
	assert not nav_result.startswith('Error'), f'Navigation failed: {nav_result}'

	result = await mcp_server._execute_tool(
		'browser_click',
		{
			'label': 'Download report',
			'wait_for_download': True,
			'expected_download_name': 'report.txt',
			'download_timeout_seconds': 5.0,
		},
	)
	assert not str(result).startswith('Error'), f'click wait failed: {result}'
	assert 'downloaded report.txt' in str(result)


async def test_browser_wait_for_download_times_out_when_match_never_arrives(
	httpserver: HTTPServer, browser_session: BrowserSession, mcp_server: AgentycServer
):
	httpserver.expect_request('/downloads-page-timeout').respond_with_data(_DOWNLOAD_PAGE, content_type='text/html')
	base_url = f'http://127.0.0.1:{httpserver.port}'
	nav_result = await mcp_server._navigate(f'{base_url}/downloads-page-timeout')
	assert not nav_result.startswith('Error'), f'Navigation failed: {nav_result}'

	result = await mcp_server._execute_tool(
		'browser_wait_for_download',
		{'expected_name': 'missing.txt', 'timeout_seconds': 0.5},
	)
	assert str(result).startswith('Error [timeout]'), result


async def test_browser_click_wait_for_download_surfaces_timeout(
	httpserver: HTTPServer, browser_session: BrowserSession, mcp_server: AgentycServer
):
	httpserver.expect_request('/downloads-page-click-timeout').respond_with_data(_DOWNLOAD_PAGE, content_type='text/html')
	httpserver.expect_request('/downloads/report.txt').respond_with_data(
		'report downloaded by click timeout test\n',
		content_type='text/plain',
		headers={'Content-Disposition': 'attachment; filename="report.txt"'},
	)
	base_url = f'http://127.0.0.1:{httpserver.port}'
	nav_result = await mcp_server._navigate(f'{base_url}/downloads-page-click-timeout')
	assert not nav_result.startswith('Error'), f'Navigation failed: {nav_result}'

	result = await mcp_server._execute_tool(
		'browser_click',
		{
			'ref': await _download_ref(mcp_server),
			'wait_for_download': True,
			'expected_download_name': 'missing.txt',
			'download_timeout_seconds': 0.5,
		},
	)
	assert str(result).startswith('Error [timeout]'), result
