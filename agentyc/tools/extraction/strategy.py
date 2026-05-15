from __future__ import annotations

from typing import Literal

from agentyc.tools.extraction.common import normalize_text

DeterministicExtractionStrategy = Literal[
	'deterministic-links',
	'deterministic-link-collections',
	'deterministic-images',
	'deterministic-tables',
	'deterministic-lists',
	'deterministic-form-fields',
	'deterministic-key-values',
]

LINK_QUERY_HINTS = (
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
TABLE_QUERY_HINTS = (
	'pricing table',
	'extract the table',
	'extract table',
	'list the table rows',
	'list table rows',
	'table rows',
	'table columns',
	'what is in the table',
)
LIST_QUERY_HINTS = (
	'checklist items',
	'list items',
	'bullet points',
	'ordered list',
	'unordered list',
	'list the steps',
	'steps in the list',
)
LINK_COLLECTION_QUERY_HINTS = (
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
IMAGE_QUERY_HINTS = (
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
FORM_QUERY_HINTS = (
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
KEY_VALUE_QUERY_HINTS = (
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
NON_DETERMINISTIC_QUERY_HINTS = (
	'summarize',
	'summary',
	'describe',
	'explain',
	'compare',
	'analyze',
	'overview',
)


def should_use_deterministic_link_route(*, query: str, extract_links: bool) -> bool:
	if not extract_links:
		return False

	normalized_query = normalize_text(query)
	if normalized_query in {'links', 'urls', 'hrefs'}:
		return True
	return any(hint in normalized_query for hint in LINK_QUERY_HINTS)


def get_deterministic_extraction_strategy(
	*,
	query: str,
	extract_links: bool,
	output_schema: dict | None,
) -> DeterministicExtractionStrategy | None:
	normalized_query = normalize_text(query)
	if not normalized_query:
		return None
	if any(hint in normalized_query for hint in NON_DETERMINISTIC_QUERY_HINTS):
		return None
	if should_use_deterministic_link_route(query=query, extract_links=extract_links):
		return 'deterministic-links'
	if any(hint in normalized_query for hint in LINK_COLLECTION_QUERY_HINTS):
		return 'deterministic-link-collections'
	if any(hint in normalized_query for hint in IMAGE_QUERY_HINTS):
		return 'deterministic-images'
	if any(hint in normalized_query for hint in TABLE_QUERY_HINTS):
		return 'deterministic-tables'
	if any(hint in normalized_query for hint in KEY_VALUE_QUERY_HINTS):
		return 'deterministic-key-values'
	if any(hint in normalized_query for hint in FORM_QUERY_HINTS):
		return 'deterministic-form-fields'
	if any(hint in normalized_query for hint in LIST_QUERY_HINTS):
		return 'deterministic-lists'
	return None
