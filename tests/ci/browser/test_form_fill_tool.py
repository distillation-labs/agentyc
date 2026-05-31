"""Integration tests for the batched browser_fill_form MCP tool."""

from __future__ import annotations

import json

import pytest
from pytest_httpserver import HTTPServer

from agentyc.browser import BrowserProfile, BrowserSession
from agentyc.mcp.server import AgentycServer
from agentyc.tools.service import Tools

_FORM_PAGE = """
<!DOCTYPE html>
<html lang="en">
<head><title>Form fill tool</title></head>
<body>
  <main>
    <form id="profile-form">
      <label>Full name <input id="full-name" name="fullName" type="text" /></label>
      <label>Email address <input id="email" name="email" type="email" /></label>
      <label>Company <input id="company" name="company" type="text" /></label>
      <label>Role
        <select id="role" name="role">
          <option value="">Choose role</option>
          <option>Engineer</option>
          <option>Designer</option>
          <option>Operator</option>
        </select>
      </label>
      <button type="submit">Save profile</button>
    </form>
    <div id="status">Idle</div>
  </main>
  <script>
    document.getElementById('profile-form').addEventListener('submit', (event) => {
      event.preventDefault();
      const data = new FormData(event.target);
      document.getElementById('status').textContent = JSON.stringify({
        fullName: data.get('fullName'),
        email: data.get('email'),
        company: data.get('company'),
        role: data.get('role'),
      });
    });
  </script>
</body>
</html>
"""


_FORM_PAGE_WITH_TOGGLES = """
<!DOCTYPE html>
<html lang="en">
<head><title>Form fill toggles</title></head>
<body>
  <main>
    <form id="deploy-form">
      <label>Project name <input id="project-name" aria-label="Project name" name="projectName" type="text" /></label>
      <label>Repository URL <input id="repository-url" aria-label="Repository URL" name="repositoryUrl" type="url" /></label>
      <label>Environment
        <select id="environment" aria-label="Environment" name="environment">
          <option value="">Choose environment</option>
          <option>Preview</option>
          <option>Production</option>
        </select>
      </label>
      <label><input id="deploy-preview" aria-label="Deploy preview" name="deployPreview" type="checkbox" /> Deploy preview</label>
      <fieldset>
        <legend>Plan</legend>
        <label><input id="plan-basic" aria-label="Basic plan" name="plan" type="radio" value="basic" /> Basic</label>
        <label><input id="plan-pro" aria-label="Pro plan" name="plan" type="radio" value="pro" /> Pro</label>
      </fieldset>
      <button type="submit">Deploy</button>
    </form>
    <div id="status">Idle</div>
  </main>
  <script>
    document.getElementById('deploy-form').addEventListener('submit', (event) => {
      event.preventDefault();
      const data = new FormData(event.target);
      document.getElementById('status').textContent = JSON.stringify({
        projectName: data.get('projectName'),
        repositoryUrl: data.get('repositoryUrl'),
        environment: data.get('environment'),
        deployPreview: data.get('deployPreview') === 'on',
        plan: data.get('plan'),
      });
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
	server._update_session_activity = lambda *_args, **_kwargs: None
	return server


async def _get_form_refs(server: AgentycServer) -> tuple[list[str], str, str]:
	state_json, _ = await server._get_browser_state(mode='auto', include_screenshot=False)
	payload = json.loads(state_json)
	interactive = payload['interactive_elements']
	text_inputs = [element['ref'] for element in interactive if element.get('tag') == 'input']
	select_ref = next(element['ref'] for element in interactive if element.get('tag') == 'select')
	submit_ref = next(element['ref'] for element in interactive if element.get('text') == 'Save profile')
	return text_inputs, select_ref, submit_ref


async def _get_status_payload(server: AgentycServer) -> dict[str, object]:
	status_html = await server._get_html('#status')
	return json.loads(status_html.removeprefix('<div id="status">').removesuffix('</div>'))


async def test_browser_fill_form_batches_text_and_dropdown_steps(
	httpserver: HTTPServer, browser_session: BrowserSession, mcp_server: AgentycServer
):
	httpserver.expect_request('/form-fill').respond_with_data(_FORM_PAGE, content_type='text/html')
	base_url = f'http://127.0.0.1:{httpserver.port}'
	nav_result = await mcp_server._navigate(f'{base_url}/form-fill')
	assert not nav_result.startswith('Error'), f'Navigation failed: {nav_result}'

	text_inputs, select_ref, submit_ref = await _get_form_refs(mcp_server)
	result = await mcp_server._execute_tool(
		'browser_fill_form',
		{
			'fields': [
				{'ref': text_inputs[0], 'label': 'Full name', 'text': 'Alex Mercer'},
				{'ref': text_inputs[1], 'label': 'Email address', 'text': 'alex@example.com'},
				{'ref': text_inputs[2], 'label': 'Company', 'text': 'Distillation Labs'},
				{'ref': select_ref, 'label': 'Role', 'option_text': 'Engineer'},
			]
		},
	)
	assert not str(result).startswith('Error'), f'fill_form failed: {result}'

	click_result = await mcp_server._click(ref=submit_ref)
	assert not click_result.startswith('Error'), f'Submit failed: {click_result}'
	assert await _get_status_payload(mcp_server) == {
		'fullName': 'Alex Mercer',
		'email': 'alex@example.com',
		'company': 'Distillation Labs',
		'role': 'Engineer',
	}


async def test_browser_fill_form_supports_toggle_controls_and_label_lookup(
	httpserver: HTTPServer, browser_session: BrowserSession, mcp_server: AgentycServer
):
	httpserver.expect_request('/form-fill-toggles').respond_with_data(_FORM_PAGE_WITH_TOGGLES, content_type='text/html')
	base_url = f'http://127.0.0.1:{httpserver.port}'
	nav_result = await mcp_server._navigate(f'{base_url}/form-fill-toggles')
	assert not nav_result.startswith('Error'), f'Navigation failed: {nav_result}'

	result = await mcp_server._execute_tool(
		'browser_fill_form',
		{
			'fields': [
				{'label': 'Project name', 'text': 'agentyc'},
				{'label': 'Repository URL', 'text': 'https://github.com/distillation-labs/agentyc'},
				{'label': 'Environment', 'option_text': 'Production'},
				{'label': 'Deploy preview', 'checked': True},
				{'label': 'Pro plan', 'checked': True},
			]
		},
	)
	assert not str(result).startswith('Error'), f'fill_form failed: {result}'

	state_json, _ = await mcp_server._get_browser_state(mode='full', include_screenshot=False)
	payload = json.loads(state_json)
	assert any(
		element.get('type') == 'checkbox'
		and element.get('text') == 'Deploy preview'
		and str(element.get('checked')).lower() == 'true'
		for element in payload['interactive_elements']
	)
	assert any(
		element.get('type') == 'radio' and element.get('value') == 'pro' and str(element.get('checked')).lower() == 'true'
		for element in payload['interactive_elements']
	)

	submit_ref = next(element['ref'] for element in payload['interactive_elements'] if element.get('text') == 'Deploy')
	click_result = await mcp_server._click(ref=submit_ref)
	assert not click_result.startswith('Error'), f'Submit failed: {click_result}'
	assert await _get_status_payload(mcp_server) == {
		'projectName': 'agentyc',
		'repositoryUrl': 'https://github.com/distillation-labs/agentyc',
		'environment': 'Production',
		'deployPreview': True,
		'plan': 'pro',
	}


async def test_browser_fill_form_requires_exactly_one_field_operation(
	httpserver: HTTPServer, browser_session: BrowserSession, mcp_server: AgentycServer
):
	httpserver.expect_request('/form-fill-error').respond_with_data(_FORM_PAGE, content_type='text/html')
	base_url = f'http://127.0.0.1:{httpserver.port}'
	nav_result = await mcp_server._navigate(f'{base_url}/form-fill-error')
	assert not nav_result.startswith('Error'), f'Navigation failed: {nav_result}'

	text_inputs, _, _ = await _get_form_refs(mcp_server)
	result = await mcp_server._execute_tool(
		'browser_fill_form',
		{
			'fields': [
				{'ref': text_inputs[0], 'label': 'Full name', 'text': 'Alex Mercer', 'option_text': 'Engineer'},
			]
		},
	)
	assert str(result).startswith('Error [invalid_argument]'), result
