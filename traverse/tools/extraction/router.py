from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import urljoin

from agentyc.dom.views import EnhancedDOMTreeNode

DeterministicExtractionStrategy = Literal[
	'deterministic-links',
	'deterministic-link-collections',
	'deterministic-images',
	'deterministic-tables',
	'deterministic-lists',
	'deterministic-form-fields',
	'deterministic-key-values',
]

_LINK_QUERY_HINTS = (
	'all links',
	'all urls',
	'all url',
	'all hrefs',
	'list links',
	'list urls',
	'extract links',
	'extract urls',
	'page links',
	'page urls',
	'link targets',
)
_TABLE_QUERY_HINTS = (
	'pricing table',
	'extract the table',
	'extract table',
	'list the table rows',
	'list table rows',
	'table rows',
	'table columns',
	'what is in the table',
)
_LIST_QUERY_HINTS = (
	'checklist items',
	'list items',
	'bullet points',
	'ordered list',
	'unordered list',
	'list the steps',
	'steps in the list',
)
_LINK_COLLECTION_QUERY_HINTS = (
	'search results',
	'results list',
	'result list',
	'result cards',
	'result cards on the page',
	'navigation links',
	'nav links',
	'menu items',
	'menu links',
	'pagination links',
	'pagination controls',
)
_IMAGE_QUERY_HINTS = (
	'image url',
	'image urls',
	'image src',
	'image sources',
	'img url',
	'img urls',
	'img src',
	'photo url',
	'photo urls',
	'product image',
	'product images',
	'thumbnail',
	'thumbnails',
	'picture',
	'pictures',
)
_FORM_QUERY_HINTS = (
	'form fields',
	'fields in the form',
	'fields on the page',
	'input fields',
	'form controls',
	'form inputs',
	'required fields',
	'dropdown options',
	'select options',
)
_KEY_VALUE_QUERY_HINTS = (
	'key value pairs',
	'key-value pairs',
	'settings summary',
	'status panel',
	'configuration values',
	'config values',
	'properties panel',
	'metadata panel',
	'deployment details',
)
_NON_DETERMINISTIC_QUERY_HINTS = (
	'summarize',
	'summary',
	'describe',
	'explain',
	'compare',
	'analyze',
	'overview',
)
_FIELD_COLLECTION_NAMES = {'fields', 'formfields', 'inputs', 'controls'}
_LIST_COLLECTION_NAMES = {'items', 'steps', 'checklist', 'entries', 'results'}
_TABLE_ROW_COLLECTION_NAMES = {'rows', 'tablerows', 'entries', 'items', 'results'}
_COLUMN_COLLECTION_NAMES = {'columns', 'headers'}
_LINK_COLLECTION_NAMES = {'results', 'items', 'links', 'entries', 'cards', 'pages', 'navigation', 'menuitems'}
_KEY_VALUE_COLLECTION_NAMES = {'pairs', 'items', 'entries', 'properties', 'settings', 'details'}
_KEY_VALUE_COUNT_NAMES = {'paircount', 'itemcount', 'entrycount', 'propertycount'}
_FIELD_VALUE_NAMES = {
	'label': {'label', 'name', 'title'},
	'field_type': {'fieldtype', 'type', 'inputtype', 'kind'},
	'required': {'required', 'isrequired', 'mandatory'},
	'placeholder': {'placeholder', 'hint', 'example'},
	'options': {'options', 'choices', 'values'},
}
_LIST_ITEM_VALUE_NAMES = {'text', 'item', 'label', 'step', 'value', 'name'}
_LINK_ITEM_VALUE_NAMES = {
	'title': {'title', 'name', 'label', 'text'},
	'url': {'url', 'href', 'link', 'target'},
	'summary': {'summary', 'description', 'details', 'snippet'},
}
_KEY_VALUE_FIELD_NAMES = {
	'key': {'key', 'name', 'label', 'property', 'field', 'setting'},
	'value': {'value', 'content', 'detail', 'status'},
}

_MARKDOWN_LINK_PATTERN = re.compile(r'(?<!!)\[([^\]]*)\]\(([^)]+)\)')
_MARKDOWN_IMAGE_PATTERN = re.compile(r'!\[([^\]]*)\]\(([^)\s]+)(?:\s+"([^"]*)")?\)')
_MARKDOWN_TABLE_ROW_PATTERN = re.compile(r'^\s*\|.*\|\s*$')
_MARKDOWN_LIST_ITEM_PATTERN = re.compile(r'^\s*(?:[-*+]|\d+[.)])\s+(.*)$')
_MARKDOWN_LIST_CONTINUATION_PATTERN = re.compile(r'^\s{2,}\S')
_KEY_VALUE_PATTERN = re.compile(r'^([^:\n]{1,80}?):\s+(.+)$')


@dataclass(slots=True)
class DeterministicExtractionResult:
	content: str
	metadata: dict[str, Any]
	structured_data: dict[str, Any] | None = None


def get_deterministic_extraction_strategy(
	*,
	query: str,
	extract_links: bool,
	output_schema: dict | None,
) -> DeterministicExtractionStrategy | None:
	normalized_query = normalize_text(query)
	if not normalized_query:
		return None
	if any(hint in normalized_query for hint in _NON_DETERMINISTIC_QUERY_HINTS):
		return None
	if _should_use_deterministic_link_route(query=query, extract_links=extract_links):
		return 'deterministic-links'
	if any(hint in normalized_query for hint in _LINK_COLLECTION_QUERY_HINTS):
		return 'deterministic-link-collections'
	if any(hint in normalized_query for hint in _IMAGE_QUERY_HINTS):
		return 'deterministic-images'
	if any(hint in normalized_query for hint in _TABLE_QUERY_HINTS):
		return 'deterministic-tables'
	if any(hint in normalized_query for hint in _KEY_VALUE_QUERY_HINTS):
		return 'deterministic-key-values'
	if any(hint in normalized_query for hint in _FORM_QUERY_HINTS):
		return 'deterministic-form-fields'
	if any(hint in normalized_query for hint in _LIST_QUERY_HINTS):
		return 'deterministic-lists'
	return None


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

	if strategy == 'deterministic-links':
		if output_schema is not None:
			return None
		return _format_deterministic_links_result(
			query=query,
			markdown=markdown,
			current_url=current_url,
			truncated=truncated,
			content_stats=content_stats,
			already_collected=already_collected or [],
		)
	if strategy == 'deterministic-link-collections':
		return _format_deterministic_link_collection_result(
			query=query,
			markdown=markdown,
			current_url=current_url,
			truncated=truncated,
			content_stats=content_stats,
			output_schema=output_schema,
			already_collected=already_collected or [],
		)
	if strategy == 'deterministic-images':
		return _format_deterministic_image_result(
			query=query,
			markdown=markdown,
			current_url=current_url,
			truncated=truncated,
			content_stats=content_stats,
			output_schema=output_schema,
			already_collected=already_collected or [],
		)
	if strategy == 'deterministic-tables':
		return _format_deterministic_table_result(
			query=query,
			markdown=markdown,
			current_url=current_url,
			truncated=truncated,
			content_stats=content_stats,
			output_schema=output_schema,
			already_collected=already_collected or [],
		)
	if strategy == 'deterministic-lists':
		return _format_deterministic_list_result(
			query=query,
			markdown=markdown,
			current_url=current_url,
			truncated=truncated,
			content_stats=content_stats,
			output_schema=output_schema,
			already_collected=already_collected or [],
		)
	if strategy == 'deterministic-key-values':
		return _format_deterministic_key_value_result(
			query=query,
			markdown=markdown,
			current_url=current_url,
			truncated=truncated,
			content_stats=content_stats,
			output_schema=output_schema,
			already_collected=already_collected or [],
		)
	if strategy == 'deterministic-form-fields':
		return _format_deterministic_form_result(
			query=query,
			current_url=current_url,
			selector_map=selector_map or {},
			output_schema=output_schema,
			already_collected=already_collected or [],
		)
	return None


def _format_deterministic_links_result(
	*,
	query: str,
	markdown: str,
	current_url: str,
	truncated: bool,
	content_stats: dict[str, Any],
	already_collected: list[str],
) -> DeterministicExtractionResult:
	normalized_seen = {normalize_text(item) for item in already_collected if item.strip()}
	links = []
	for link in _extract_markdown_links(markdown=markdown, current_url=current_url):
		text_key = normalize_text(link['text'])
		url_key = normalize_text(link['url'])
		if text_key in normalized_seen or url_key in normalized_seen:
			continue
		links.append(link)

	if links:
		lines = []
		for index, link in enumerate(links, start=1):
			if link['text'] and link['text'] != link['url']:
				lines.append(f'{index}. {link["text"]} — {link["url"]}')
			else:
				lines.append(f'{index}. {link["url"]}')
	else:
		lines = ['No links found in the current page content.']

	_append_partial_notice(lines=lines, truncated=truncated, content_stats=content_stats)
	return _build_deterministic_result(
		query=query,
		current_url=current_url,
		formatted_result='\n'.join(lines),
		metadata={
			'strategy': 'deterministic-links',
			'result_count': len(links),
			'is_partial': truncated,
			'next_start_char': content_stats.get('next_start_char'),
		},
	)


def _format_deterministic_link_collection_result(
	*,
	query: str,
	markdown: str,
	current_url: str,
	truncated: bool,
	content_stats: dict[str, Any],
	output_schema: dict | None,
	already_collected: list[str],
) -> DeterministicExtractionResult | None:
	normalized_seen = {normalize_text(item) for item in already_collected if item.strip()}
	items = []
	for item in _extract_markdown_link_collection_items(markdown=markdown, current_url=current_url):
		title_key = normalize_text(item['title'])
		url_key = normalize_text(item['url'])
		if title_key in normalized_seen or url_key in normalized_seen:
			continue
		items.append(item)
	structured_data = _build_link_collection_structured_payload(items=items, output_schema=output_schema)
	if output_schema is not None:
		if structured_data is None:
			return None
		return _build_structured_deterministic_result(
			query=query,
			current_url=current_url,
			structured_data=structured_data,
			metadata={
				'strategy': 'deterministic-link-collections',
				'item_count': len(items),
				'is_partial': truncated,
				'next_start_char': content_stats.get('next_start_char'),
			},
		)

	if items:
		lines = []
		for index, item in enumerate(items, start=1):
			line = f'{index}. {item["title"]} — {item["url"]}'
			if item['summary']:
				line += f'; {item["summary"]}'
			lines.append(line)
	else:
		lines = ['No deterministic link collection items found in the current page content.']

	_append_partial_notice(lines=lines, truncated=truncated, content_stats=content_stats)
	return _build_deterministic_result(
		query=query,
		current_url=current_url,
		formatted_result='\n'.join(lines),
		metadata={
			'strategy': 'deterministic-link-collections',
			'item_count': len(items),
			'is_partial': truncated,
			'next_start_char': content_stats.get('next_start_char'),
		},
	)


def _format_deterministic_table_result(
	*,
	query: str,
	markdown: str,
	current_url: str,
	truncated: bool,
	content_stats: dict[str, Any],
	output_schema: dict | None,
	already_collected: list[str],
) -> DeterministicExtractionResult | None:
	normalized_seen = {normalize_text(item) for item in already_collected if item.strip()}
	tables = _extract_markdown_tables(markdown)
	structured_data = _build_table_structured_payload(tables=tables, output_schema=output_schema)
	if output_schema is not None:
		if structured_data is None:
			return None
		return _build_structured_deterministic_result(
			query=query,
			current_url=current_url,
			structured_data=structured_data,
			metadata={
				'strategy': 'deterministic-tables',
				'table_count': len(tables),
				'row_count': sum(len(table['rows']) for table in tables),
				'is_partial': truncated,
				'next_start_char': content_stats.get('next_start_char'),
			},
		)
	lines: list[str] = []
	row_count = 0
	for table_index, table in enumerate(tables, start=1):
		lines.append(f'Table {table_index}')
		lines.append(f'Columns: {" | ".join(table["columns"])}')
		row_lines = []
		for row_index, row in enumerate(table['rows'], start=1):
			row_text = '; '.join(f'{column}: {value}' for column, value in row.items())
			if normalize_text(row_text) in normalized_seen:
				continue
			row_lines.append(f'{row_index}. {row_text}')
		if row_lines:
			lines.extend(row_lines)
			row_count += len(row_lines)
		else:
			lines.append('No rows matched after duplicate filtering.')
		if table_index != len(tables):
			lines.append('')

	if not tables:
		lines = ['No markdown tables found in the current page content.']

	_append_partial_notice(lines=lines, truncated=truncated, content_stats=content_stats)
	return _build_deterministic_result(
		query=query,
		current_url=current_url,
		formatted_result='\n'.join(lines),
		metadata={
			'strategy': 'deterministic-tables',
			'table_count': len(tables),
			'row_count': row_count,
			'is_partial': truncated,
			'next_start_char': content_stats.get('next_start_char'),
		},
	)


def _format_deterministic_list_result(
	*,
	query: str,
	markdown: str,
	current_url: str,
	truncated: bool,
	content_stats: dict[str, Any],
	output_schema: dict | None,
	already_collected: list[str],
) -> DeterministicExtractionResult | None:
	normalized_seen = {normalize_text(item) for item in already_collected if item.strip()}
	items = [item for item in _extract_markdown_list_items(markdown) if normalize_text(item) not in normalized_seen]
	structured_data = _build_list_structured_payload(items=items, output_schema=output_schema)
	if output_schema is not None:
		if structured_data is None:
			return None
		return _build_structured_deterministic_result(
			query=query,
			current_url=current_url,
			structured_data=structured_data,
			metadata={
				'strategy': 'deterministic-lists',
				'item_count': len(items),
				'is_partial': truncated,
				'next_start_char': content_stats.get('next_start_char'),
			},
		)
	if items:
		lines = [f'{index}. {item}' for index, item in enumerate(items, start=1)]
	else:
		lines = ['No markdown list items found in the current page content.']

	_append_partial_notice(lines=lines, truncated=truncated, content_stats=content_stats)
	return _build_deterministic_result(
		query=query,
		current_url=current_url,
		formatted_result='\n'.join(lines),
		metadata={
			'strategy': 'deterministic-lists',
			'item_count': len(items),
			'is_partial': truncated,
			'next_start_char': content_stats.get('next_start_char'),
		},
	)


def _format_deterministic_key_value_result(
	*,
	query: str,
	markdown: str,
	current_url: str,
	truncated: bool,
	content_stats: dict[str, Any],
	output_schema: dict | None,
	already_collected: list[str],
) -> DeterministicExtractionResult | None:
	normalized_seen = {normalize_text(item) for item in already_collected if item.strip()}
	pairs = []
	for pair in _extract_key_value_pairs(markdown):
		key_text = normalize_text(pair['key'])
		value_text = normalize_text(pair['value'])
		if key_text in normalized_seen or value_text in normalized_seen:
			continue
		pairs.append(pair)
	structured_data = _build_key_value_structured_payload(pairs=pairs, output_schema=output_schema)
	if output_schema is not None:
		if structured_data is None:
			return None
		return _build_structured_deterministic_result(
			query=query,
			current_url=current_url,
			structured_data=structured_data,
			metadata={
				'strategy': 'deterministic-key-values',
				'pair_count': len(pairs),
				'is_partial': truncated,
				'next_start_char': content_stats.get('next_start_char'),
			},
		)

	if pairs:
		lines = [f'{index}. {pair["key"]}: {pair["value"]}' for index, pair in enumerate(pairs, start=1)]
	else:
		lines = ['No deterministic key/value pairs found in the current page content.']

	_append_partial_notice(lines=lines, truncated=truncated, content_stats=content_stats)
	return _build_deterministic_result(
		query=query,
		current_url=current_url,
		formatted_result='\n'.join(lines),
		metadata={
			'strategy': 'deterministic-key-values',
			'pair_count': len(pairs),
			'is_partial': truncated,
			'next_start_char': content_stats.get('next_start_char'),
		},
	)


def _format_deterministic_form_result(
	*,
	query: str,
	current_url: str,
	selector_map: dict[int, EnhancedDOMTreeNode],
	output_schema: dict | None,
	already_collected: list[str],
) -> DeterministicExtractionResult | None:
	normalized_seen = {normalize_text(item) for item in already_collected if item.strip()}
	fields = []
	for field in _extract_form_fields(selector_map):
		if normalize_text(field['label']) in normalized_seen:
			continue
		fields.append(field)
	structured_data = _build_form_structured_payload(fields=fields, output_schema=output_schema)
	if output_schema is not None:
		if structured_data is None:
			return None
		return _build_structured_deterministic_result(
			query=query,
			current_url=current_url,
			structured_data=structured_data,
			metadata={
				'strategy': 'deterministic-form-fields',
				'field_count': len(fields),
				'is_partial': False,
				'next_start_char': None,
			},
		)

	if fields:
		lines = []
		for index, field in enumerate(fields, start=1):
			line = f'{index}. {field["label"]} — type={field["field_type"]}'
			details: list[str] = []
			if field['required']:
				details.append('required')
			if field['placeholder']:
				details.append(f'placeholder={field["placeholder"]}')
			if field['options']:
				details.append(f'options={" | ".join(field["options"])}')
			if details:
				line += f'; {"; ".join(details)}'
			lines.append(line)
	else:
		lines = ['No form fields found in the current page controls.']

	return _build_deterministic_result(
		query=query,
		current_url=current_url,
		formatted_result='\n'.join(lines),
		metadata={
			'strategy': 'deterministic-form-fields',
			'field_count': len(fields),
			'is_partial': False,
			'next_start_char': None,
		},
	)


def _format_deterministic_image_result(
	*,
	query: str,
	markdown: str,
	current_url: str,
	truncated: bool,
	content_stats: dict[str, Any],
	output_schema: dict | None,
	already_collected: list[str],
) -> DeterministicExtractionResult | None:
	normalized_seen = {normalize_text(item) for item in already_collected if item.strip()}
	images = []
	for image in _extract_markdown_images(markdown=markdown, current_url=current_url):
		alt_key = normalize_text(image['alt'])
		url_key = normalize_text(image['url'])
		if alt_key in normalized_seen or url_key in normalized_seen:
			continue
		images.append(image)
	structured_data = _build_image_structured_payload(images=images, output_schema=output_schema)
	if output_schema is not None:
		if structured_data is None:
			return None
		return _build_structured_deterministic_result(
			query=query,
			current_url=current_url,
			structured_data=structured_data,
			metadata={
				'strategy': 'deterministic-images',
				'image_count': len(images),
				'is_partial': truncated,
				'next_start_char': content_stats.get('next_start_char'),
			},
		)

	if images:
		lines = []
		for index, image in enumerate(images, start=1):
			line = f'{index}. {image["alt"] or image["url"]} — {image["url"]}'
			if image['title']:
				line += f'; title={image["title"]}'
			lines.append(line)
	else:
		lines = ['No markdown images found in the current page content.']

	_append_partial_notice(lines=lines, truncated=truncated, content_stats=content_stats)
	return _build_deterministic_result(
		query=query,
		current_url=current_url,
		formatted_result='\n'.join(lines),
		metadata={
			'strategy': 'deterministic-images',
			'image_count': len(images),
			'is_partial': truncated,
			'next_start_char': content_stats.get('next_start_char'),
		},
	)


def _build_deterministic_result(
	*,
	query: str,
	current_url: str,
	formatted_result: str,
	metadata: dict[str, Any],
) -> DeterministicExtractionResult:
	content = f'<url>\n{current_url}\n</url>\n<query>\n{query}\n</query>\n<result>\n{formatted_result}\n</result>'
	return DeterministicExtractionResult(content=content, metadata=metadata)


def _build_structured_deterministic_result(
	*,
	query: str,
	current_url: str,
	structured_data: dict[str, Any],
	metadata: dict[str, Any],
) -> DeterministicExtractionResult:
	content = (
		f'<url>\n{current_url}\n</url>\n<query>\n{query}\n</query>\n'
		f'<structured_result>\n{json.dumps(structured_data)}\n</structured_result>'
	)
	return DeterministicExtractionResult(content=content, metadata=metadata, structured_data=structured_data)


def _append_partial_notice(*, lines: list[str], truncated: bool, content_stats: dict[str, Any]) -> None:
	if truncated and content_stats.get('next_start_char') is not None:
		lines.append(f'Partial result. Use start_from_char={content_stats["next_start_char"]} to continue.')


def _build_table_structured_payload(*, tables: list[dict[str, Any]], output_schema: dict | None) -> dict[str, Any] | None:
	if output_schema is None:
		return None
	properties = output_schema.get('properties', {})
	if not properties:
		return None
	first_table = tables[0] if tables else {'columns': [], 'rows': []}
	result: dict[str, Any] = {}
	matched = False
	single_array_fallback = len(properties) == 1
	for prop_name, prop_schema in properties.items():
		normalized = _normalize_identifier(prop_name)
		value: Any | None = None
		if normalized in _COLUMN_COLLECTION_NAMES:
			value = list(first_table['columns'])
		elif normalized in _TABLE_ROW_COLLECTION_NAMES:
			value = _project_table_rows(rows=first_table['rows'], prop_schema=prop_schema)
		elif normalized == 'tables':
			value = _project_tables(tables=tables, prop_schema=prop_schema)
		elif normalized == 'tablecount':
			value = len(tables)
		elif normalized == 'rowcount':
			value = sum(len(table['rows']) for table in tables)
		elif single_array_fallback and prop_schema.get('type') == 'array':
			value = _project_table_rows(rows=first_table['rows'], prop_schema=prop_schema)
		elif normalized in {'table', 'data'} and prop_schema.get('type') == 'object':
			value = _project_single_table(table=first_table, schema=prop_schema)
		if value is None:
			continue
		result[prop_name] = value
		matched = True
	return result if matched else None


def _build_list_structured_payload(*, items: list[str], output_schema: dict | None) -> dict[str, Any] | None:
	if output_schema is None:
		return None
	properties = output_schema.get('properties', {})
	if not properties:
		return None
	result: dict[str, Any] = {}
	matched = False
	single_array_fallback = len(properties) == 1
	for prop_name, prop_schema in properties.items():
		normalized = _normalize_identifier(prop_name)
		value: Any | None = None
		if normalized in _LIST_COLLECTION_NAMES:
			value = _project_list_items(items=items, prop_schema=prop_schema)
		elif normalized in {'itemcount', 'stepcount'}:
			value = len(items)
		elif single_array_fallback and prop_schema.get('type') == 'array':
			value = _project_list_items(items=items, prop_schema=prop_schema)
		if value is None:
			continue
		result[prop_name] = value
		matched = True
	return result if matched else None


def _build_link_collection_structured_payload(
	*, items: list[dict[str, str]], output_schema: dict | None
) -> dict[str, Any] | None:
	if output_schema is None:
		return None
	properties = output_schema.get('properties', {})
	if not properties:
		return None
	result: dict[str, Any] = {}
	matched = False
	single_array_fallback = len(properties) == 1
	for prop_name, prop_schema in properties.items():
		normalized = _normalize_identifier(prop_name)
		value: Any | None = None
		if normalized in _LINK_COLLECTION_NAMES:
			value = _project_link_collection_items(items=items, prop_schema=prop_schema)
		elif normalized in {'resultcount', 'itemcount', 'linkcount', 'entrycount', 'pagecount'}:
			value = len(items)
		elif single_array_fallback and prop_schema.get('type') == 'array':
			value = _project_link_collection_items(items=items, prop_schema=prop_schema)
		if value is None:
			continue
		result[prop_name] = value
		matched = True
	return result if matched else None


def _build_image_structured_payload(*, images: list[dict[str, str]], output_schema: dict | None) -> dict[str, Any] | None:
	if output_schema is None:
		return None
	properties = output_schema.get('properties', {})
	if not properties:
		return None
	result: dict[str, Any] = {}
	matched = False
	single_array_fallback = len(properties) == 1
	for prop_name, prop_schema in properties.items():
		normalized = _normalize_identifier(prop_name)
		value: Any | None = None
		if normalized in {'images', 'items', 'results', 'entries', 'cards', 'products'}:
			value = _project_image_items(images=images, prop_schema=prop_schema)
		elif normalized in {'imagecount', 'itemcount', 'resultcount', 'entrycount', 'productcount'}:
			value = len(images)
		elif normalized in {'image', 'picture', 'photo', 'thumbnail'} and prop_schema.get('type') == 'object':
			value = _project_single_image(image=images[0], schema=prop_schema) if images else None
		elif single_array_fallback and prop_schema.get('type') == 'array':
			value = _project_image_items(images=images, prop_schema=prop_schema)
		if value is None:
			continue
		result[prop_name] = value
		matched = True
	return result if matched else None


def _build_key_value_structured_payload(*, pairs: list[dict[str, str]], output_schema: dict | None) -> dict[str, Any] | None:
	if output_schema is None:
		return None
	properties = output_schema.get('properties', {})
	if not properties:
		return None
	normalized_pairs = {_normalize_identifier(pair['key']): pair['value'] for pair in pairs}
	result: dict[str, Any] = {}
	matched = False
	single_array_fallback = len(properties) == 1
	for prop_name, prop_schema in properties.items():
		normalized = _normalize_identifier(prop_name)
		value: Any | None = None
		if normalized in _KEY_VALUE_COLLECTION_NAMES:
			value = _project_key_value_pairs(pairs=pairs, prop_schema=prop_schema)
		elif normalized in _KEY_VALUE_COUNT_NAMES:
			value = len(pairs)
		elif normalized in normalized_pairs:
			value = _coerce_scalar_value(normalized_pairs[normalized], prop_schema)
		elif prop_schema.get('type') == 'object':
			value = _project_key_value_object(pairs=pairs, prop_schema=prop_schema)
		elif single_array_fallback and prop_schema.get('type') == 'array':
			value = _project_key_value_pairs(pairs=pairs, prop_schema=prop_schema)
		if value is None:
			continue
		result[prop_name] = value
		matched = True
	return result if matched else None


def _project_image_items(*, images: list[dict[str, str]], prop_schema: dict[str, Any]) -> list[Any] | None:
	if prop_schema.get('type') != 'array':
		return None
	item_schema = prop_schema.get('items', {})
	if item_schema.get('type') == 'string':
		return [image['url'] for image in images]
	if item_schema.get('type') == 'object' or item_schema.get('properties'):
		return [_project_single_image(image=image, schema=item_schema) for image in images]
	return list(images)


def _project_single_image(*, image: dict[str, str], schema: dict[str, Any]) -> dict[str, Any]:
	properties = schema.get('properties', {})
	if not properties:
		return dict(image)
	result: dict[str, Any] = {}
	for prop_name, prop_schema in properties.items():
		normalized = _normalize_identifier(prop_name)
		if normalized in {'url', 'src', 'href', 'link'}:
			result[prop_name] = _coerce_scalar_value(image['url'], prop_schema)
			continue
		if normalized == 'title':
			result[prop_name] = _coerce_scalar_value(image['title'] or image['alt'] or image['url'], prop_schema)
			continue
		if normalized in {'alt', 'label', 'name', 'caption'}:
			result[prop_name] = _coerce_scalar_value(image['alt'] or image['title'] or image['url'], prop_schema)
	return result


def _build_form_structured_payload(*, fields: list[dict[str, Any]], output_schema: dict | None) -> dict[str, Any] | None:
	if output_schema is None:
		return None
	properties = output_schema.get('properties', {})
	if not properties:
		return None
	result: dict[str, Any] = {}
	matched = False
	single_array_fallback = len(properties) == 1
	for prop_name, prop_schema in properties.items():
		normalized = _normalize_identifier(prop_name)
		value: Any | None = None
		if normalized in _FIELD_COLLECTION_NAMES:
			value = _project_form_fields(fields=fields, prop_schema=prop_schema)
		elif normalized == 'fieldcount':
			value = len(fields)
		elif single_array_fallback and prop_schema.get('type') == 'array':
			value = _project_form_fields(fields=fields, prop_schema=prop_schema)
		if value is None:
			continue
		result[prop_name] = value
		matched = True
	return result if matched else None


def _project_link_collection_items(*, items: list[dict[str, str]], prop_schema: dict[str, Any]) -> list[Any] | None:
	if prop_schema.get('type') != 'array':
		return None
	item_schema = prop_schema.get('items', {})
	if item_schema.get('type') == 'string':
		return [item['title'] for item in items]
	if item_schema.get('type') == 'object' or item_schema.get('properties'):
		return [_project_link_collection_item(item=item, item_schema=item_schema) for item in items]
	return list(items)


def _project_link_collection_item(*, item: dict[str, str], item_schema: dict[str, Any]) -> dict[str, Any]:
	properties = item_schema.get('properties', {})
	if not properties:
		return dict(item)
	result: dict[str, Any] = {}
	for prop_name, prop_schema in properties.items():
		normalized = _normalize_identifier(prop_name)
		source_key = None
		for canonical_key, aliases in _LINK_ITEM_VALUE_NAMES.items():
			if normalized in aliases:
				source_key = canonical_key
				break
		if source_key is None:
			continue
		result[prop_name] = _coerce_scalar_value(item[source_key], prop_schema)
	return result


def _project_key_value_pairs(*, pairs: list[dict[str, str]], prop_schema: dict[str, Any]) -> list[Any] | None:
	if prop_schema.get('type') != 'array':
		return None
	item_schema = prop_schema.get('items', {})
	if item_schema.get('type') == 'string':
		return [f'{pair["key"]}: {pair["value"]}' for pair in pairs]
	if item_schema.get('type') == 'object' or item_schema.get('properties'):
		return [_project_key_value_pair(pair=pair, item_schema=item_schema) for pair in pairs]
	return list(pairs)


def _project_key_value_pair(*, pair: dict[str, str], item_schema: dict[str, Any]) -> dict[str, Any]:
	properties = item_schema.get('properties', {})
	if not properties:
		return dict(pair)
	result: dict[str, Any] = {}
	for prop_name, prop_schema in properties.items():
		normalized = _normalize_identifier(prop_name)
		if normalized in _KEY_VALUE_FIELD_NAMES['key']:
			result[prop_name] = _coerce_scalar_value(pair['key'], prop_schema)
			continue
		if normalized in _KEY_VALUE_FIELD_NAMES['value']:
			result[prop_name] = _coerce_scalar_value(pair['value'], prop_schema)
			continue
		if normalized == _normalize_identifier(pair['key']):
			result[prop_name] = _coerce_scalar_value(pair['value'], prop_schema)
	return result


def _project_key_value_object(*, pairs: list[dict[str, str]], prop_schema: dict[str, Any]) -> dict[str, Any]:
	properties = prop_schema.get('properties', {})
	if not properties:
		return {pair['key']: pair['value'] for pair in pairs}
	pair_lookup = {_normalize_identifier(pair['key']): pair['value'] for pair in pairs}
	result: dict[str, Any] = {}
	for prop_name, nested_schema in properties.items():
		normalized = _normalize_identifier(prop_name)
		if normalized not in pair_lookup:
			continue
		result[prop_name] = _coerce_scalar_value(pair_lookup[normalized], nested_schema)
	return result


def _project_tables(*, tables: list[dict[str, Any]], prop_schema: dict[str, Any]) -> list[dict[str, Any]] | None:
	if prop_schema.get('type') != 'array':
		return None
	item_schema = prop_schema.get('items', {})
	return [_project_single_table(table=table, schema=item_schema) for table in tables]


def _project_single_table(*, table: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
	properties = schema.get('properties', {})
	if not properties:
		return {'columns': table['columns'], 'rows': table['rows']}
	result: dict[str, Any] = {}
	for prop_name, prop_schema in properties.items():
		normalized = _normalize_identifier(prop_name)
		value: Any | None = None
		if normalized in _COLUMN_COLLECTION_NAMES:
			value = list(table['columns'])
		elif normalized in _TABLE_ROW_COLLECTION_NAMES:
			value = _project_table_rows(rows=table['rows'], prop_schema=prop_schema)
		elif normalized == 'rowcount':
			value = len(table['rows'])
		if value is None:
			continue
		result[prop_name] = value
	return result


def _project_table_rows(*, rows: list[dict[str, str]], prop_schema: dict[str, Any]) -> list[Any] | None:
	if prop_schema.get('type') != 'array':
		return None
	item_schema = prop_schema.get('items', {})
	if item_schema.get('type') == 'string':
		return ['; '.join(f'{key}: {value}' for key, value in row.items()) for row in rows]
	if item_schema.get('type') == 'object' or item_schema.get('properties'):
		return [_project_table_row(row=row, item_schema=item_schema) for row in rows]
	return list(rows)


def _project_table_row(*, row: dict[str, str], item_schema: dict[str, Any]) -> dict[str, Any]:
	properties = item_schema.get('properties', {})
	if not properties:
		return dict(row)
	result: dict[str, Any] = {}
	for prop_name, prop_schema in properties.items():
		source_key = _find_matching_key(prop_name, row.keys())
		if source_key is None:
			continue
		result[prop_name] = _coerce_scalar_value(row[source_key], prop_schema)
	return result


def _project_list_items(*, items: list[str], prop_schema: dict[str, Any]) -> list[Any] | None:
	if prop_schema.get('type') != 'array':
		return None
	item_schema = prop_schema.get('items', {})
	if item_schema.get('type') == 'string' or not item_schema:
		return list(items)
	if item_schema.get('type') == 'object' or item_schema.get('properties'):
		return [_project_list_item(item=item, item_schema=item_schema) for item in items]
	return None


def _project_list_item(*, item: str, item_schema: dict[str, Any]) -> dict[str, Any]:
	properties = item_schema.get('properties', {})
	if not properties:
		return {'text': item}
	result: dict[str, Any] = {}
	for prop_name, prop_schema in properties.items():
		normalized = _normalize_identifier(prop_name)
		if normalized in _LIST_ITEM_VALUE_NAMES:
			result[prop_name] = _coerce_scalar_value(item, prop_schema)
	return result


def _project_form_fields(*, fields: list[dict[str, Any]], prop_schema: dict[str, Any]) -> list[Any] | None:
	if prop_schema.get('type') != 'array':
		return None
	item_schema = prop_schema.get('items', {})
	if item_schema.get('type') == 'string':
		return [field['label'] for field in fields]
	if item_schema.get('type') == 'object' or item_schema.get('properties'):
		return [_project_form_field(field=field, item_schema=item_schema) for field in fields]
	return list(fields)


def _project_form_field(*, field: dict[str, Any], item_schema: dict[str, Any]) -> dict[str, Any]:
	properties = item_schema.get('properties', {})
	if not properties:
		return dict(field)
	result: dict[str, Any] = {}
	for prop_name, prop_schema in properties.items():
		normalized = _normalize_identifier(prop_name)
		source_key = None
		for canonical_key, aliases in _FIELD_VALUE_NAMES.items():
			if normalized in aliases:
				source_key = canonical_key
				break
		if source_key is None:
			continue
		result[prop_name] = _coerce_form_field_value(field[source_key], prop_schema)
	return result


def _coerce_form_field_value(value: Any, prop_schema: dict[str, Any]) -> Any:
	if prop_schema.get('type') == 'array':
		return list(value) if isinstance(value, list) else [value]
	return _coerce_scalar_value(value, prop_schema)


def _coerce_scalar_value(value: Any, prop_schema: dict[str, Any]) -> Any:
	type_name = prop_schema.get('type')
	if type_name == 'integer':
		if isinstance(value, str):
			match = re.search(r'-?\d+', value)
			return int(match.group()) if match else 0
		return int(value)
	if type_name == 'number':
		if isinstance(value, str):
			match = re.search(r'-?\d+(?:\.\d+)?', value.replace(',', ''))
			return float(match.group()) if match else 0.0
		return float(value)
	if type_name == 'boolean':
		if isinstance(value, str):
			return normalize_text(value) in {'true', 'yes', 'required', '1'}
		return bool(value)
	return value


def _find_matching_key(target: str, candidates: Any) -> str | None:
	normalized_target = _normalize_identifier(target)
	for candidate in candidates:
		if _normalize_identifier(candidate) == normalized_target:
			return candidate
	return None


def _normalize_identifier(text: str) -> str:
	return re.sub(r'[^a-z0-9]+', '', normalize_text(text))


def _should_use_deterministic_link_route(*, query: str, extract_links: bool) -> bool:
	if not extract_links:
		return False

	normalized_query = normalize_text(query)
	if normalized_query in {'links', 'urls', 'hrefs'}:
		return True
	return any(hint in normalized_query for hint in _LINK_QUERY_HINTS)


def _extract_markdown_links(*, markdown: str, current_url: str) -> list[dict[str, str]]:
	seen: set[tuple[str, str]] = set()
	results: list[dict[str, str]] = []
	for text, url in _MARKDOWN_LINK_PATTERN.findall(markdown):
		normalized_url = urljoin(current_url, url.strip())
		normalized_text = ' '.join(text.split())
		key = (normalized_text, normalized_url)
		if key in seen:
			continue
		seen.add(key)
		results.append({'text': normalized_text, 'url': normalized_url})
	return results


def _extract_markdown_images(*, markdown: str, current_url: str) -> list[dict[str, str]]:
	seen: set[tuple[str, str, str]] = set()
	results: list[dict[str, str]] = []
	for alt, url, title in _MARKDOWN_IMAGE_PATTERN.findall(markdown):
		normalized_url = urljoin(current_url, url.strip())
		normalized_alt = ' '.join(alt.split())
		normalized_title = ' '.join(title.split())
		key = (normalized_alt, normalized_url, normalized_title)
		if key in seen:
			continue
		seen.add(key)
		results.append({'alt': normalized_alt, 'url': normalized_url, 'title': normalized_title})
	return results


def _extract_markdown_tables(markdown: str) -> list[dict[str, Any]]:
	tables: list[dict[str, Any]] = []
	lines = markdown.splitlines()
	index = 0
	while index < len(lines) - 1:
		header_line = lines[index]
		separator_line = lines[index + 1]
		if not _MARKDOWN_TABLE_ROW_PATTERN.match(header_line):
			index += 1
			continue
		if not (_MARKDOWN_TABLE_ROW_PATTERN.match(separator_line) and '---' in separator_line):
			index += 1
			continue

		columns = _split_markdown_table_row(header_line)
		index += 2
		rows: list[dict[str, str]] = []
		while index < len(lines) and _MARKDOWN_TABLE_ROW_PATTERN.match(lines[index]):
			values = _split_markdown_table_row(lines[index])
			if values:
				if len(values) < len(columns):
					values.extend([''] * (len(columns) - len(values)))
				row = {column: value for column, value in zip(columns, values, strict=False)}
				rows.append(row)
			index += 1
		tables.append({'columns': columns, 'rows': rows})
	return tables


def _split_markdown_table_row(line: str) -> list[str]:
	return [part.strip() for part in line.strip().strip('|').split('|')]


def _extract_markdown_list_items(markdown: str) -> list[str]:
	items: list[str] = []
	current_lines: list[str] = []
	for line in markdown.splitlines():
		match = _MARKDOWN_LIST_ITEM_PATTERN.match(line)
		if match:
			if current_lines:
				items.append(' '.join(part for part in current_lines if part))
			current_lines = [match.group(1).strip()]
			continue
		if current_lines and _MARKDOWN_LIST_CONTINUATION_PATTERN.match(line):
			current_lines.append(line.strip())
			continue
		if current_lines:
			items.append(' '.join(part for part in current_lines if part))
			current_lines = []
	if current_lines:
		items.append(' '.join(part for part in current_lines if part))
	return items


def _extract_markdown_link_collection_items(*, markdown: str, current_url: str) -> list[dict[str, str]]:
	items: list[dict[str, str]] = []
	seen: set[tuple[str, str]] = set()
	for list_item in _extract_markdown_list_items(markdown):
		matches = _MARKDOWN_LINK_PATTERN.findall(list_item)
		if not matches:
			continue
		title, url = matches[0]
		normalized_url = urljoin(current_url, url.strip())
		normalized_title = ' '.join(title.split()) or normalized_url
		summary = _MARKDOWN_LINK_PATTERN.sub(lambda match: ' '.join(match.group(1).split()), list_item)
		summary = summary.replace(normalized_title, '', 1)
		summary = ' '.join(summary.strip(' -:;,.').split())
		key = (normalize_text(normalized_title), normalize_text(normalized_url))
		if key in seen:
			continue
		seen.add(key)
		items.append({'title': normalized_title, 'url': normalized_url, 'summary': summary})
	if items:
		return items

	for link in _extract_markdown_links(markdown=markdown, current_url=current_url):
		title = link['text'] or link['url']
		key = (normalize_text(title), normalize_text(link['url']))
		if key in seen:
			continue
		seen.add(key)
		items.append({'title': title, 'url': link['url'], 'summary': ''})
	return items


def _extract_key_value_pairs(markdown: str) -> list[dict[str, str]]:
	pairs: list[dict[str, str]] = []
	seen: set[tuple[str, str]] = set()
	for raw_line in markdown.splitlines():
		line = raw_line.strip()
		if not line:
			continue
		list_match = _MARKDOWN_LIST_ITEM_PATTERN.match(line)
		if list_match:
			line = list_match.group(1).strip()
		line = _MARKDOWN_LINK_PATTERN.sub(lambda match: ' '.join(match.group(1).split()), line)
		match = _KEY_VALUE_PATTERN.match(line)
		if not match:
			continue
		key = ' '.join(match.group(1).split())
		value = ' '.join(match.group(2).split())
		if not key or not value:
			continue
		if key.endswith(('.', '?')):
			continue
		signature = (normalize_text(key), normalize_text(value))
		if signature in seen:
			continue
		seen.add(signature)
		pairs.append({'key': key, 'value': value})
	return pairs


def _extract_form_fields(selector_map: dict[int, EnhancedDOMTreeNode]) -> list[dict[str, Any]]:
	fields: list[dict[str, Any]] = []
	seen: set[tuple[str, str, str, tuple[str, ...]]] = set()
	for _, element in sorted(selector_map.items()):
		if element.tag_name not in {'input', 'select', 'textarea'}:
			continue
		field_type = normalize_text(element.attributes.get('type', '')) or element.tag_name
		if field_type == 'hidden':
			continue
		label = _form_field_label(element)
		placeholder = ' '.join(element.attributes.get('placeholder', '').split())
		options = _extract_option_texts(element) if element.tag_name == 'select' else []
		signature = (label, field_type, placeholder, tuple(options))
		if signature in seen:
			continue
		seen.add(signature)
		fields.append(
			{
				'label': label,
				'field_type': field_type,
				'required': _is_required_field(element),
				'placeholder': placeholder,
				'options': options,
			}
		)
	return fields


def _form_field_label(element: EnhancedDOMTreeNode) -> str:
	for candidate in (
		element.attributes.get('aria-label'),
		getattr(element.ax_node, 'name', None) if element.ax_node else None,
		_parent_label_text(element),
		element.attributes.get('name'),
		element.attributes.get('placeholder'),
		element.attributes.get('id'),
		element.get_meaningful_text_for_llm(),
	):
		normalized = ' '.join((candidate or '').split())
		if normalized:
			return normalized
	return f'{element.tag_name} field'


def _parent_label_text(element: EnhancedDOMTreeNode) -> str | None:
	parent = element.parent_node
	while parent is not None:
		if parent.tag_name == 'label':
			text = ' '.join(parent.get_all_children_text().split())
			if text:
				return text
			break
		parent = parent.parent_node
	return None


def _extract_option_texts(element: EnhancedDOMTreeNode) -> list[str]:
	options: list[str] = []
	for child in _walk_descendants(element):
		if child.tag_name != 'option':
			continue
		text = ' '.join(child.get_all_children_text().split()) or ' '.join(child.attributes.get('value', '').split())
		if text and text not in options:
			options.append(text)
	return options


def _walk_descendants(node: EnhancedDOMTreeNode) -> list[EnhancedDOMTreeNode]:
	results: list[EnhancedDOMTreeNode] = []
	for child in node.children:
		results.append(child)
		results.extend(_walk_descendants(child))
	return results


def _is_required_field(element: EnhancedDOMTreeNode) -> bool:
	if 'required' in element.attributes:
		return True
	return normalize_text(element.attributes.get('aria-required', '')) == 'true'


def normalize_text(text: str) -> str:
	return ' '.join(text.lower().split())
