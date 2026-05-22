from __future__ import annotations

import asyncio
import json

import pytest
from pytest_httpserver import HTTPServer

from agentyc.browser import BrowserProfile, BrowserSession
from agentyc.mcp.server import AgentycServer
from agentyc.tools.service import Tools

_AUTH_BOOTSTRAP_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
	<meta charset="utf-8">
	<title>Shared auth bootstrap</title>
	<link rel="icon" href="data:,">
</head>
<body>
	<main>
		<h1>Shared auth bootstrap</h1>
		<label>
			Workspace user
			<input id="workspace-user" aria-label="Workspace user" type="text" value="release-bot">
		</label>
		<button id="login" aria-label="Sign in shared workspace">Sign in shared workspace</button>
		<p id="status">Signed out</p>
	</main>
	<script>
		document.getElementById('login').addEventListener('click', () => {
			const user = document.getElementById('workspace-user').value.trim() || 'missing';
			document.cookie = 'agent_session=token-123; path=/';
			window.localStorage.setItem('workspace_user', user);
			document.getElementById('status').textContent = `Signed in ${user}`;
		});
	</script>
</body>
</html>
"""

_AUTH_STATUS_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
	<meta charset="utf-8">
	<title>Shared auth status</title>
	<link rel="icon" href="data:,">
</head>
<body>
	<main>
		<h1>Shared auth status</h1>
		<p id="auth-status">Loading…</p>
	</main>
	<script>
		function getCookie(name) {
			const pair = document.cookie.split('; ').find((item) => item.startsWith(name + '='));
			return pair ? pair.slice(name.length + 1) : '';
		}
		const token = getCookie('agent_session') || 'missing';
		const workspaceUser = window.localStorage.getItem('workspace_user') || 'missing';
		document.getElementById('auth-status').textContent = `Authenticated ${token} workspace=${workspaceUser}`;
	</script>
</body>
</html>
"""


def _workspace_html(name: str) -> str:
	return f"""
<!DOCTYPE html>
<html lang="en">
<head>
	<meta charset="utf-8">
	<title>{name} workspace</title>
	<link rel="icon" href="data:,">
</head>
<body>
	<main>
		<h1>{name} workspace</h1>
		<label>
			{name} task
			<input
				id="{name.lower()}-task"
				aria-label="{name} task"
				placeholder="{name.lower()}@example.com"
				type="text"
			>
		</label>
		<button id="{name.lower()}-save" aria-label="Save {name} task">Save {name} task</button>
		<p id="status">{name} idle</p>
	</main>
	<script>
		document.getElementById('{name.lower()}-save').addEventListener('click', () => {{
			const value = document.getElementById('{name.lower()}-task').value.trim() || '{name} idle';
			document.getElementById('status').textContent = value;
		}});
	</script>
</body>
</html>
"""


def _base_url(server: HTTPServer) -> str:
	return f'http://127.0.0.1:{server.port}'


def _respond_with_html(server: HTTPServer, path: str, html: str, *, repeat: int = 6) -> None:
	for _ in range(repeat):
		server.expect_request(path).respond_with_data(html, content_type='text/html')


def _build_primary_server(browser_session: BrowserSession, *, cdp_url: str, runtime_label: str) -> AgentycServer:
	server = AgentycServer(cdp_url=cdp_url, runtime_label=runtime_label)
	server.browser_session = browser_session
	server.tools = Tools()
	server.tools.set_coordinate_clicking(True)
	server._update_session_activity = lambda *_args, **_kwargs: None
	return server


async def _start_attached_server(*, cdp_url: str, runtime_label: str) -> AgentycServer:
	server = AgentycServer(cdp_url=cdp_url, runtime_label=runtime_label)
	await server._init_browser_session(headless=True, user_data_dir=None)
	assert server.tools is not None
	server.tools.set_coordinate_clicking(True)
	server._update_session_activity = lambda *_args, **_kwargs: None
	return server


async def _get_state_payload(server: AgentycServer) -> dict:
	state_json, _ = await server._get_browser_state(include_screenshot=False)
	return json.loads(state_json)


async def _wait_for_state_payload(server: AgentycServer, *, require_interactive: bool = False) -> dict:
	payload: dict = {}
	for _ in range(30):
		payload = await _get_state_payload(server)
		if require_interactive:
			if payload.get('interactive_elements'):
				return payload
		elif payload.get('tabs'):
			return payload
		await asyncio.sleep(0.1)
	return payload


def _element_ref(payload: dict, *, text: str | None = None, placeholder: str | None = None) -> str:
	for element in payload['interactive_elements']:
		if text is not None and text not in (element.get('text') or ''):
			continue
		if placeholder is not None and placeholder != element.get('placeholder'):
			continue
		return element['ref']
	raise AssertionError(
		f'No interactive element matched text={text!r} placeholder={placeholder!r}: {payload["interactive_elements"]}'
	)


def _assert_distinct_owned_tabs(*payloads: dict) -> None:
	tab_ids = {payload['current_tab']['tab_id'] for payload in payloads}
	assert len(tab_ids) == len(payloads), f'Expected unique current tabs, got {tab_ids}'
	for payload in payloads:
		visible_tab_ids = {tab.get('tab_id') for tab in payload['tabs']}
		assert tab_ids.issubset(visible_tab_ids), f'Payload did not surface all runtime tabs: {visible_tab_ids}'


@pytest.fixture
def threaded_httpserver():
	server = HTTPServer(threaded=True)
	server.start()
	yield server
	server.stop()


async def test_attached_subagents_get_dedicated_tabs_and_share_auth_state(threaded_httpserver: HTTPServer) -> None:
	_respond_with_html(threaded_httpserver, '/auth-bootstrap', _AUTH_BOOTSTRAP_HTML, repeat=4)
	_respond_with_html(threaded_httpserver, '/auth-status', _AUTH_STATUS_HTML, repeat=8)
	base_url = _base_url(threaded_httpserver)

	primary_session = BrowserSession(
		browser_profile=BrowserProfile(
			headless=True,
			user_data_dir=None,
			keep_alive=True,
		)
	)
	await primary_session.start()

	try:
		cdp_url = primary_session.browser_profile.cdp_url
		assert cdp_url

		server_a = _build_primary_server(primary_session, cdp_url=cdp_url, runtime_label='Primary')
		server_b = await _start_attached_server(cdp_url=cdp_url, runtime_label='Subagent B')
		server_c = await _start_attached_server(cdp_url=cdp_url, runtime_label='Subagent C')

		try:
			navigate_result = await server_a._navigate(f'{base_url}/auth-bootstrap')
			assert not navigate_result.startswith('Error')

			state_a = await _wait_for_state_payload(server_a, require_interactive=True)
			sign_in_ref = _element_ref(state_a, text='Sign in shared workspace')
			click_result = await server_a._click(ref=sign_in_ref)
			assert not click_result.startswith('Error')

			nav_b, nav_c = await asyncio.gather(
				server_b._navigate(f'{base_url}/auth-status'),
				server_c._navigate(f'{base_url}/auth-status'),
			)
			assert not nav_b.startswith('Error'), nav_b
			assert not nav_c.startswith('Error'), nav_c

			state_a = await _wait_for_state_payload(server_a)
			state_b = await _wait_for_state_payload(server_b)
			state_c = await _wait_for_state_payload(server_c)

			_assert_distinct_owned_tabs(state_a, state_b, state_c)

			auth_html_b = await server_b._get_html('#auth-status')
			auth_html_c = await server_c._get_html('#auth-status')

			assert 'Authenticated token-123 workspace=release-bot' in auth_html_b
			assert 'Authenticated token-123 workspace=release-bot' in auth_html_c
		finally:
			await server_b._shutdown()
			await server_c._shutdown()
	finally:
		await primary_session.kill()


async def test_three_parallel_subagents_operate_independently_on_owned_tabs(threaded_httpserver: HTTPServer) -> None:
	_respond_with_html(threaded_httpserver, '/workspace-alpha', _workspace_html('Alpha'))
	_respond_with_html(threaded_httpserver, '/workspace-beta', _workspace_html('Beta'))
	_respond_with_html(threaded_httpserver, '/workspace-gamma', _workspace_html('Gamma'))
	base_url = _base_url(threaded_httpserver)

	primary_session = BrowserSession(
		browser_profile=BrowserProfile(
			headless=True,
			user_data_dir=None,
			keep_alive=True,
		)
	)
	await primary_session.start()

	try:
		cdp_url = primary_session.browser_profile.cdp_url
		assert cdp_url

		server_a = _build_primary_server(primary_session, cdp_url=cdp_url, runtime_label='Primary')
		server_b = await _start_attached_server(cdp_url=cdp_url, runtime_label='Subagent B')
		server_c = await _start_attached_server(cdp_url=cdp_url, runtime_label='Subagent C')

		try:
			nav_a, nav_b, nav_c = await asyncio.gather(
				server_a._navigate(f'{base_url}/workspace-alpha'),
				server_b._navigate(f'{base_url}/workspace-beta'),
				server_c._navigate(f'{base_url}/workspace-gamma'),
			)
			assert not nav_a.startswith('Error'), nav_a
			assert not nav_b.startswith('Error'), nav_b
			assert not nav_c.startswith('Error'), nav_c

			state_a = await _wait_for_state_payload(server_a, require_interactive=True)
			state_b = await _wait_for_state_payload(server_b, require_interactive=True)
			state_c = await _wait_for_state_payload(server_c, require_interactive=True)

			alpha_input_ref = _element_ref(state_a, placeholder='alpha@example.com')
			alpha_save_ref = _element_ref(state_a, text='Save Alpha task')
			beta_input_ref = _element_ref(state_b, placeholder='beta@example.com')
			beta_save_ref = _element_ref(state_b, text='Save Beta task')
			gamma_input_ref = _element_ref(state_c, placeholder='gamma@example.com')
			gamma_save_ref = _element_ref(state_c, text='Save Gamma task')

			type_a, type_b, type_c = await asyncio.gather(
				server_a._type_text(ref=alpha_input_ref, text='alpha-task'),
				server_b._type_text(ref=beta_input_ref, text='beta-task'),
				server_c._type_text(ref=gamma_input_ref, text='gamma-task'),
			)
			assert not type_a.startswith('Error'), type_a
			assert not type_b.startswith('Error'), type_b
			assert not type_c.startswith('Error'), type_c

			click_a, click_b, click_c = await asyncio.gather(
				server_a._click(ref=alpha_save_ref),
				server_b._click(ref=beta_save_ref),
				server_c._click(ref=gamma_save_ref),
			)
			assert not click_a.startswith('Error'), click_a
			assert not click_b.startswith('Error'), click_b
			assert not click_c.startswith('Error'), click_c

			_assert_distinct_owned_tabs(
				await _get_state_payload(server_a),
				await _get_state_payload(server_b),
				await _get_state_payload(server_c),
			)

			status_a = await server_a._get_html('#status')
			status_b = await server_b._get_html('#status')
			status_c = await server_c._get_html('#status')

			assert 'alpha-task' in status_a
			assert 'beta-task' in status_b
			assert 'gamma-task' in status_c
			assert 'beta-task' not in status_a and 'gamma-task' not in status_a
			assert 'alpha-task' not in status_b and 'gamma-task' not in status_b
			assert 'alpha-task' not in status_c and 'beta-task' not in status_c
		finally:
			await server_b._shutdown()
			await server_c._shutdown()
	finally:
		await primary_session.kill()
