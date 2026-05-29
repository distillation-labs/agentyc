"""Tests for coordinate clicking feature.

This feature allows certain models (Claude Sonnet 4, Claude Opus 4, Gemini 3 Pro, agentyc/* models)
to use coordinate-based clicking, while other models only get index-based clicking.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from agentyc.actions import ActionResult
from agentyc.browser.events import SwitchTabEvent
from agentyc.dom.views import NodeType
from agentyc.tools.service import Tools
from agentyc.tools.actions import interactions
from agentyc.tools.views import ClickElementAction, ClickElementActionIndexOnly


class TestCoordinateClickingTools:
	"""Test the Tools class coordinate clicking functionality."""

	def test_default_coordinate_clicking_disabled(self):
		"""By default, coordinate clicking should be disabled."""
		tools = Tools()

		assert tools._coordinate_clicking_enabled is False

	def test_default_uses_index_only_action(self):
		"""Default Tools should use ClickElementActionIndexOnly."""
		tools = Tools()

		click_action = tools.registry.registry.actions.get('click')
		assert click_action is not None
		assert click_action.param_model == ClickElementActionIndexOnly

	def test_default_click_schema_has_only_index(self):
		"""Default click action schema should only have index property."""
		tools = Tools()

		click_action = tools.registry.registry.actions.get('click')
		assert click_action is not None
		schema = click_action.param_model.model_json_schema()

		assert 'index' in schema['properties']
		assert 'coordinate_x' not in schema['properties']
		assert 'coordinate_y' not in schema['properties']

	def test_enable_coordinate_clicking(self):
		"""Enabling coordinate clicking should switch to ClickElementAction."""
		tools = Tools()
		tools.set_coordinate_clicking(True)

		assert tools._coordinate_clicking_enabled is True

		click_action = tools.registry.registry.actions.get('click')
		assert click_action is not None
		assert click_action.param_model == ClickElementAction

	def test_enabled_click_schema_has_coordinates(self):
		"""Enabled click action schema should have index and coordinate properties."""
		tools = Tools()
		tools.set_coordinate_clicking(True)

		click_action = tools.registry.registry.actions.get('click')
		assert click_action is not None
		schema = click_action.param_model.model_json_schema()

		assert 'index' in schema['properties']
		assert 'coordinate_x' in schema['properties']
		assert 'coordinate_y' in schema['properties']

	def test_disable_coordinate_clicking(self):
		"""Disabling coordinate clicking should switch back to index-only."""
		tools = Tools()
		tools.set_coordinate_clicking(True)
		tools.set_coordinate_clicking(False)

		assert tools._coordinate_clicking_enabled is False

		click_action = tools.registry.registry.actions.get('click')
		assert click_action is not None
		assert click_action.param_model == ClickElementActionIndexOnly

	def test_set_coordinate_clicking_idempotent(self):
		"""Setting the same value twice should not cause issues."""
		tools = Tools()

		# Enable twice
		tools.set_coordinate_clicking(True)
		tools.set_coordinate_clicking(True)
		assert tools._coordinate_clicking_enabled is True

		# Disable twice
		tools.set_coordinate_clicking(False)
		tools.set_coordinate_clicking(False)
		assert tools._coordinate_clicking_enabled is False

	def test_schema_title_consistent(self):
		"""Schema title should be 'ClickElementAction' regardless of mode."""
		tools = Tools()

		# Check default (disabled)
		click_action = tools.registry.registry.actions.get('click')
		assert click_action is not None
		schema = click_action.param_model.model_json_schema()
		assert schema['title'] == 'ClickElementAction'

		# Check enabled
		tools.set_coordinate_clicking(True)
		click_action = tools.registry.registry.actions.get('click')
		assert click_action is not None
		schema = click_action.param_model.model_json_schema()
		assert schema['title'] == 'ClickElementAction'


class TestCoordinateClickingModelDetection:
	"""Test the model detection logic for coordinate clicking."""

	@pytest.mark.parametrize(
		'model_name,expected_coords',
		[
			# Models that SHOULD have coordinate clicking (claude-sonnet-4*, claude-opus-4*, gemini-3-pro*, agentyc/*)
			('claude-sonnet-4-5', True),
			('claude-sonnet-4-5-20250101', True),
			('claude-sonnet-4-0', True),
			('claude-sonnet-4', True),
			('claude-opus-4-5', True),
			('claude-opus-4-5-latest', True),
			('claude-opus-4-0', True),
			('claude-opus-4', True),
			('gemini-3-pro-preview', True),
			('gemini-3-pro', True),
			('agentyc/fast', True),
			('agentyc/accurate', True),
			('CLAUDE-SONNET-4-5', True),  # Case insensitive
			('CLAUDE-SONNET-4', True),  # Case insensitive
			('GEMINI-3-PRO', True),  # Case insensitive
			# Models that should NOT have coordinate clicking
			('claude-3-5-sonnet', False),
			('claude-sonnet-3-5', False),
			('gpt-4o', False),
			('gpt-4-turbo', False),
			('gemini-2.0-flash', False),
			('gemini-1.5-pro', False),
			('llama-3.1-70b', False),
			('mistral-large', False),
		],
	)
	def test_model_detection_patterns(self, model_name: str, expected_coords: bool):
		"""Test that the model detection patterns correctly identify coordinate-capable models."""
		model_lower = model_name.lower()
		supports_coords = any(
			pattern in model_lower for pattern in ['claude-sonnet-4', 'claude-opus-4', 'gemini-3-pro', 'agentyc/']
		)
		assert supports_coords == expected_coords, f'Model {model_name}: expected {expected_coords}, got {supports_coords}'


class TestCoordinateClickingWithPassedTools:
	"""Test that coordinate clicking works correctly when Tools is passed to Agent."""

	def test_tools_can_be_modified_after_creation(self):
		"""Tools created externally can have coordinate clicking enabled."""
		tools = Tools()
		assert tools._coordinate_clicking_enabled is False

		# Simulate what Agent does for coordinate-capable models
		tools.set_coordinate_clicking(True)

		click_action = tools.registry.registry.actions.get('click')
		assert click_action is not None
		assert click_action.param_model == ClickElementAction

	def test_tools_state_preserved_after_modification(self):
		"""Verify that other tool state is preserved when toggling coordinate clicking."""
		tools = Tools(exclude_actions=['search'])

		# Search should be excluded
		assert 'search' not in tools.registry.registry.actions

		# Enable coordinate clicking
		tools.set_coordinate_clicking(True)

		# Search should still be excluded
		assert 'search' not in tools.registry.registry.actions

		# Click should have coordinates
		click_action = tools.registry.registry.actions.get('click')
		assert click_action is not None
		assert click_action.param_model == ClickElementAction


class TestClickNewTabDetection:
	@pytest.mark.asyncio
	async def test_same_tab_click_skips_tab_enumeration(self):
		tools = Tools()
		node = _FakeNode(7, 'Approve deployment', tag='button', attrs={'aria-label': 'Approve deployment'})
		browser_session = SimpleNamespace(
			get_element_by_index=AsyncMock(return_value=node),
			get_tabs=AsyncMock(side_effect=AssertionError('same-tab clicks should not enumerate tabs')),
			highlight_interaction_element=AsyncMock(),
			event_bus=SimpleNamespace(dispatch=Mock()),
			session_manager=SimpleNamespace(get_all_page_targets=lambda: [SimpleNamespace(target_id='target-existing')]),
			cdp_client=Mock(),
			agent_focus_target_id='target-existing',
		)
		event = _CompletedEvent({'click_x': 10, 'click_y': 20})
		browser_session.event_bus.dispatch.return_value = event

		result = await tools.click(index=7, browser_session=browser_session)

		assert isinstance(result, ActionResult)
		assert result.error is None
		assert result.extracted_content == 'Clicked button "Approve deployment" aria-label=Approve deployment'
		browser_session.get_tabs.assert_not_awaited()
		browser_session.event_bus.dispatch.assert_called_once()

	@pytest.mark.asyncio
	async def test_link_click_detects_new_tab_without_get_tabs(self):
		tools = Tools()
		node = _FakeNode(4, 'Open docs', tag='a', attrs={'href': 'https://example.com/docs', 'aria-label': 'Open docs'})
		page_targets = [SimpleNamespace(target_id='target-existing')]

		def get_all_page_targets():
			return list(page_targets)

		switch_event = _CompletedEvent('switched')
		click_event = _CompletedAsyncEvent(_append_new_target_and_return(page_targets, 'target-new'))

		def dispatch(event):
			if isinstance(event, SwitchTabEvent):
				return switch_event
			return click_event

		browser_session = SimpleNamespace(
			get_element_by_index=AsyncMock(return_value=node),
			get_tabs=AsyncMock(side_effect=AssertionError('link clicks should use target snapshots, not get_tabs')),
			highlight_interaction_element=AsyncMock(),
			event_bus=SimpleNamespace(dispatch=Mock(side_effect=dispatch)),
			session_manager=SimpleNamespace(get_all_page_targets=get_all_page_targets),
			cdp_client=Mock(),
			agent_focus_target_id='target-existing',
		)

		result = await tools.click(index=4, browser_session=browser_session)

		assert isinstance(result, ActionResult)
		assert result.error is None
		assert result.extracted_content.endswith('. Automatically switched to new tab (tab_id: -new).')
		browser_session.get_tabs.assert_not_awaited()

	async def test_click_regular_link_checks_new_tab_without_wait(self, monkeypatch):
		tools = Tools()
		node = _FakeNode(4, 'Docs', tag='a', attrs={'href': 'https://example.com/docs'})
		detect_new_tab = AsyncMock(return_value='')
		monkeypatch.setattr(interactions, '_detect_new_tab_opened', detect_new_tab)

		browser_session = SimpleNamespace(
			get_element_by_index=AsyncMock(return_value=node),
			highlight_interaction_element=AsyncMock(),
			event_bus=SimpleNamespace(dispatch=Mock(return_value=_CompletedEvent({'click_x': 1, 'click_y': 1}))),
			session_manager=SimpleNamespace(get_all_page_targets=lambda: [SimpleNamespace(target_id='target-existing')]),
			cdp_client=Mock(),
			agent_focus_target_id='target-existing',
		)

		result = await tools.click(index=4, browser_session=browser_session)

		assert isinstance(result, ActionResult)
		assert result.error is None
		detect_new_tab.assert_awaited_once_with(
			browser_session,
			{'target-existing'},
			wait_for_target=False,
		)

	async def test_click_explicit_new_tab_signal_keeps_wait(self, monkeypatch):
		tools = Tools()
		node = _FakeNode(4, 'Docs', tag='a', attrs={'href': 'https://example.com/docs', 'target': '_blank'})
		detect_new_tab = AsyncMock(return_value='')
		monkeypatch.setattr(interactions, '_detect_new_tab_opened', detect_new_tab)

		browser_session = SimpleNamespace(
			get_element_by_index=AsyncMock(return_value=node),
			highlight_interaction_element=AsyncMock(),
			event_bus=SimpleNamespace(dispatch=Mock(return_value=_CompletedEvent({'click_x': 1, 'click_y': 1}))),
			session_manager=SimpleNamespace(get_all_page_targets=lambda: [SimpleNamespace(target_id='target-existing')]),
			cdp_client=Mock(),
			agent_focus_target_id='target-existing',
		)

		result = await tools.click(index=4, browser_session=browser_session)

		assert isinstance(result, ActionResult)
		assert result.error is None
		detect_new_tab.assert_awaited_once_with(
			browser_session,
			{'target-existing'},
			wait_for_target=True,
		)


def _append_new_target_and_return(page_targets: list[SimpleNamespace], target_id: str):
	async def _side_effect(*_args, **_kwargs):
		page_targets.append(SimpleNamespace(target_id=target_id))
		return {'click_x': 1, 'click_y': 1}

	return _side_effect


class _CompletedEvent:
	def __init__(self, result=None):
		self.result = result

	def __await__(self):
		async def _noop():
			return None

		return _noop().__await__()

	async def event_result(self, *args, **kwargs):
		return self.result


class _CompletedAsyncEvent:
	def __init__(self, result_factory):
		self.result_factory = result_factory

	def __await__(self):
		async def _noop():
			return None

		return _noop().__await__()

	async def event_result(self, *args, **kwargs):
		return await self.result_factory(*args, **kwargs)


class _FakeNode:
	def __init__(self, backend_node_id: int, text: str, *, tag: str, attrs: dict[str, str] | None = None):
		self.node_id = backend_node_id
		self.backend_node_id = backend_node_id
		self.session_id = 'test-session'
		self.frame_id = None
		self.target_id = 'target-existing'
		self.node_type = NodeType.ELEMENT_NODE
		self.node_name = tag.upper()
		self.node_value = ''
		self.tag_name = tag
		self.attributes = attrs or {}
		self.is_scrollable = False
		self.is_visible = True
		self.absolute_position = None
		self.ax_node = None
		self.snapshot_node = None
		self.parent_node = None
		self.children = []
		self.children_nodes = []
		self._text = text

	def get_all_children_text(self, max_depth: int | None = None) -> str:
		return self._text
