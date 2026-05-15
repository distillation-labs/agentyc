from __future__ import annotations

import re
from typing import Any
from urllib.parse import urljoin

from agentyc.tools.extraction.common import normalize_text

MARKDOWN_LINK_PATTERN = re.compile(r'(?<!!)\[([^\]]*)\]\(([^)]+)\)')
MARKDOWN_IMAGE_PATTERN = re.compile(r'!\[([^\]]*)\]\(([^)\s]+)(?:\s+"([^"]*)")?\)')
MARKDOWN_TABLE_ROW_PATTERN = re.compile(r'^\s*\|.*\|\s*$')
MARKDOWN_LIST_ITEM_PATTERN = re.compile(r'^\s*(?:[-*+]|\d+[.)])\s+(.*)$')
MARKDOWN_LIST_CONTINUATION_PATTERN = re.compile(r'^\s{2,}\S')
KEY_VALUE_PATTERN = re.compile(r'^([^:\n]{1,80}?):\s+(.+)$')


def extract_markdown_links(*, markdown: str, current_url: str) -> list[dict[str, str]]:
	seen: set[tuple[str, str]] = set()
	results: list[dict[str, str]] = []
	for text, url in MARKDOWN_LINK_PATTERN.findall(markdown):
		normalized_url = urljoin(current_url, url.strip())
		normalized_text = ' '.join(text.split())
		key = (normalized_text, normalized_url)
		if key in seen:
			continue
		seen.add(key)
		results.append({'text': normalized_text, 'url': normalized_url})
	return results


def extract_markdown_images(*, markdown: str, current_url: str) -> list[dict[str, str]]:
	seen: set[tuple[str, str, str]] = set()
	results: list[dict[str, str]] = []
	for alt, url, title in MARKDOWN_IMAGE_PATTERN.findall(markdown):
		normalized_url = urljoin(current_url, url.strip())
		normalized_alt = ' '.join(alt.split())
		normalized_title = ' '.join(title.split())
		key = (normalized_alt, normalized_url, normalized_title)
		if key in seen:
			continue
		seen.add(key)
		results.append({'alt': normalized_alt, 'url': normalized_url, 'title': normalized_title})
	return results


def extract_markdown_tables(markdown: str) -> list[dict[str, Any]]:
	tables: list[dict[str, Any]] = []
	lines = markdown.splitlines()
	index = 0
	while index < len(lines) - 1:
		header_line = lines[index]
		separator_line = lines[index + 1]
		if not MARKDOWN_TABLE_ROW_PATTERN.match(header_line):
			index += 1
			continue
		if not (MARKDOWN_TABLE_ROW_PATTERN.match(separator_line) and '---' in separator_line):
			index += 1
			continue

		columns = split_markdown_table_row(header_line)
		index += 2
		rows: list[dict[str, str]] = []
		while index < len(lines) and MARKDOWN_TABLE_ROW_PATTERN.match(lines[index]):
			values = split_markdown_table_row(lines[index])
			if values:
				if len(values) < len(columns):
					values.extend([''] * (len(columns) - len(values)))
				row = {column: value for column, value in zip(columns, values, strict=False)}
				rows.append(row)
			index += 1
		tables.append({'columns': columns, 'rows': rows})
	return tables


def split_markdown_table_row(line: str) -> list[str]:
	return [part.strip() for part in line.strip().strip('|').split('|')]


def extract_markdown_list_items(markdown: str) -> list[str]:
	items: list[str] = []
	current_lines: list[str] = []
	for line in markdown.splitlines():
		match = MARKDOWN_LIST_ITEM_PATTERN.match(line)
		if match:
			if current_lines:
				items.append(' '.join(part for part in current_lines if part))
			current_lines = [match.group(1).strip()]
			continue
		if current_lines and MARKDOWN_LIST_CONTINUATION_PATTERN.match(line):
			current_lines.append(line.strip())
			continue
		if current_lines:
			items.append(' '.join(part for part in current_lines if part))
			current_lines = []
	if current_lines:
		items.append(' '.join(part for part in current_lines if part))
	return items


def extract_markdown_link_collection_items(*, markdown: str, current_url: str) -> list[dict[str, str]]:
	items: list[dict[str, str]] = []
	seen: set[tuple[str, str]] = set()
	for list_item in extract_markdown_list_items(markdown):
		matches = MARKDOWN_LINK_PATTERN.findall(list_item)
		if not matches:
			continue
		title, url = matches[0]
		normalized_url = urljoin(current_url, url.strip())
		normalized_title = ' '.join(title.split()) or normalized_url
		summary = MARKDOWN_LINK_PATTERN.sub(lambda match: ' '.join(match.group(1).split()), list_item)
		summary = summary.replace(normalized_title, '', 1)
		summary = ' '.join(summary.strip(' -:;,.').split())
		key = (normalize_text(normalized_title), normalize_text(normalized_url))
		if key in seen:
			continue
		seen.add(key)
		items.append({'title': normalized_title, 'url': normalized_url, 'summary': summary})
	if items:
		return items

	for link in extract_markdown_links(markdown=markdown, current_url=current_url):
		title = link['text'] or link['url']
		key = (normalize_text(title), normalize_text(link['url']))
		if key in seen:
			continue
		seen.add(key)
		items.append({'title': title, 'url': link['url'], 'summary': ''})
	return items


def extract_key_value_pairs(markdown: str) -> list[dict[str, str]]:
	pairs: list[dict[str, str]] = []
	seen: set[tuple[str, str]] = set()
	for raw_line in markdown.splitlines():
		line = raw_line.strip()
		if not line:
			continue
		list_match = MARKDOWN_LIST_ITEM_PATTERN.match(line)
		if list_match:
			line = list_match.group(1).strip()
		line = MARKDOWN_LINK_PATTERN.sub(lambda match: ' '.join(match.group(1).split()), line)
		match = KEY_VALUE_PATTERN.match(line)
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
