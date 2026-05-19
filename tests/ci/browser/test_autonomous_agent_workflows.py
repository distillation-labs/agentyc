from __future__ import annotations

import json
from pathlib import Path

import pytest
from pytest_httpserver import HTTPServer

from agentyc.browser import BrowserProfile, BrowserSession
from agentyc.mcp.server import AgentycServer
from agentyc.tools.service import Tools

_WORKSPACE_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
	<meta charset="utf-8">
	<title>Release workspace</title>
	<link rel="icon" href="data:,">
</head>
<body>
	<main>
		<h1>Release workspace</h1>
		<p id="auth-status">Loading workspace...</p>
	</main>
	<script>
		function getCookie(name) {
			const pair = document.cookie.split('; ').find((item) => item.startsWith(name + '='));
			return pair ? pair.slice(name.length + 1) : '';
		}
		const token = getCookie('agent_session');
		const workspace = window.localStorage.getItem('workspace') || 'missing';
		const lastView = window.sessionStorage.getItem('last_view') || 'missing';
		document.getElementById('auth-status').textContent = token
			? `Authenticated ${token} workspace=${workspace} view=${lastView}`
			: `Signed out workspace=${workspace} view=${lastView}`;
	</script>
</body>
</html>
"""

_DEPLOY_FORM_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
	<meta charset="utf-8">
	<title>Deployment form</title>
	<link rel="icon" href="data:,">
</head>
<body>
	<main>
		<h1>Deployment form</h1>
		<label>
			Release title
			<input id="release-title" aria-label="Release title" type="text">
		</label>
		<label>
			Environment
			<select id="environment" aria-label="Environment">
				<option selected>Choose environment</option>
				<option>Preview</option>
				<option>Production</option>
				<option>Canary</option>
			</select>
		</label>
		<label>
			Deployment notes
			<textarea id="notes" aria-label="Deployment notes"></textarea>
		</label>
		<button id="queue" aria-label="Queue deployment">Queue deployment</button>
		<p id="result">Idle</p>
	</main>
	<script>
		document.getElementById('queue').addEventListener('click', () => {
			const title = document.getElementById('release-title').value.trim();
			const environment = document.getElementById('environment').value;
			const notes = document.getElementById('notes').value.trim();
			document.getElementById('result').textContent = `${title}|${environment}|${notes}`;
		});
	</script>
</body>
</html>
"""

_DOCS_HOME_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
	<meta charset="utf-8">
	<title>Docs home</title>
	<link rel="icon" href="data:,">
</head>
<body>
	<main>
		<h1>Docs home</h1>
		<button id="docs-menu" aria-label="Docs menu">Docs menu</button>
		<nav id="docs-popover" hidden>
			<a href="/docs/release-checklist">Release checklist</a>
			<a href="/docs/runbook">Incident runbook</a>
		</nav>
	</main>
	<script>
		const menu = document.getElementById('docs-menu');
		const popover = document.getElementById('docs-popover');
		function openMenu() {
			popover.hidden = false;
		}
		menu.addEventListener('mouseenter', openMenu);
		menu.addEventListener('mouseover', openMenu);
	</script>
</body>
</html>
"""

_RELEASE_CHECKLIST_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
	<meta charset="utf-8">
	<title>Release checklist</title>
	<link rel="icon" href="data:,">
	<style>
		section {
			margin-bottom: 24px;
		}
	</style>
</head>
<body>
	<main>
		<h1>Release checklist</h1>
		<section>
			<h2>Preflight</h2>
			<p>Validate the build, smoke tests, and monitoring dashboards before release.</p>
		</section>
		<div style="height: 1600px;" aria-hidden="true">Spacer for a long docs page.</div>
		<section id="rollback-step-3">
			<h2>Rollback step 3</h2>
			<p>Rollback step 3: Re-enable the previous build before widening traffic again.</p>
			<p>Rollback window: 15 minutes after the deploy is declared unhealthy.</p>
		</section>
	</main>
</body>
</html>
"""

_KANBAN_BOARD_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
	<meta charset="utf-8">
	<title>Release board</title>
	<link rel="icon" href="data:,">
	<style>
		.lane {
			display: inline-block;
			width: 280px;
			min-height: 200px;
			margin-right: 24px;
			padding: 16px;
			border: 1px solid #d1d5db;
			vertical-align: top;
		}
		.card {
			margin-top: 12px;
			padding: 12px;
			border: 1px solid #4b5563;
			background: #f9fafb;
		}
		.drop-target {
			margin-top: 12px;
			padding: 12px;
			border: 1px dashed #2563eb;
		}
	</style>
</head>
<body>
	<main>
		<h1>Release board</h1>
		<section class="lane" id="todo-lane">
			<h2>To do</h2>
			<div id="card-ci" class="card" role="button" tabindex="0" aria-label="Investigate flaky CI">Investigate flaky CI</div>
		</section>
		<section class="lane" id="done-lane">
			<h2>Done</h2>
			<div id="done-dropzone" class="drop-target" role="button" tabindex="0" aria-label="Done lane">Done lane</div>
		</section>
		<p id="status">Idle</p>
		<aside id="details">No card selected</aside>
	</main>
	<script>
		let draggingCard = null;
		const card = document.getElementById('card-ci');
		const status = document.getElementById('status');
		const details = document.getElementById('details');
		const doneLane = document.getElementById('done-lane');
		const doneDropzone = document.getElementById('done-dropzone');

		card.addEventListener('mousedown', () => {
			draggingCard = card;
		});

		card.addEventListener('dblclick', () => {
			details.textContent = `Opened details for ${card.textContent.trim()}`;
		});

		card.addEventListener('contextmenu', (event) => {
			event.preventDefault();
			status.textContent = `Context menu for ${card.textContent.trim()}`;
		});

		doneDropzone.addEventListener('mouseup', () => {
			if (!draggingCard) {
				return;
			}
			doneLane.appendChild(draggingCard);
			status.textContent = `Moved ${draggingCard.textContent.trim()} to Done`;
			draggingCard = null;
		});
	</script>
</body>
</html>
"""


def _base_url(httpserver: HTTPServer) -> str:
	return f'http://127.0.0.1:{httpserver.port}'


def _respond_with_html(httpserver: HTTPServer, path: str, html: str, repeat: int = 1) -> None:
	for _ in range(repeat):
		httpserver.expect_request(path).respond_with_data(html, content_type='text/html')


def _build_server(browser_session: BrowserSession) -> AgentycServer:
	server = AgentycServer()
	server.browser_session = browser_session
	server.tools = Tools()
	server._update_session_activity = lambda *_args, **_kwargs: None
	return server


async def _start_browser_session() -> BrowserSession:
	session = BrowserSession(
		browser_profile=BrowserProfile(
			headless=True,
			user_data_dir=None,
		)
	)
	await session.start()
	return session


async def _get_state_payload(server: AgentycServer, mode: str = 'auto') -> dict:
	state_json, _ = await server._get_browser_state(mode=mode)
	return json.loads(state_json)


def _element_ref(payload: dict, *, text: str | None = None, tag: str | None = None) -> str:
	matches = []
	for element in payload['interactive_elements']:
		if text is not None and text not in (element.get('text') or ''):
			continue
		if tag is not None and element.get('tag') != tag:
			continue
		matches.append(element)
	assert matches, f'No interactive element matched text={text!r} tag={tag!r}: {payload["interactive_elements"]}'
	return matches[0]['ref']


@pytest.fixture
async def browser_session():
	session = await _start_browser_session()
	yield session
	await session.stop()


async def test_public_mcp_auth_state_persistence_across_sessions(
	httpserver: HTTPServer,
	tmp_path: Path,
):
	_respond_with_html(httpserver, '/workspace', _WORKSPACE_HTML, repeat=4)
	base_url = _base_url(httpserver)
	state_path = tmp_path / 'workspace-state.json'

	first_session = await _start_browser_session()
	first_server = _build_server(first_session)
	second_session: BrowserSession | None = None
	try:
		navigate_result = await first_server._navigate(f'{base_url}/workspace')
		assert not navigate_result.startswith('Error')

		set_cookie_result = await first_server._set_cookies(
			[
				{
					'name': 'agent_session',
					'value': 'token-123',
					'domain': '127.0.0.1',
					'path': '/',
				}
			]
		)
		assert set_cookie_result == 'Set 1 cookie(s): agent_session'

		storage_result = await first_server._evaluate(
			"""(function() {
				window.localStorage.setItem('workspace', 'release-train');
				window.sessionStorage.setItem('last_view', 'release-board');
				return JSON.stringify({
					workspace: window.localStorage.getItem('workspace'),
					lastView: window.sessionStorage.getItem('last_view')
				});
			})()"""
		)
		assert 'release-train' in storage_result
		assert 'release-board' in storage_result

		refresh_result = await first_server._refresh()
		assert refresh_result == f'Refreshed page: {base_url}/workspace'

		auth_status_html = await first_server._get_html('#auth-status')
		assert 'Authenticated token-123 workspace=release-train view=release-board' in auth_status_html

		cookies_result = await first_server._get_cookies()
		cookies = json.loads(cookies_result)
		assert any(cookie['name'] == 'agent_session' and cookie['value'] == 'token-123' for cookie in cookies)

		save_result = await first_server._save_state(str(state_path))
		assert save_result == f'Browser state saved to: {state_path}'
		assert state_path.exists()
	finally:
		await first_session.stop()

	second_session = await _start_browser_session()
	second_server = _build_server(second_session)
	try:
		load_result = await second_server._load_state(str(state_path))
		assert load_result == f'Browser state loaded from: {state_path}'

		navigate_result = await second_server._navigate(f'{base_url}/workspace')
		assert not navigate_result.startswith('Error')

		auth_status_html = await second_server._get_html('#auth-status')
		assert 'Authenticated token-123 workspace=release-train view=release-board' in auth_status_html

		cookies_result = await second_server._get_cookies()
		cookies = json.loads(cookies_result)
		assert any(cookie['name'] == 'agent_session' and cookie['value'] == 'token-123' for cookie in cookies)

		clear_result = await second_server._clear_cookies(name='agent_session')
		assert clear_result == 'Deleted cookie: agent_session'

		refresh_result = await second_server._refresh()
		assert refresh_result == f'Refreshed page: {base_url}/workspace'

		auth_status_html = await second_server._get_html('#auth-status')
		assert 'Signed out workspace=release-train view=release-board' in auth_status_html
	finally:
		if second_session is not None:
			await second_session.stop()


async def test_public_mcp_keyboard_dropdown_workflow(httpserver: HTTPServer, browser_session: BrowserSession):
	_respond_with_html(httpserver, '/deploy-form', _DEPLOY_FORM_HTML)
	base_url = _base_url(httpserver)
	server = _build_server(browser_session)

	navigate_result = await server._navigate(f'{base_url}/deploy-form')
	assert not navigate_result.startswith('Error')

	payload = await _get_state_payload(server)
	title_ref = _element_ref(payload, text='Release title')
	environment_ref = _element_ref(payload, tag='select')
	notes_ref = _element_ref(payload, tag='textarea')
	queue_ref = _element_ref(payload, text='Queue deployment')

	type_result = await server._type_text(ref=title_ref, text='Release 2.4.0')
	assert not type_result.startswith('Error')

	tab_to_select = await server._press_key('Tab')
	assert tab_to_select == 'Pressed key: Tab'

	focused_select = json.loads(await server._get_focused_element())
	assert focused_select['tag'] == 'select'
	assert focused_select['ariaLabel'] == 'Environment'

	options_result = await server._get_dropdown_options(ref=environment_ref)
	assert 'Choose environment' in options_result
	assert 'Preview' in options_result
	assert 'Production' in options_result
	assert 'Canary' in options_result

	select_result = await server._select_option(ref=environment_ref, text='Production')
	assert 'Production' in select_result

	tab_to_notes = await server._press_key('Tab')
	assert tab_to_notes == 'Pressed key: Tab'

	focused_notes = json.loads(await server._get_focused_element())
	assert focused_notes['tag'] == 'textarea'
	assert focused_notes['ariaLabel'] == 'Deployment notes'

	notes_result = await server._type_text(ref=notes_ref, text='Run smoke suite first')
	assert not notes_result.startswith('Error')

	click_result = await server._click(ref=queue_ref)
	assert not click_result.startswith('Error')

	result_html = await server._get_html('#result')
	assert 'Release 2.4.0|Production|Run smoke suite first' in result_html


async def test_public_mcp_hover_search_and_scroll_workflow(httpserver: HTTPServer, browser_session: BrowserSession):
	_respond_with_html(httpserver, '/docs', _DOCS_HOME_HTML)
	_respond_with_html(httpserver, '/docs/release-checklist', _RELEASE_CHECKLIST_HTML)
	base_url = _base_url(httpserver)
	server = _build_server(browser_session)

	navigate_result = await server._navigate(f'{base_url}/docs')
	assert not navigate_result.startswith('Error')

	payload = await _get_state_payload(server)
	menu_ref = _element_ref(payload, text='Docs menu')

	hover_result = await server._hover(ref=menu_ref)
	assert hover_result.startswith('Hovered over ')

	wait_result = await server._wait_for_element(text='Release checklist', timeout_seconds=2)
	assert wait_result.startswith('Element "Release checklist" appeared after ')

	payload = await _get_state_payload(server)
	checklist_ref = _element_ref(payload, text='Release checklist')

	click_result = await server._click(ref=checklist_ref)
	assert not click_result.startswith('Error')

	search_result = await server._search_page(pattern='Rollback window')
	assert 'Rollback window' in search_result
	assert '15 minutes' in search_result

	scroll_result = await server._scroll_to_text('Rollback step 3')
	assert scroll_result == "Scrolled to text: 'Rollback step 3'"

	section_html = await server._get_html('#rollback-step-3')
	assert 'Re-enable the previous build before widening traffic again.' in section_html


async def test_public_mcp_drag_context_menu_and_double_click_workflow(
	httpserver: HTTPServer,
	browser_session: BrowserSession,
):
	_respond_with_html(httpserver, '/board', _KANBAN_BOARD_HTML)
	base_url = _base_url(httpserver)
	server = _build_server(browser_session)

	navigate_result = await server._navigate(f'{base_url}/board')
	assert not navigate_result.startswith('Error')

	payload = await _get_state_payload(server)
	card_ref = _element_ref(payload, text='Investigate flaky CI')
	done_ref = _element_ref(payload, text='Done lane')

	drag_result = await server._drag_to(source_ref=card_ref, target_ref=done_ref)
	assert drag_result.startswith('Dragged from (')

	done_html = await server._get_html('#done-lane')
	assert 'Investigate flaky CI' in done_html
	assert 'Moved Investigate flaky CI to Done' in await server._get_html('#status')

	payload = await _get_state_payload(server)
	card_ref = _element_ref(payload, text='Investigate flaky CI')

	right_click_result = await server._right_click(ref=card_ref)
	assert right_click_result.startswith('Right-clicked at (')
	assert 'Context menu for Investigate flaky CI' in await server._get_html('#status')

	double_click_result = await server._double_click(ref=card_ref)
	assert double_click_result.startswith('Double-clicked ')

	details_html = await server._get_html('#details')
	assert 'Opened details for Investigate flaky CI' in details_html
