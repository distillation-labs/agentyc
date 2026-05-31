"""Form-oriented runtime helpers for batched MCP actions."""

from __future__ import annotations

import json
from typing import Any

from agentyc.mcp.action_runtime_targeting import _normalize_target_label, _resolve_target_by_label


def _field_operation_name(field: dict[str, Any]) -> tuple[str | None, int]:
	operations = (
		('text', field.get('text') is not None),
		('option_text', field.get('option_text') is not None),
		('path', field.get('path') is not None),
		('checked', 'checked' in field),
	)
	provided = [name for name, present in operations if present]
	return (provided[0] if provided else None), len(provided)


async def _resolve_form_field_target(
	self,
	field: dict[str, Any],
	*,
	position: int,
	operation: str,
	interactive_elements: list[dict[str, Any]] | None,
) -> tuple[str | None, int | None, str]:
	ref = field.get('ref')
	index = field.get('index')
	label = str(field.get('label') or '').strip()
	if ref is not None or index is not None:
		return ref, index, label or str(ref or index)
	if not label:
		raise ValueError(f'Field {position} must provide ref, index, or label')
	return await _resolve_target_by_label(
		self,
		label=label,
		operation=operation,
		interactive_elements=interactive_elements,
		error_prefix=f'Field {position}',
	)


def _get_ax_prop(element: Any, name: str) -> Any:
	ax_node = getattr(element, 'ax_node', None)
	properties = getattr(ax_node, 'properties', None) if ax_node else None
	if not properties:
		return None
	for prop in properties:
		if getattr(prop, 'name', None) == name:
			return prop.value
	return None


def _toggle_kind(element: Any) -> str | None:
	attributes = getattr(element, 'attributes', {}) or {}
	tag = str(getattr(element, 'tag_name', '') or '').lower()
	input_type = str(attributes.get('type') or '').lower()
	role = str(getattr(getattr(element, 'ax_node', None), 'role', None) or attributes.get('role') or '').lower()
	if tag == 'input' and input_type in {'checkbox', 'radio'}:
		return input_type
	if role in {'checkbox', 'radio', 'switch'}:
		return role
	return None


def _checked_to_bool(value: Any) -> bool | None:
	if isinstance(value, bool):
		return value
	normalized = _normalize_target_label(value)
	if normalized in {'true', 'checked', 'mixed'}:
		return True
	if normalized in {'false', 'unchecked'}:
		return False
	return None


def _read_toggle_state(element: Any) -> bool | None:
	current = _checked_to_bool(_get_ax_prop(element, 'checked'))
	if current is not None:
		return current
	attributes = getattr(element, 'attributes', {}) or {}
	if 'aria-checked' in attributes:
		return _checked_to_bool(attributes.get('aria-checked'))
	if 'checked' in attributes:
		value = attributes.get('checked')
		return True if value in {'', None} else bool(_checked_to_bool(value))
	return False if _toggle_kind(element) is not None else None


async def _set_checked(self, *, checked: bool, index: int | None = None, ref: str | None = None, label: str | None = None) -> str:
	element, resolved_index, drift_recovered = await self._resolve_live_element(index=index, ref=ref)
	if not element:
		return self._format_action_error(
			f'Element with ref/index {ref or resolved_index} was not found. Refresh browser state before retrying.',
			default_code='stale_ref',
		)
	kind = _toggle_kind(element)
	if kind is None:
		return self._format_action_error(
			f'Element {label or ref or resolved_index} does not support checked state changes.',
			default_code='invalid_target',
		)
	current_state = _read_toggle_state(element)
	state_name = 'checked' if checked else 'unchecked'
	target_label = label or str(ref or resolved_index)
	if kind == 'radio' and current_state is True and not checked:
		return self._format_action_error(
			f'Radio button {target_label} cannot be directly unchecked; select a different option instead.',
			default_code='invalid_argument',
		)
	if current_state == checked:
		return f'{target_label} already {state_name}' + (' (recovered after DOM drift)' if drift_recovered else '')

	click_result = await self._click(index=resolved_index, ref=ref)
	if click_result.startswith('Error'):
		return click_result
	updated_element, _, _ = await self._resolve_live_element(index=resolved_index, ref=ref)
	if updated_element is None or _read_toggle_state(updated_element) != checked:
		return self._format_action_error(
			f'Element {target_label} did not end {state_name} after clicking it.',
			default_code='postcondition_failed',
		)
	return f'Set {target_label} {state_name}' + (' (recovered after DOM drift)' if drift_recovered else '')


async def _fill_form(self, fields: list[dict[str, Any]]) -> str:
	"""Fill multiple form fields in a single MCP round trip."""
	if not self.browser_session:
		return 'Error: No browser session active'
	if not fields:
		return 'Error: Provide at least one field entry'

	self._update_session_activity(self.browser_session.id)
	interactive_elements: list[dict[str, Any]] | None = None
	if any(isinstance(field, dict) and field.get('ref') is None and field.get('index') is None for field in fields):
		state_json, _ = await self._get_browser_state(mode='full', include_screenshot=False)
		if state_json.startswith('Error'):
			return state_json
		payload = json.loads(state_json)
		raw_elements = payload.get('interactive_elements')
		if not isinstance(raw_elements, list):
			return 'Error [fill_form_failed]: browser_fill_form could not inspect current form fields'
		interactive_elements = [element for element in raw_elements if isinstance(element, dict)]

	results: list[str] = []
	for position, field in enumerate(fields, start=1):
		if not isinstance(field, dict):
			return f'Error [invalid_argument]: Field {position} must be an object'
		operation, provided_count = _field_operation_name(field)
		label = str(field.get('label') or field.get('ref') or field.get('index') or f'field {position}')
		if provided_count != 1 or operation is None:
			return f'Error [invalid_argument]: Field {position} ({label}) must provide exactly one of text, option_text, path, or checked'
		if operation == 'checked' and not isinstance(field.get('checked'), bool):
			return f'Error [invalid_argument]: Field {position} ({label}) must provide checked as a boolean'
		try:
			ref, index, resolved_label = await _resolve_form_field_target(
				self,
				field,
				position=position,
				operation=operation,
				interactive_elements=interactive_elements,
			)
		except ValueError as error:
			return f'Error [invalid_argument]: {error}'

		if operation == 'text':
			result = await self._type_text(text=field['text'], index=index, ref=ref)
		elif operation == 'option_text':
			result = await self._select_option(text=field['option_text'], index=index, ref=ref)
		elif operation == 'path':
			result = await self._upload_file(path=field['path'], index=index, ref=ref)
		else:
			result = await _set_checked(self, checked=field['checked'], index=index, ref=ref, label=resolved_label)

		if result.startswith('Error'):
			return f'Error [fill_form_failed]: browser_fill_form stopped at {resolved_label}: {result}'
		results.append(result)

	return f'Filled {len(results)} form fields:\n- ' + '\n- '.join(results)
