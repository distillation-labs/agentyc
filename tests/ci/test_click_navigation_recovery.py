from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from agentyc.mcp.server import AgentycServer


def _empty_state(url: str) -> SimpleNamespace:
	return SimpleNamespace(
		url=url,
		dom_state=SimpleNamespace(
			_root=None,
			llm_representation=lambda: '',
		),
	)


def _loaded_state(url: str) -> SimpleNamespace:
	return SimpleNamespace(
		url=url,
		dom_state=SimpleNamespace(
			_root=object(),
			llm_representation=lambda: 'Loaded page',
		),
	)


async def test_click_recovers_empty_http_navigation_with_direct_retry():
	server = AgentycServer()
	server.browser_session = SimpleNamespace(
		id='session-1',
		get_current_page_url=AsyncMock(side_effect=['https://example.test/start', 'https://example.test/next']),
		get_current_page_title=AsyncMock(return_value='Recovered target'),
		get_browser_state_summary=AsyncMock(
			side_effect=[
				_empty_state('https://example.test/next'),
				_empty_state('https://example.test/next'),
				_loaded_state('https://example.test/next'),
			]
		),
		logger=SimpleNamespace(warning=lambda *_args, **_kwargs: None),
	)
	element = SimpleNamespace(tag_name='a', attributes={'href': 'https://example.test/next'}, parent_node=None)

	async def run_tool_action(action_name: str, payload: dict):
		if action_name == 'click':
			return SimpleNamespace(error=None)
		if action_name == 'navigate':
			assert payload == {'url': 'https://example.test/next', 'new_tab': False}
			return SimpleNamespace(error=None)
		raise AssertionError(f'unexpected action {action_name}')

	with (
		patch.object(server, '_resolve_live_element', new=AsyncMock(return_value=(element, 101, False))),
		patch.object(server, '_validate_actionable_element', return_value=None),
		patch.object(server, '_run_tool_action', new=AsyncMock(side_effect=run_tool_action)) as run_tool_action_mock,
		patch('agentyc.mcp.action_runtime._page_is_site_unavailable', new=AsyncMock(return_value=False)),
		patch('agentyc.mcp.action_runtime.asyncio.sleep', new=AsyncMock()),
	):
		result = await server._click(ref='e101')

	assert result == 'Clicked element e101 → https://example.test/next | "Recovered target"'
	assert run_tool_action_mock.await_count == 2


async def test_click_reports_site_unavailable_when_direct_retry_fails():
	server = AgentycServer()
	server.browser_session = SimpleNamespace(
		id='session-1',
		get_current_page_url=AsyncMock(side_effect=['https://example.test/start', 'https://example.test/next']),
		get_current_page_title=AsyncMock(return_value='Broken target'),
		get_browser_state_summary=AsyncMock(
			side_effect=[
				_empty_state('https://example.test/next'),
				_empty_state('https://example.test/next'),
			]
		),
		logger=SimpleNamespace(warning=lambda *_args, **_kwargs: None),
	)
	element = SimpleNamespace(tag_name='a', attributes={'href': 'https://example.test/next'}, parent_node=None)

	async def run_tool_action(action_name: str, payload: dict):
		if action_name == 'click':
			return SimpleNamespace(error=None)
		if action_name == 'navigate':
			assert payload == {'url': 'https://example.test/next', 'new_tab': False}
			return SimpleNamespace(error='Navigation failed - site unavailable: https://example.test/next')
		raise AssertionError(f'unexpected action {action_name}')

	with (
		patch.object(server, '_resolve_live_element', new=AsyncMock(return_value=(element, 101, False))),
		patch.object(server, '_validate_actionable_element', return_value=None),
		patch.object(server, '_run_tool_action', new=AsyncMock(side_effect=run_tool_action)),
		patch('agentyc.mcp.action_runtime._page_is_site_unavailable', new=AsyncMock(return_value=False)),
		patch('agentyc.mcp.action_runtime.asyncio.sleep', new=AsyncMock()),
	):
		result = await server._click(ref='e101')

	assert result == 'Error [site_unavailable]: Navigation failed - site unavailable: https://example.test/next'
