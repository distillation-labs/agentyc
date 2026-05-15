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
	'sitemap links',
	'footer links',
	'header links',
	'all anchors',
	'clickable links',
	'external links',
	'internal links',
	'social media links',
	'links on the page',
	'hyperlinks',
	'navigation urls',
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
	'data table',
	'price table',
	'comparison table',
	'grid data',
	'rows in the table',
	'column headers',
	'spreadsheet data',
	'data in the table',
	'products table',
	'pricing plans',
	'plan comparison',
	'feature comparison',
	'rate table',
	'schedule',
	'timetable',
	'leaderboard',
	'scoreboard',
	'rankings',
	'table data',
	'tabular',
)

LIST_QUERY_HINTS = (
	'checklist items',
	'list items',
	'bullet points',
	'ordered list',
	'unordered list',
	'list the steps',
	'steps in the list',
	'menu options',
	'dropdown items',
	'items on the page',
	'product list',
	'product items',
	'items in cart',
	'shopping cart items',
	'categories',
	'tags on',
	'list of',
	'options available',
	'available options',
	'navigation items',
	'steps to',
	'feature list',
	'benefits',
	'advantages',
	'requirements',
	'prerequisites',
	'todo items',
	'task list',
	'options in the',
	'items in the',
	'sidebar items',
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
	'product cards',
	'article cards',
	'blog posts',
	'articles on the page',
	'search listings',
	'recommended',
	'related items',
	'featured items',
	'all posts',
	'all articles',
	'news items',
	'news articles',
	'feed items',
	'cards on the page',
	'listings',
	'properties',
	'products on the page',
	'job listings',
	'courses',
	'videos on the page',
	'comments',
	'posts on the page',
	'items in the list',
	'links in the',
	'links on the',
	'page cards',
	'page results',
	'items in the nav',
	'breadcrumb',
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
	'all images',
	'product photo',
	'gallery images',
	'banner image',
	'hero image',
	'logo',
	'icon',
	'images on the page',
	'media',
	'visual content',
	'image links',
	'image list',
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
	'login form',
	'sign in form',
	'checkout form',
	'registration form',
	'contact form',
	'search form',
	'email field',
	'password field',
	'text inputs',
	'checkboxes',
	'radio buttons',
	'sign up form',
	'subscription form',
	'survey fields',
	'questionnaire',
	'form data',
	'submit button',
	'inputs on the page',
	'interactive fields',
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
	'product details',
	'order details',
	'account details',
	'user profile',
	'personal information',
	'pricing details',
	'plan details',
	'subscription details',
	'profile information',
	'order summary',
	'cart summary',
	'shipping details',
	'billing details',
	'invoice details',
	'item details',
	'listing details',
	'property details',
	'server details',
	'configuration',
	'settings',
	'status',
	'metrics',
	'specifications',
	'specs',
	'technical details',
	'system info',
	'environment variables',
	'environment details',
	'container details',
	'deployment info',
	'attributes',
	'detail panel',
	'summary panel',
	'info panel',
	'page details',
	'current settings',
	'current values',
	'overview panel',
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

ROUTE_EXAMPLES: dict[str, str] = {
	'tables': '"extract table", "table rows", "pricing table", "comparison table"',
	'lists': '"list items", "bullet points", "ordered list", "options in the menu"',
	'links': 'use extract_links=true with "all links", "page links", "footer links"',
	'link-collections': '"search results", "result cards", "product cards", "blog posts"',
	'images': '"product images", "all images", "thumbnails", "hero image"',
	'forms': '"form fields", "input fields", "login form", "checkboxes"',
	'key-value': '"key value pairs", "product details", "deployment details", "settings"',
}


def _build_no_route_error(query: str) -> str:
	examples = '\n'.join(f'  {family}: {hint}' for family, hint in ROUTE_EXAMPLES.items())
	return (
		f'No deterministic extraction route matched query: "{query}". '
		f'Supported routes with example queries:\n{examples}\n'
		f'For raw content use browser_get_state or browser_get_html.'
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
