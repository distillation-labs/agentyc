"""Deterministic label-based target resolution for MCP browser actions."""

from __future__ import annotations

import json
from typing import Any, cast


def _normalize_target_label(value: Any) -> str:
	return ' '.join(str(value or '').strip().lower().split())


def _summary_supports_operation(summary: dict[str, Any], operation: str) -> bool:
	tag = str(summary.get('tag') or '').lower()
	input_type = str(summary.get('type') or '').lower()
	role = str(summary.get('role') or '').lower()
	if operation == 'text':
		return (
			tag in {'input', 'textarea'}
			and input_type not in {'button', 'checkbox', 'file', 'hidden', 'image', 'radio', 'reset', 'submit'}
		) or role in {'searchbox', 'spinbutton', 'textbox'}
	if operation == 'option_text':
		return tag == 'select' or role == 'combobox'
	if operation == 'path':
		return tag == 'input' and input_type == 'file'
	if operation == 'checked':
		return (tag == 'input' and input_type in {'checkbox', 'radio'}) or role in {'checkbox', 'radio', 'switch'}
	if operation == 'click':
		if summary.get('disabled'):
			return False
		if tag in {'a', 'button', 'label', 'summary'}:
			return True
		if tag == 'input' and input_type not in {'hidden'}:
			return True
		return role in {'button', 'checkbox', 'combobox', 'link', 'menuitem', 'option', 'radio', 'switch', 'tab'}
	return False


def _label_match_score(summary: dict[str, Any], normalized_label: str) -> int:
	def _score(candidate: Any, exact: int, partial: int) -> int:
		value = _normalize_target_label(candidate)
		if not value:
			return 0
		if value == normalized_label:
			return exact
		if len(normalized_label) >= 4 and (normalized_label in value or value in normalized_label):
			return partial
		return 0

	score = max(
		_score(summary.get('text'), 100, 70),
		_score(summary.get('placeholder'), 85, 60),
		_score(summary.get('context'), 70, 45),
		_score(summary.get('description'), 65, 40),
	)
	if summary.get('disabled'):
		score -= 20
	return score


def _describe_summary(summary: dict[str, Any]) -> str:
	ref = summary.get('ref') or summary.get('index') or '?'
	detail = summary.get('text') or summary.get('placeholder') or summary.get('context') or summary.get('tag') or 'element'
	return f'{ref} ({detail})'


async def _interactive_elements_for_targeting(self) -> list[dict[str, Any]]:
	state_json, _ = await self._get_browser_state(mode='full', include_screenshot=False)
	if state_json.startswith('Error'):
		raise ValueError(state_json)
	payload = json.loads(state_json)
	raw_elements = payload.get('interactive_elements')
	if not isinstance(raw_elements, list):
		raise ValueError('browser state did not include interactive elements')
	return [element for element in raw_elements if isinstance(element, dict)]


async def _resolve_target_by_label(
	self,
	*,
	label: str,
	operation: str,
	interactive_elements: list[dict[str, Any]] | None = None,
	error_prefix: str = 'Element',
) -> tuple[str | None, int | None, str]:
	resolved_label = str(label or '').strip()
	if not resolved_label:
		raise ValueError(f'{error_prefix} label must not be empty')
	if interactive_elements is None:
		interactive_elements = await _interactive_elements_for_targeting(self)

	normalized_label = _normalize_target_label(resolved_label)
	matches: list[tuple[int, str, dict[str, Any]]] = []
	for summary in interactive_elements:
		if not _summary_supports_operation(summary, operation):
			continue
		score = _label_match_score(summary, normalized_label)
		if score <= 0:
			continue
		matches.append((score, str(summary.get('ref') or summary.get('index') or ''), summary))

	if not matches:
		raise ValueError(f'{error_prefix} ({resolved_label}) did not match any supported target')

	matches.sort(key=lambda item: (-item[0], item[1]))
	top_score = matches[0][0]
	top_matches = [match for match in matches if match[0] == top_score]
	if len(top_matches) > 1:
		candidates = ', '.join(_describe_summary(match[2]) for match in top_matches[:3])
		raise ValueError(f'{error_prefix} ({resolved_label}) matched multiple targets: {candidates}')

	chosen = matches[0][2]
	return cast(str | None, chosen.get('ref')), cast(int | None, chosen.get('index')), resolved_label
