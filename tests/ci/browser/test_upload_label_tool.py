"""Integration tests for label-targeted browser_upload_file."""

from __future__ import annotations

import pytest
from pytest_httpserver import HTTPServer

from agentyc.browser import BrowserProfile, BrowserSession
from agentyc.mcp.server import AgentycServer
from agentyc.tools.service import Tools

_UPLOAD_PAGE = """
<!DOCTYPE html>
<html lang="en">
<body>
  <form>
    <label for="resume">Resume</label>
    <input id="resume" type="file" aria-label="Resume">
    <div id="status"></div>
  </form>
  <script>
    document.getElementById('resume').addEventListener('change', (event) => {
      const file = event.target.files[0];
      document.getElementById('status').textContent = file ? file.name : '';
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
	return server


async def test_browser_upload_file_can_target_input_by_label(
	httpserver: HTTPServer, browser_session: BrowserSession, mcp_server: AgentycServer, tmp_path
):
	upload_file = tmp_path / 'resume.txt'
	upload_file.write_text('resume content', encoding='utf-8')

	httpserver.expect_request('/upload-by-label').respond_with_data(_UPLOAD_PAGE, content_type='text/html')
	base_url = f'http://127.0.0.1:{httpserver.port}'
	nav_result = await mcp_server._navigate(f'{base_url}/upload-by-label')
	assert not nav_result.startswith('Error'), f'Navigation failed: {nav_result}'

	result = await mcp_server._execute_tool(
		'browser_upload_file',
		{'label': 'Resume', 'path': str(upload_file)},
	)
	assert not str(result).startswith('Error'), f'browser_upload_file failed: {result}'

	filename = await mcp_server._evaluate('(function(){ return document.getElementById("status").textContent; })()')
	assert filename == 'resume.txt'


async def test_browser_upload_file_by_label_returns_explicit_error_for_missing_target(
	httpserver: HTTPServer, browser_session: BrowserSession, mcp_server: AgentycServer, tmp_path
):
	upload_file = tmp_path / 'resume.txt'
	upload_file.write_text('resume content', encoding='utf-8')

	httpserver.expect_request('/upload-by-label-missing').respond_with_data(_UPLOAD_PAGE, content_type='text/html')
	base_url = f'http://127.0.0.1:{httpserver.port}'
	nav_result = await mcp_server._navigate(f'{base_url}/upload-by-label-missing')
	assert not nav_result.startswith('Error'), f'Navigation failed: {nav_result}'

	result = await mcp_server._execute_tool(
		'browser_upload_file',
		{'label': 'Cover letter', 'path': str(upload_file)},
	)
	assert str(result).startswith('Error [invalid_argument]'), result
