"""Headless real-world MCP workflows for release readiness hardening."""

import asyncio
import json

import pytest
from pytest_httpserver import HTTPServer

from agentyc.browser import BrowserProfile, BrowserSession
from agentyc.filesystem.file_system import FileSystem
from agentyc.mcp.server import AgentycServer
from agentyc.tools.service import Tools

_RELEASE_READINESS_HTML = """
<!DOCTYPE html>
<html lang="en">
<head><title>Release readiness</title></head>
<body>
	<main>
		<h1>Release readiness</h1>
		<p id="status">Booting checks...</p>
		<p id="summary">Collecting metadata...</p>
		<a id="download-link" href="/downloads/release-summary.txt" download aria-label="Download release summary">
			Download release summary
		</a>
		<a id="runbook-link" href="/runbook" aria-label="Open release runbook">Open release runbook</a>
	</main>
	<script>
		console.log('release readiness page loaded');
		setTimeout(() => {
			document.getElementById('status').textContent = 'Collecting release metadata...';
		}, 40);
		fetch('/release-status.json')
			.then(response => response.json())
			.then(data => {
				document.getElementById('summary').textContent = data.summary;
				console.warn('release metadata fetched');
			});
		setTimeout(() => {
			document.getElementById('status').textContent = 'Release summary ready';
			document.body.dataset.ready = 'true';
		}, 120);
	</script>
</body>
</html>
"""

_RUNBOOK_HTML = """
<!DOCTYPE html>
<html lang="en">
<head><title>Incident runbook</title></head>
<body>
	<main>
		<h1>Incident runbook</h1>
		<p>Escalate blocking release issues and confirm rollback ownership.</p>
	</main>
</body>
</html>
"""

_CONFIRM_DIALOG_HTML = """
<!DOCTYPE html>
<html lang="en">
<head><title>Confirm dialog</title></head>
<body>
	<main>
		<button aria-label="Delete branch">Delete branch</button>
		<p id="status">Idle</p>
	</main>
	<script>
		document.querySelector('button').addEventListener('click', () => {
			const confirmed = confirm('Delete this branch?');
			document.getElementById('status').textContent = confirmed ? 'Deleted' : 'Cancelled';
		});
	</script>
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


def _find_ref(state: dict[str, object], text: str) -> str | None:
	for element in state.get('interactive_elements', []):
		if isinstance(element, dict) and text in str(element.get('text') or element.get('aria_label') or ''):
			return str(element.get('ref'))
	return None


async def _wait_for_downloads(
	mcp_server: AgentycServer, *, expected_name: str, timeout_seconds: float = 5.0
) -> list[dict[str, object]]:
	deadline = asyncio.get_event_loop().time() + timeout_seconds
	while asyncio.get_event_loop().time() < deadline:
		raw = await mcp_server._get_downloads()
		if raw.startswith('Error') or raw.startswith('No files downloaded'):
			await asyncio.sleep(0.1)
			continue
		payload = json.loads(raw)
		if any(entry.get('name') == expected_name for entry in payload if isinstance(entry, dict)):
			return payload
		await asyncio.sleep(0.1)
	raise AssertionError(f'Expected download {expected_name!r}, got: {raw}')


async def test_headless_release_readiness_workflow(
	httpserver: HTTPServer,
	browser_session: BrowserSession,
	mcp_server,
	tmp_path,
):
	"""Battle-test a realistic headless release workflow across export, download, viewport, trace, and log tools."""
	httpserver.expect_request('/release-readiness').respond_with_data(_RELEASE_READINESS_HTML, content_type='text/html')
	httpserver.expect_request('/runbook').respond_with_data(_RUNBOOK_HTML, content_type='text/html')
	httpserver.expect_request('/release-status.json').respond_with_json({'summary': 'Release summary ready for export.'})
	httpserver.expect_request('/downloads/release-summary.txt').respond_with_data(
		'release summary downloaded by headless test\n',
		content_type='text/plain',
		headers={
			'Content-Disposition': 'attachment; filename="release-summary.txt"',
		},
	)

	mcp_server._file_system_base_dir = tmp_path
	mcp_server.file_system = FileSystem(base_dir=str(tmp_path))

	await mcp_server._register_cdp_event_listeners()
	base_url = f'http://127.0.0.1:{httpserver.port}'

	new_tab_result = await mcp_server._new_tab(url=f'{base_url}/release-readiness')
	assert not new_tab_result.startswith('Error'), f'new_tab failed: {new_tab_result}'
	assert f'{base_url}/release-readiness' in new_tab_result

	tab_payload = json.loads(await mcp_server._list_tabs())
	assert len(tab_payload['tabs']) >= 2

	viewport_result = await mcp_server._set_viewport(width=1024, height=720)
	assert '1024x720' in viewport_result

	stable_result = await mcp_server._wait_for_stable_dom(timeout_seconds=5.0, quiet_ms=250)
	assert not stable_result.startswith('Error'), f'wait_for_stable_dom failed: {stable_result}'
	assert 'stable' in stable_result.lower()

	viewport_width = await mcp_server._evaluate('(function(){ return window.innerWidth; })()')
	assert '1024' in viewport_width
	probe_fetch = await mcp_server._evaluate(
		'(async function(){ const response = await fetch("/release-status.json"); return response.status; })()'
	)
	assert '200' in probe_fetch
	await asyncio.sleep(0.1)

	state_text, _ = await mcp_server._get_browser_state(include_screenshot=False)
	state = json.loads(state_text)
	download_ref = _find_ref(state, 'Download release summary')
	runbook_ref = _find_ref(state, 'Open release runbook')
	assert download_ref is not None
	assert runbook_ref is not None

	download_href = await mcp_server._get_attribute(name='href', ref=download_ref)
	runbook_href = await mcp_server._get_attribute(name='href', ref=runbook_ref)
	assert '/downloads/release-summary.txt' in download_href
	assert '/runbook' in runbook_href

	trace_start = await mcp_server._start_trace()
	assert trace_start == 'Trace started'

	click_result = await mcp_server._click(ref=download_ref)
	assert not click_result.startswith('Error'), f'download click failed: {click_result}'

	downloads = await _wait_for_downloads(mcp_server, expected_name='release-summary.txt')
	assert any(entry.get('name') == 'release-summary.txt' for entry in downloads)

	trace_nav = await mcp_server._navigate(f'{base_url}/runbook')
	assert not trace_nav.startswith('Error'), f'trace navigation failed: {trace_nav}'
	await asyncio.sleep(0.2)

	trace_stop = await mcp_server._stop_trace()
	trace_events = json.loads(trace_stop)
	assert isinstance(trace_events, list)

	console_before = json.loads(await mcp_server._get_console_logs(max_entries=20))
	network_before = json.loads(await mcp_server._get_network_log(max_entries=20))
	assert any('release readiness page loaded' in str(entry.get('text', '')) for entry in console_before)
	assert any('/release-status.json' in str(entry.get('url', '')) for entry in network_before)

	clear_logs = await mcp_server._clear_logs(console=True, network=True)
	assert 'console' in clear_logs.lower()
	assert 'network' in clear_logs.lower()
	assert json.loads(await mcp_server._get_console_logs(max_entries=20)) == []
	assert json.loads(await mcp_server._get_network_log(max_entries=20)) == []

	pdf_result = await mcp_server._save_as_pdf(file_name='release-readiness.pdf')
	assert not pdf_result.startswith('Error'), f'save_as_pdf failed: {pdf_result}'
	assert 'release-readiness.pdf' in pdf_result

	pdf_path = tmp_path / 'agentyc_agent_data' / 'release-readiness.pdf'
	assert pdf_path.exists()
	assert pdf_path.stat().st_size > 100


async def test_headless_confirm_dialog_workflow_records_closed_popup_message(
	httpserver: HTTPServer,
	browser_session: BrowserSession,
	mcp_server,
):
	"""Headless confirm-dialog flows should succeed and leave a visible popup trail in browser state."""
	httpserver.expect_request('/confirm-dialog').respond_with_data(_CONFIRM_DIALOG_HTML, content_type='text/html')
	base_url = f'http://127.0.0.1:{httpserver.port}'

	nav_result = await mcp_server._navigate(f'{base_url}/confirm-dialog')
	assert not nav_result.startswith('Error'), f'Navigation failed: {nav_result}'

	state_text, _ = await mcp_server._get_browser_state(include_screenshot=False)
	state = json.loads(state_text)
	delete_ref = _find_ref(state, 'Delete branch')
	assert delete_ref is not None

	click_result = await mcp_server._click(ref=delete_ref)
	assert not click_result.startswith('Error'), f'Click failed: {click_result}'

	status_html = await mcp_server._get_html('#status')
	assert 'Deleted' in status_html
	handle_result = await mcp_server._handle_dialog(accept=True)
	assert not handle_result.startswith('Error'), f'handle_dialog should acknowledge auto-handled popup: {handle_result}'
	assert 'auto-handled' in handle_result
	assert 'Delete this branch?' in handle_result

	closed_messages: list[str] = []
	for _ in range(10):
		final_state_text, _ = await mcp_server._get_browser_state(include_screenshot=False)
		final_state = json.loads(final_state_text)
		closed_messages = (final_state.get('debug') or {}).get('closed_popup_messages') or []
		if closed_messages:
			break
		await asyncio.sleep(0.1)
	assert any('Delete this branch?' in str(message) for message in closed_messages)
