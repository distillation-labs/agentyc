from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from agentyc.dom.views import EnhancedDOMTreeNode
from agentyc.tools.extraction.common import normalize_text
from agentyc.tools.extraction.form_fields import extract_form_fields
from agentyc.tools.extraction.markdown_parsers import (
	extract_key_value_pairs,
	extract_markdown_images,
	extract_markdown_link_collection_items,
	extract_markdown_links,
	extract_markdown_list_items,
	extract_markdown_tables,
)
from agentyc.tools.extraction.projection import (
	build_form_structured_payload,
	build_image_structured_payload,
	build_key_value_structured_payload,
	build_link_collection_structured_payload,
	build_list_structured_payload,
	build_table_structured_payload,
)


@dataclass(slots=True)
class DeterministicExtractionResult:
	content: str
	metadata: dict[str, Any]
	structured_data: dict[str, Any] | None = None


def build_deterministic_result(
	*,
	query: str,
	current_url: str,
	formatted_result: str,
	metadata: dict[str, Any],
) -> DeterministicExtractionResult:
	content = f'<url>\n{current_url}\n</url>\n<query>\n{query}\n</query>\n<result>\n{formatted_result}\n</result>'
	return DeterministicExtractionResult(content=content, metadata=metadata)


def build_structured_deterministic_result(
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


def append_partial_notice(*, lines: list[str], truncated: bool, content_stats: dict[str, Any]) -> None:
	if truncated and content_stats.get('next_start_char') is not None:
		lines.append(f'Partial result. Use start_from_char={content_stats["next_start_char"]} to continue.')


def format_deterministic_links_result(
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
	for link in extract_markdown_links(markdown=markdown, current_url=current_url):
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

	append_partial_notice(lines=lines, truncated=truncated, content_stats=content_stats)
	return build_deterministic_result(
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


def format_deterministic_link_collection_result(
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
	for item in extract_markdown_link_collection_items(markdown=markdown, current_url=current_url):
		title_key = normalize_text(item['title'])
		url_key = normalize_text(item['url'])
		if title_key in normalized_seen or url_key in normalized_seen:
			continue
		items.append(item)
	structured_data = build_link_collection_structured_payload(items=items, output_schema=output_schema)
	if output_schema is not None:
		if structured_data is None:
			return None
		return build_structured_deterministic_result(
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

	append_partial_notice(lines=lines, truncated=truncated, content_stats=content_stats)
	return build_deterministic_result(
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


def format_deterministic_table_result(
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
	tables = extract_markdown_tables(markdown)
	structured_data = build_table_structured_payload(tables=tables, output_schema=output_schema)
	if output_schema is not None:
		if structured_data is None:
			return None
		return build_structured_deterministic_result(
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

	append_partial_notice(lines=lines, truncated=truncated, content_stats=content_stats)
	return build_deterministic_result(
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


def format_deterministic_list_result(
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
	items = [item for item in extract_markdown_list_items(markdown) if normalize_text(item) not in normalized_seen]
	structured_data = build_list_structured_payload(items=items, output_schema=output_schema)
	if output_schema is not None:
		if structured_data is None:
			return None
		return build_structured_deterministic_result(
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

	append_partial_notice(lines=lines, truncated=truncated, content_stats=content_stats)
	return build_deterministic_result(
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


def format_deterministic_key_value_result(
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
	for pair in extract_key_value_pairs(markdown):
		key_text = normalize_text(pair['key'])
		value_text = normalize_text(pair['value'])
		if key_text in normalized_seen or value_text in normalized_seen:
			continue
		pairs.append(pair)
	structured_data = build_key_value_structured_payload(pairs=pairs, output_schema=output_schema)
	if output_schema is not None:
		if structured_data is None:
			return None
		return build_structured_deterministic_result(
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

	append_partial_notice(lines=lines, truncated=truncated, content_stats=content_stats)
	return build_deterministic_result(
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


def format_deterministic_form_result(
	*,
	query: str,
	current_url: str,
	selector_map: dict[int, EnhancedDOMTreeNode],
	output_schema: dict | None,
	already_collected: list[str],
) -> DeterministicExtractionResult | None:
	normalized_seen = {normalize_text(item) for item in already_collected if item.strip()}
	fields = []
	for field in extract_form_fields(selector_map):
		if normalize_text(field['label']) in normalized_seen:
			continue
		fields.append(field)
	structured_data = build_form_structured_payload(fields=fields, output_schema=output_schema)
	if output_schema is not None:
		if structured_data is None:
			return None
		return build_structured_deterministic_result(
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

	return build_deterministic_result(
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


def format_deterministic_image_result(
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
	for image in extract_markdown_images(markdown=markdown, current_url=current_url):
		alt_key = normalize_text(image['alt'])
		url_key = normalize_text(image['url'])
		if alt_key in normalized_seen or url_key in normalized_seen:
			continue
		images.append(image)
	structured_data = build_image_structured_payload(images=images, output_schema=output_schema)
	if output_schema is not None:
		if structured_data is None:
			return None
		return build_structured_deterministic_result(
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

	append_partial_notice(lines=lines, truncated=truncated, content_stats=content_stats)
	return build_deterministic_result(
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
