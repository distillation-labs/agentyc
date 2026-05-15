from __future__ import annotations

from typing import Any

from agentyc.dom.views import EnhancedDOMTreeNode
from agentyc.tools.extraction.common import normalize_text
from agentyc.tools.extraction.formatters import (
	DeterministicExtractionResult,
	format_deterministic_form_result,
	format_deterministic_image_result,
	format_deterministic_key_value_result,
	format_deterministic_link_collection_result,
	format_deterministic_links_result,
	format_deterministic_list_result,
	format_deterministic_table_result,
)
from agentyc.tools.extraction.strategy import DeterministicExtractionStrategy, get_deterministic_extraction_strategy


def maybe_extract_deterministic_content(
	*,
	query: str,
	markdown: str,
	current_url: str,
	extract_links: bool,
	output_schema: dict | None,
	truncated: bool,
	content_stats: dict[str, Any],
	selector_map: dict[int, EnhancedDOMTreeNode] | None = None,
	already_collected: list[str] | None = None,
) -> DeterministicExtractionResult | None:
	strategy = get_deterministic_extraction_strategy(
		query=query,
		extract_links=extract_links,
		output_schema=output_schema,
	)
	if strategy is None:
		return None

	already_collected = already_collected or []
	if strategy == 'deterministic-links':
		if output_schema is not None:
			return None
		return format_deterministic_links_result(
			query=query,
			markdown=markdown,
			current_url=current_url,
			truncated=truncated,
			content_stats=content_stats,
			already_collected=already_collected,
		)
	if strategy == 'deterministic-link-collections':
		return format_deterministic_link_collection_result(
			query=query,
			markdown=markdown,
			current_url=current_url,
			truncated=truncated,
			content_stats=content_stats,
			output_schema=output_schema,
			already_collected=already_collected,
		)
	if strategy == 'deterministic-images':
		return format_deterministic_image_result(
			query=query,
			markdown=markdown,
			current_url=current_url,
			truncated=truncated,
			content_stats=content_stats,
			output_schema=output_schema,
			already_collected=already_collected,
		)
	if strategy == 'deterministic-tables':
		return format_deterministic_table_result(
			query=query,
			markdown=markdown,
			current_url=current_url,
			truncated=truncated,
			content_stats=content_stats,
			output_schema=output_schema,
			already_collected=already_collected,
		)
	if strategy == 'deterministic-lists':
		return format_deterministic_list_result(
			query=query,
			markdown=markdown,
			current_url=current_url,
			truncated=truncated,
			content_stats=content_stats,
			output_schema=output_schema,
			already_collected=already_collected,
		)
	if strategy == 'deterministic-key-values':
		return format_deterministic_key_value_result(
			query=query,
			markdown=markdown,
			current_url=current_url,
			truncated=truncated,
			content_stats=content_stats,
			output_schema=output_schema,
			already_collected=already_collected,
		)
	if strategy == 'deterministic-form-fields':
		return format_deterministic_form_result(
			query=query,
			current_url=current_url,
			selector_map=selector_map or {},
			output_schema=output_schema,
			already_collected=already_collected,
		)
	return None


__all__ = [
	'DeterministicExtractionResult',
	'DeterministicExtractionStrategy',
	'get_deterministic_extraction_strategy',
	'maybe_extract_deterministic_content',
	'normalize_text',
]
