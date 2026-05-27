"""Headless MCP tests for network, frame, and storage tools."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from pytest_httpserver import HTTPServer
from werkzeug.wrappers import Response

from agentyc.browser import BrowserProfile, BrowserSession
from agentyc.mcp.server import AgentycServer
from agentyc.tools.service import Tools

_FRAME_PARENT_HTML = """
<!DOCTYPE html>
<html lang="en">
<head><title>Frame Storage Parent</title></head>
<body>
	<main>
		<h1>Frame Storage Parent</h1>
		<iframe name="child-frame" src="/iframe-child" style="width: 480px; height: 240px;"></iframe>
	</main>
</body>
</html>
"""

_FRAME_CHILD_HTML = """
<!DOCTYPE html>
<html lang="en">
<head><title>Frame Child Title</title></head>
<body>
	<main>
		<h2>Iframe child payload</h2>
		<p id="child-copy">Frame child body content</p>
	</main>
</body>
</html>
"""

_NETWORK_TOOLS_HTML = """
<!DOCTYPE html>
<html lang="en">
<head><title>Network Tools</title></head>
<body>
	<main>
		<button id="send-capture">Send capture request</button>
		<button id="load-mock">Load mock endpoint</button>
		<p id="capture-status">capture idle</p>
		<p id="mock-status">mock idle</p>
	</main>
	<script>
		document.getElementById('send-capture').addEventListener('click', async () => {
			const response = await fetch('/capture-api', {
				method: 'POST',
				headers: {
					'Content-Type': 'application/json',
					'X-Debug-Token': 'alpha'
				},
				body: JSON.stringify({message: 'hello', count: 1})
			});
			const payload = await response.json();
			document.getElementById('capture-status').textContent = payload.message + ':' + payload.token;
		});

		document.getElementById('load-mock').addEventListener('click', async () => {
			try {
				const response = await fetch('/mock-api');
				const text = await response.text();
				document.getElementById('mock-status').textContent = text;
			} catch (error) {
				document.getElementById('mock-status').textContent = 'error:' + (error && error.name ? error.name : String(error));
			}
		});
	</script>
</body>
</html>
"""


def _base_url(httpserver: HTTPServer) -> str:
	return f'http://127.0.0.1:{httpserver.port}'


async def _state_payload(server: AgentycServer) -> dict[str, Any]:
	state_json, _ = await server._get_browser_state(include_screenshot=False)
	return json.loads(state_json)


def _find_ref(payload: dict[str, Any], text: str) -> str:
	for element in payload.get('interactive_elements', []):
		if text in str(element.get('text') or element.get('aria_label') or ''):
			return str(element['ref'])
	raise AssertionError(f'Could not find interactive element with text {text!r}: {payload.get("interactive_elements", [])}')


async def _wait_for_frame(server: AgentycServer, *, url_suffix: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
	frames: list[dict[str, Any]] = []
	for _ in range(30):
		frames = json.loads(await server._execute_tool('browser_list_frames', {}))
		match = next((frame for frame in frames if str(frame.get('url') or '').endswith(url_suffix)), None)
		if match is not None:
			return frames, match
		await asyncio.sleep(0.1)
	raise AssertionError(f'Could not find frame ending with {url_suffix!r}: {frames}')


async def _wait_for_inspected_entry(server: AgentycServer, **arguments: Any) -> dict[str, Any]:
	last_payload: dict[str, Any] | None = None
	last_raw = ''
	for _ in range(40):
		raw = await server._execute_tool('browser_inspect_network_entry', arguments)
		last_raw = raw
		if not raw.startswith('Error'):
			last_payload = json.loads(raw)
			if 'response_body' in last_payload:
				return last_payload
		await asyncio.sleep(0.1)
	if last_payload is not None:
		raise AssertionError(f'Inspected entry never included response_body: {last_payload}')
	raise AssertionError(f'Could not inspect network entry: {last_raw}')


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


async def test_frame_and_storage_tools(httpserver: HTTPServer, mcp_server: AgentycServer):
	httpserver.expect_request('/frame-storage').respond_with_data(_FRAME_PARENT_HTML, content_type='text/html')
	httpserver.expect_request('/iframe-child').respond_with_data(_FRAME_CHILD_HTML, content_type='text/html')

	base_url = _base_url(httpserver)
	navigate_result = await mcp_server._navigate(f'{base_url}/frame-storage')
	assert not navigate_result.startswith('Error'), f'Navigation failed: {navigate_result}'

	frames, child_frame = await _wait_for_frame(mcp_server, url_suffix='/iframe-child')
	root_frame = next((frame for frame in frames if str(frame.get('url') or '').endswith('/frame-storage')), None)
	assert root_frame is not None, f'Expected root frame in: {frames}'
	assert child_frame.get('parent_frame_id') == root_frame['frame_id']
	assert child_frame.get('name') == 'child-frame'
	assert child_frame.get('is_cross_origin') is False

	child_html = await mcp_server._get_frame_html(str(child_frame['frame_id']))
	assert 'Frame Child Title' in child_html
	assert 'Frame child body content' in child_html

	missing_frame_result = await mcp_server._get_frame_html('missing-frame-id')
	assert missing_frame_result.startswith('Error [not_found]:')

	origin = base_url
	set_local = json.loads(
		await mcp_server._set_storage(origin=origin, storage_type='localStorage', key='release', value='train')
	)
	set_session = json.loads(
		await mcp_server._set_storage(origin=origin, storage_type='sessionStorage', key='panel', value='network')
	)
	assert set_local['ok'] is True
	assert set_session['ok'] is True

	storage_payload = json.loads(await mcp_server._execute_tool('browser_get_storage', {'origin': origin}))
	assert len(storage_payload) == 1
	assert any(item == {'name': 'release', 'value': 'train'} for item in storage_payload[0].get('localStorage', []))
	assert any(item == {'name': 'panel', 'value': 'network'} for item in storage_payload[0].get('sessionStorage', []))

	filtered_payload = json.loads(
		await mcp_server._execute_tool(
			'browser_get_storage',
			{'origin': origin, 'storage_type': 'localStorage', 'key': 'release'},
		)
	)
	assert filtered_payload == [{'origin': origin, 'localStorage': [{'name': 'release', 'value': 'train'}]}]

	clear_key = json.loads(await mcp_server._clear_storage(origin=origin, storage_type='localStorage', key='release'))
	assert clear_key['ok'] is True
	assert json.loads(await mcp_server._get_storage(origin=origin, key='release')) == []

	clear_all = json.loads(await mcp_server._clear_storage(origin=origin))
	assert clear_all['ok'] is True
	assert clear_all['storage_type'] == 'all'
	assert json.loads(await mcp_server._get_storage(origin=origin)) == []


async def test_inspect_network_entry_and_replay_request(httpserver: HTTPServer, mcp_server: AgentycServer):
	captured_requests: list[dict[str, Any]] = []

	def capture_handler(request) -> Response:
		payload = request.get_json(silent=True) or {}
		recorded = {
			'message': payload.get('message'),
			'count': payload.get('count'),
			'token': request.headers.get('X-Debug-Token'),
			'replay': request.headers.get('X-Replay'),
		}
		captured_requests.append(recorded)
		return Response(
			json.dumps(recorded),
			status=202,
			content_type='application/json',
			headers={'X-Recorded': 'yes'},
		)

	httpserver.expect_request('/network-tools').respond_with_data(_NETWORK_TOOLS_HTML, content_type='text/html')
	httpserver.expect_request('/capture-api', method='POST').respond_with_handler(capture_handler)

	base_url = _base_url(httpserver)
	await mcp_server._register_cdp_event_listeners()
	navigate_result = await mcp_server._navigate(f'{base_url}/network-tools')
	assert not navigate_result.startswith('Error'), f'Navigation failed: {navigate_result}'

	state = await _state_payload(mcp_server)
	capture_ref = _find_ref(state, 'Send capture request')
	response_task = asyncio.create_task(
		mcp_server._wait_for_response(
			url_substring='/capture-api',
			method='POST',
			status=202,
			include_headers=True,
			timeout_seconds=5.0,
		)
	)
	click_result = await mcp_server._click(ref=capture_ref)
	assert not click_result.startswith('Error'), f'Click failed: {click_result}'

	response_payload = json.loads(await response_task)
	assert response_payload['status'] == 202
	assert response_payload['url'].endswith('/capture-api')

	inspect_payload = await _wait_for_inspected_entry(
		mcp_server,
		url_substring='/capture-api',
		method='POST',
		include_headers=True,
	)
	request_header_keys = {str(key).lower() for key in (inspect_payload.get('req_headers') or {}).keys()}
	response_header_keys = {str(key).lower() for key in (inspect_payload.get('resp_headers') or {}).keys()}
	assert inspect_payload['request_id']
	assert inspect_payload['target_tab_id']
	assert inspect_payload['request_body']['json'] == {'message': 'hello', 'count': 1}
	assert inspect_payload['response_body']['json'] == {'message': 'hello', 'count': 1, 'token': 'alpha', 'replay': None}
	assert 'x-debug-token' in request_header_keys
	assert 'x-recorded' in response_header_keys

	replay_result = json.loads(
		await mcp_server._replay_request(
			request_id=str(inspect_payload['request_id']),
			body=json.dumps({'message': 'replayed', 'count': 2}),
			headers={
				'Content-Type': 'application/json',
				'X-Debug-Token': 'beta',
				'X-Replay': 'yes',
			},
		)
	)
	replay_body = json.loads(replay_result['body'])
	assert replay_result['status'] == 202
	assert replay_result['ok'] is True
	assert replay_body == {'message': 'replayed', 'count': 2, 'token': 'beta', 'replay': 'yes'}
	assert len(captured_requests) >= 2
	assert captured_requests[0] == {'message': 'hello', 'count': 1, 'token': 'alpha', 'replay': None}
	assert captured_requests[-1] == {'message': 'replayed', 'count': 2, 'token': 'beta', 'replay': 'yes'}


async def test_network_mock_and_conditions_tools(httpserver: HTTPServer, mcp_server: AgentycServer):
	httpserver.expect_request('/network-tools').respond_with_data(_NETWORK_TOOLS_HTML, content_type='text/html')
	httpserver.expect_request('/mock-api').respond_with_data('real response', content_type='text/plain')
	httpserver.expect_request('/conditions-api').respond_with_data('conditions ok', content_type='text/plain')

	base_url = _base_url(httpserver)
	navigate_result = await mcp_server._navigate(f'{base_url}/network-tools')
	assert not navigate_result.startswith('Error'), f'Navigation failed: {navigate_result}'

	add_mock_payload = json.loads(
		await mcp_server._execute_tool(
			'browser_add_network_mock',
			{
				'url_substring': '/mock-api',
				'action': 'fulfill',
				'status': 200,
				'headers': {'Content-Type': 'text/plain'},
				'body': 'mocked response',
			},
		)
	)
	mock_id = add_mock_payload['mock_id']
	list_before = json.loads(await mcp_server._execute_tool('browser_list_network_mocks', {}))
	assert len(list_before) == 1
	assert list_before[0]['mock_id'] == mock_id
	assert list_before[0]['match_count'] == 0

	mocked_result = await mcp_server._evaluate(
		"""(async function() {
			const response = await fetch('/mock-api');
			return await response.text();
		})()"""
	)
	assert 'mocked response' in mocked_result

	list_after = list_before
	for _ in range(20):
		list_after = json.loads(await mcp_server._execute_tool('browser_list_network_mocks', {}))
		if list_after and list_after[0].get('match_count', 0) >= 1:
			break
		await asyncio.sleep(0.1)
	assert list_after[0]['match_count'] >= 1

	remove_payload = json.loads(await mcp_server._execute_tool('browser_remove_network_mock', {'mock_id': mock_id}))
	assert remove_payload['removed'] == 1
	assert json.loads(await mcp_server._execute_tool('browser_list_network_mocks', {})) == []

	real_result = await mcp_server._evaluate(
		"""(async function() {
			const response = await fetch('/mock-api');
			return await response.text();
		})()"""
	)
	assert 'real response' in real_result

	conditions_payload = json.loads(
		await mcp_server._execute_tool(
			'browser_set_network_conditions',
			{'offline': True, 'latency_ms': 5.0},
		)
	)
	assert conditions_payload['offline'] is True
	assert conditions_payload['reset'] is False

	condition_list = json.loads(await mcp_server._execute_tool('browser_get_network_conditions', {}))
	assert len(condition_list) == 1
	assert condition_list[0]['offline'] is True

	offline_result = await mcp_server._evaluate(
		"""(async function() {
			try {
				await fetch('/conditions-api');
				return 'unexpected-success';
			} catch (error) {
				return 'offline:' + (error && error.name ? error.name : String(error));
			}
		})()"""
	)
	assert 'offline:' in offline_result
	assert 'unexpected-success' not in offline_result

	reset_payload = json.loads(await mcp_server._execute_tool('browser_set_network_conditions', {'reset': True}))
	assert reset_payload['reset'] is True
	assert reset_payload['offline'] is False
	assert json.loads(await mcp_server._execute_tool('browser_get_network_conditions', {})) == []

	await asyncio.sleep(0.2)
	online_result = await mcp_server._evaluate(
		"""(async function() {
			const response = await fetch('/conditions-api');
			return await response.text();
		})()"""
	)
	assert 'conditions ok' in online_result
