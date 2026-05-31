"""Shared action-runtime helper functions."""

from __future__ import annotations

import json
from typing import Any


def _inject_extraction_metadata(self, extracted_content: str, metadata: dict[str, Any] | None) -> str:
	if not metadata:
		return extracted_content
	visible_metadata = {
		'route': metadata.get('route') or metadata.get('strategy'),
		'llm_used': bool(metadata.get('llm_used', False)),
		'is_partial': bool(metadata.get('is_partial', False)),
		'structured_extraction': bool(metadata.get('structured_extraction', False)),
		'deterministic_extraction': bool(metadata.get('deterministic_extraction', False)),
	}
	if metadata.get('next_start_char') is not None:
		visible_metadata['next_start_char'] = metadata.get('next_start_char')
	return f'{extracted_content}\n<extraction_metadata>\n{json.dumps(visible_metadata, sort_keys=True)}\n</extraction_metadata>'


def _new_tab_postcondition_satisfied(self, *, before_tabs: list[Any], before_focus_target_id: str | None) -> bool:
	if not self.browser_session:
		return False

	from agentyc._utils_urls import is_new_tab_page

	current_target_id = self.browser_session.agent_focus_target_id
	if current_target_id is None:
		return False

	before_target_ids = {tab.target_id for tab in before_tabs}
	if current_target_id not in before_target_ids:
		return True
	if before_focus_target_id is not None and current_target_id != before_focus_target_id:
		return True
	if before_focus_target_id is None:
		return False

	before_focus_tab = next((tab for tab in before_tabs if tab.target_id == before_focus_target_id), None)
	return before_focus_tab is not None and is_new_tab_page(before_focus_tab.url)


__all__ = [
	'_inject_extraction_metadata',
	'_new_tab_postcondition_satisfied',
]
