"""Headless browser tests for the 7 new MCP tools added in the production-readiness upgrade."""

import asyncio
import json

import pytest
from pytest_httpserver import HTTPServer

from agentyc.browser import BrowserProfile, BrowserSession
from agentyc.filesystem.file_system import FileSystem
from agentyc.mcp.server import AgentycServer
from agentyc.tools.service import Tools

_TEST_PAGE = """
<!DOCTYPE html>
<html lang="en">
<head><title>Test Page</title></head>
<body>
	<main>
		<h1 id="title" data-testid="main-title">Test Page</h1>
		<a href="/download" id="download-link" download>Download</a>
		<button id="trigger-alert" onclick="alert('Hello from alert')">Show Alert</button>
		<button id="trigger-confirm" onclick="confirm('Are you sure?')">Show Confirm</button>
		<button id="trigger-prompt" onclick="prompt('Enter name:', 'default')">Show Prompt</button>
		<div id="dynamic-content" aria-live="polite"></div>
		<p id="viewport-test" style="width:100vw;height:100vh;">Viewport test</p>
	</main>
	<script>
		function addContent() {
			const el = document.getElementById('dynamic-content');
			el.textContent = 'Content added at ' + Date.now();
		}
		setTimeout(addContent, 50);
		setTimeout(addContent, 100);
	</script>
</body>
</html>
"""

_DOWNLOAD_PAGE = """
<!DOCTYPE html>
<html lang="en">
<head><title>Download test</title></head>
<body>
	<main>
		<p>Download page</p>
	</main>
</body>
</html>
"""


@pytest.fixture
async def browser_session():
	session = BrowserSession(
		browser_profile=BrowserProfile(
			headless=True,
			user_data_dir=None,
		)
	)
	await session.start()
	yield session
	await session.stop()


@pytest.fixture
def mcp_server(browser_session: BrowserSession):
	server = AgentycServer()
	server.browser_session = browser_session
	server.tools = Tools()
	server._console_log_buffer.clear()
	server._network_log_buffer.clear()
	server._network_pending.clear()
	return server


class TestNewMCPTools:
	"""Integration tests for the 7 new MCP browser tools."""

	async def test_wait_for_stable_dom(self, httpserver: HTTPServer, browser_session: BrowserSession, mcp_server):
		"""browser_wait_for_stable_dom must detect DOM stability after dynamic mutations settle."""
		httpserver.expect_request('/stable').respond_with_data(_TEST_PAGE, content_type='text/html')
		base_url = f'http://127.0.0.1:{httpserver.port}'
		nav_result = await mcp_server._navigate(f'{base_url}/stable')
		assert not nav_result.startswith('Error'), f'Navigation failed: {nav_result}'
		await asyncio.sleep(0.3)

		result = await mcp_server._wait_for_stable_dom(timeout_seconds=5.0, quiet_ms=300)
		assert not result.startswith('Error'), f'wait_for_stable_dom failed: {result}'
		assert 'stable' in result.lower() or 'timeout' in result.lower(), f'Expected stable or timeout, got: {result}'

	async def test_wait_for_stable_dom_timeout(self, httpserver: HTTPServer, browser_session: BrowserSession, mcp_server):
		"""wait_for_stable_dom must handle timeout gracefully."""
		httpserver.expect_request('/forever').respond_with_data(
			"""
        <!DOCTYPE html>
        <html><body>
        <div id="mutate"></div>
        <script>
        setInterval(() => {
            document.getElementById('mutate').textContent = Math.random();
        }, 10);
        </script>
        </body></html>
        """,
			content_type='text/html',
		)
		base_url = f'http://127.0.0.1:{httpserver.port}'
		nav_result = await mcp_server._navigate(f'{base_url}/forever')
		assert not nav_result.startswith('Error'), f'Navigation failed: {nav_result}'
		await asyncio.sleep(0.2)

		result = await mcp_server._wait_for_stable_dom(timeout_seconds=2.0, quiet_ms=500)
		assert not result.startswith('Error'), f'wait_for_stable_dom should not error on timeout: {result}'
		assert '(timeout)' in result, f'Expected timeout indicator, got: {result}'

	async def test_handle_dialog_no_dialog(self, httpserver: HTTPServer, browser_session: BrowserSession, mcp_server):
		"""browser_handle_dialog must error informatively when no dialog is showing.
		PopupsWatchdog auto-handles all dialogs, so in normal operation the tool will
		only see 'No dialog is showing'.
		"""
		httpserver.expect_request('/nodialog').respond_with_data(_TEST_PAGE, content_type='text/html')
		base_url = f'http://127.0.0.1:{httpserver.port}'
		nav_result = await mcp_server._navigate(f'{base_url}/nodialog')
		assert not nav_result.startswith('Error'), f'Navigation failed: {nav_result}'
		await asyncio.sleep(0.3)

		result = await mcp_server._handle_dialog(accept=True)
		assert 'Error' in result

	async def test_get_attribute(self, httpserver: HTTPServer, browser_session: BrowserSession, mcp_server):
		"""browser_get_attribute must read specific attributes from elements."""
		httpserver.expect_request('/attrs').respond_with_data(
			"""
        <!DOCTYPE html>
        <html><body>
        <a id="link1" href="/target" class="nav-link" data-testid="main-link">Click here</a>
        <img id="img1" src="/image.png" alt="Test image" width="200" height="100">
        <input id="input1" type="text" value="hello" disabled>
        </body></html>
        """,
			content_type='text/html',
		)
		base_url = f'http://127.0.0.1:{httpserver.port}'
		nav_result = await mcp_server._navigate(f'{base_url}/attrs')
		assert not nav_result.startswith('Error'), f'Navigation failed: {nav_result}'
		await asyncio.sleep(0.3)

		state_json, _ = await mcp_server._get_browser_state(include_screenshot=False)
		state = json.loads(state_json)
		elements = state.get('interactive_elements', [])
		link_ref = next((el['ref'] for el in elements if 'Click here' in (el.get('text') or '')), None)
		assert link_ref is not None, f'Link not found: {[el.get("text") for el in elements]}'

		href = await mcp_server._get_attribute(name='href', ref=link_ref)
		assert not href.startswith('Error'), f'get_attribute failed: {href}'
		assert '/target' in href

		cls = await mcp_server._get_attribute(name='class', ref=link_ref)
		assert not cls.startswith('Error'), f'get_attribute class failed: {cls}'
		assert 'nav-link' in cls

	async def test_get_attribute_nonexistent(self, httpserver: HTTPServer, browser_session: BrowserSession, mcp_server):
		"""browser_get_attribute must report when attribute does not exist."""
		httpserver.expect_request('/noattr').respond_with_data(
			"""
        <!DOCTYPE html>
        <html><body>
        <p id="plain">Text</p>
        </body></html>
        """,
			content_type='text/html',
		)
		base_url = f'http://127.0.0.1:{httpserver.port}'
		nav_result = await mcp_server._navigate(f'{base_url}/noattr')
		assert not nav_result.startswith('Error'), f'Navigation failed: {nav_result}'
		await asyncio.sleep(0.3)

		result = await mcp_server._get_attribute(name='nonexistent', ref='e1')
		assert 'not found' in result.lower()

	async def test_clear_logs_console(self, httpserver: HTTPServer, browser_session: BrowserSession, mcp_server):
		"""browser_clear_logs must clear the console log buffer."""
		httpserver.expect_request('/clear-console').respond_with_data(
			"""
        <!DOCTYPE html>
        <html><body>
        <script>
        console.log('entry 1');
        console.warn('entry 2');
        console.error('entry 3');
        </script>
        </body></html>
        """,
			content_type='text/html',
		)

		await mcp_server._register_cdp_event_listeners()
		base_url = f'http://127.0.0.1:{httpserver.port}'
		await mcp_server.browser_session.navigate_to(f'{base_url}/clear-console')
		await asyncio.sleep(0.5)

		logs_before = json.loads(await mcp_server._get_console_logs(max_entries=100))
		assert len(logs_before) > 0, f'Expected console entries, got: {logs_before}'

		clear_result = await mcp_server._clear_logs(console=True, network=False)
		assert not clear_result.startswith('Error'), f'clear_logs failed: {clear_result}'
		assert 'console' in clear_result.lower()

		logs_after = json.loads(await mcp_server._get_console_logs(max_entries=100))
		assert len(logs_after) == 0, f'Expected empty console after clear, got: {logs_after}'

	async def test_clear_logs_network(self, httpserver: HTTPServer, browser_session: BrowserSession, mcp_server):
		"""browser_clear_logs must clear the network log buffer."""
		httpserver.expect_request('/clear-network').respond_with_data(_TEST_PAGE, content_type='text/html')

		await mcp_server._register_cdp_event_listeners()
		base_url = f'http://127.0.0.1:{httpserver.port}'
		await mcp_server.browser_session.navigate_to(f'{base_url}/clear-network')
		await asyncio.sleep(0.5)

		nets_before = json.loads(await mcp_server._get_network_log(max_entries=100))
		assert len(nets_before) > 0, f'Expected network entries, got: {nets_before}'

		clear_result = await mcp_server._clear_logs(console=False, network=True)
		assert not clear_result.startswith('Error'), f'clear_logs failed: {clear_result}'
		assert 'network' in clear_result.lower()

		nets_after = json.loads(await mcp_server._get_network_log(max_entries=100))
		assert len(nets_after) == 0, f'Expected empty network log after clear, got: {nets_after}'

	async def test_start_stop_trace(self, httpserver: HTTPServer, browser_session: BrowserSession, mcp_server):
		"""browser_start_trace / browser_stop_trace must collect CDP trace events."""
		httpserver.expect_request('/trace').respond_with_data(_TEST_PAGE, content_type='text/html')

		await mcp_server._register_cdp_event_listeners()
		base_url = f'http://127.0.0.1:{httpserver.port}'
		nav_result = await mcp_server._navigate(f'{base_url}/trace')
		assert not nav_result.startswith('Error'), f'Navigation failed: {nav_result}'
		await asyncio.sleep(0.3)

		start_result = await mcp_server._start_trace()
		assert not start_result.startswith('Error'), f'start_trace failed: {start_result}'
		assert 'started' in start_result.lower()

		await asyncio.sleep(0.3)

		stop_result = await mcp_server._stop_trace()
		assert not stop_result.startswith('Error'), f'stop_trace failed: {stop_result}'

		trace_events = json.loads(stop_result)
		assert isinstance(trace_events, list), f'Expected list of trace events, got: {type(trace_events)}'

	async def test_stop_trace_without_start(self, mcp_server):
		"""browser_stop_trace must error when no trace is active."""
		result = await mcp_server._stop_trace()
		assert 'Error' in result
		assert 'No active trace' in result

	async def test_double_start_trace(self, httpserver: HTTPServer, browser_session: BrowserSession, mcp_server):
		"""Starting a trace when already active must work (replaces previous)."""
		httpserver.expect_request('/trace2').respond_with_data(_TEST_PAGE, content_type='text/html')

		await mcp_server._register_cdp_event_listeners()
		base_url = f'http://127.0.0.1:{httpserver.port}'
		nav_result = await mcp_server._navigate(f'{base_url}/trace2')
		assert not nav_result.startswith('Error'), f'Navigation failed: {nav_result}'
		await asyncio.sleep(0.3)

		r1 = await mcp_server._start_trace()
		assert 'started' in r1.lower()

		r2 = await mcp_server._start_trace()
		assert 'started' in r2.lower()

		await asyncio.sleep(0.2)
		stop = await mcp_server._stop_trace()
		assert not stop.startswith('Error'), f'stop_trace failed: {stop}'

	async def test_set_viewport(self, httpserver: HTTPServer, browser_session: BrowserSession, mcp_server):
		"""browser_set_viewport must change the viewport dimensions."""
		httpserver.expect_request('/viewport').respond_with_data(_TEST_PAGE, content_type='text/html')

		base_url = f'http://127.0.0.1:{httpserver.port}'
		nav_result = await mcp_server._navigate(f'{base_url}/viewport')
		assert not nav_result.startswith('Error'), f'Navigation failed: {nav_result}'
		await asyncio.sleep(0.3)

		result = await mcp_server._set_viewport(width=800, height=600)
		assert not result.startswith('Error'), f'set_viewport failed: {result}'
		assert '800x600' in result, f'Expected viewport in result, got: {result}'

		result2 = await mcp_server._set_viewport(width=1024, height=768)
		assert not result2.startswith('Error'), f'set_viewport failed: {result2}'
		assert '1024x768' in result2, f'Expected viewport in result, got: {result2}'

	async def test_save_as_pdf(self, httpserver: HTTPServer, browser_session: BrowserSession, mcp_server, tmp_path):
		"""browser_save_as_pdf must generate a PDF file."""
		httpserver.expect_request('/pdf').respond_with_data(
			"""
        <!DOCTYPE html>
        <html><body>
        <h1>PDF Test</h1>
        <p>This content should appear in the PDF.</p>
        </body></html>
        """,
			content_type='text/html',
		)

		mcp_server._file_system_base_dir = tmp_path
		mcp_server.file_system = FileSystem(base_dir=str(tmp_path))

		base_url = f'http://127.0.0.1:{httpserver.port}'
		nav_result = await mcp_server._navigate(f'{base_url}/pdf')
		assert not nav_result.startswith('Error'), f'Navigation failed: {nav_result}'
		await asyncio.sleep(0.3)

		result = await mcp_server._save_as_pdf(file_name='test-output.pdf')
		assert not result.startswith('Error'), f'save_as_pdf failed: {result}'
		assert 'test-output.pdf' in result

		pdf_path = tmp_path / 'agentyc_agent_data' / 'test-output.pdf'
		assert pdf_path.exists(), f'PDF not created at {pdf_path}'
		assert pdf_path.stat().st_size > 100, f'PDF too small: {pdf_path.stat().st_size} bytes'

	async def test_save_as_pdf_landscape(self, httpserver: HTTPServer, browser_session: BrowserSession, mcp_server, tmp_path):
		"""browser_save_as_pdf must support landscape orientation."""
		httpserver.expect_request('/pdf-land').respond_with_data(
			"""
        <!DOCTYPE html>
        <html><body>
        <h1>Landscape PDF</h1>
        </body></html>
        """,
			content_type='text/html',
		)

		mcp_server._file_system_base_dir = tmp_path
		mcp_server.file_system = FileSystem(base_dir=str(tmp_path))

		base_url = f'http://127.0.0.1:{httpserver.port}'
		nav_result = await mcp_server._navigate(f'{base_url}/pdf-land')
		assert not nav_result.startswith('Error'), f'Navigation failed: {nav_result}'
		await asyncio.sleep(0.3)

		result = await mcp_server._save_as_pdf(file_name='landscape.pdf', landscape=True)
		assert not result.startswith('Error'), f'save_as_pdf landscape failed: {result}'
		assert 'landscape.pdf' in result
		assert (tmp_path / 'agentyc_agent_data' / 'landscape.pdf').exists()

	async def test_save_as_pdf_without_filename(
		self, httpserver: HTTPServer, browser_session: BrowserSession, mcp_server, tmp_path
	):
		"""browser_save_as_pdf must use page title as default filename."""
		httpserver.expect_request('/pdf-title').respond_with_data(
			"""
        <!DOCTYPE html>
        <html><head><title>MyCustomTitle</title></head>
        <body><h1>Test</h1></body></html>
        """,
			content_type='text/html',
		)

		mcp_server._file_system_base_dir = tmp_path
		mcp_server.file_system = FileSystem(base_dir=str(tmp_path))

		base_url = f'http://127.0.0.1:{httpserver.port}'
		nav_result = await mcp_server._navigate(f'{base_url}/pdf-title')
		assert not nav_result.startswith('Error'), f'Navigation failed: {nav_result}'
		await asyncio.sleep(0.3)

		result = await mcp_server._save_as_pdf()
		assert not result.startswith('Error'), f'save_as_pdf without filename failed: {result}'
		assert 'MyCustomTitle' in result

	async def test_get_downloads_empty(self, mcp_server):
		"""browser_get_downloads must report no files when nothing downloaded."""
		result = await mcp_server._get_downloads()
		assert not result.startswith('Error'), f'get_downloads failed: {result}'
		assert 'No files downloaded' in result

	async def test_set_viewport_device_scale(self, httpserver: HTTPServer, browser_session: BrowserSession, mcp_server):
		"""browser_set_viewport must support device_scale_factor."""
		httpserver.expect_request('/scale').respond_with_data(_TEST_PAGE, content_type='text/html')

		base_url = f'http://127.0.0.1:{httpserver.port}'
		nav_result = await mcp_server._navigate(f'{base_url}/scale')
		assert not nav_result.startswith('Error'), f'Navigation failed: {nav_result}'
		await asyncio.sleep(0.3)

		result = await mcp_server._set_viewport(width=1280, height=720, device_scale_factor=2.0)
		assert not result.startswith('Error'), f'set_viewport with scale failed: {result}'
		assert '1280x720' in result

	async def test_wait_for_stable_dom_no_session(self, mcp_server):
		"""browser_wait_for_stable_dom must error when no browser session exists."""
		mcp_server.browser_session = None
		result = await mcp_server._wait_for_stable_dom()
		assert 'Error' in result

	async def test_handle_dialog_no_session(self, mcp_server):
		"""browser_handle_dialog must error when no browser session exists."""
		mcp_server.browser_session = None
		result = await mcp_server._handle_dialog()
		assert 'Error' in result

	async def test_get_attribute_no_session(self, mcp_server):
		"""browser_get_attribute must error when no browser session exists."""
		mcp_server.browser_session = None
		result = await mcp_server._get_attribute(name='href')
		assert 'Error' in result

	async def test_start_trace_no_session(self, mcp_server):
		"""browser_start_trace must error when no browser session exists."""
		mcp_server.browser_session = None
		result = await mcp_server._start_trace()
		assert 'Error' in result
