import logging
import math
import os

from agentyc.actions import ActionResult
from agentyc.browser.views import BrowserError
from agentyc.dom.service import EnhancedDOMTreeNode

logger = logging.getLogger(__name__)

_ACTION_TIMEOUT_FALLBACK_S = 180.0
_COMPACT_ACTION_CONTEXT_MIN_CHARS = 3000
_SUMMARY_QUERY_HINTS = ('summarize', 'summary', 'describe', 'overview')
_ACTION_SUMMARY_QUERY_HINTS = (
	'action',
	'actions',
	'control',
	'controls',
	'navigation',
	'nav',
	'call to action',
	'cta',
	'quickstart',
	'button',
	'buttons',
	'link',
	'links',
)


def is_auth_like_llm_error(exc: Exception) -> bool:
	"""Return True for provider failures that look like missing/invalid credentials."""
	for attr_name in ('status_code', 'code'):
		code = getattr(exc, attr_name, None)
		if code in {401, 403}:
			return True

	response = getattr(exc, 'response', None)
	if getattr(response, 'status_code', None) in {401, 403}:
		return True

	message = f'{type(exc).__name__}: {exc}'.lower()
	return any(
		fragment in message
		for fragment in (
			'invalid api key',
			'incorrect api key',
			'missing api key',
			'no api key',
			'unauthorized',
			'authentication failed',
			'authentication error',
			'api key',
			'credential',
		)
	)


def parse_env_action_timeout(raw: str | None) -> float:
	"""Parse AGENTYC_ACTION_TIMEOUT_S defensively."""
	if raw is None or raw == '':
		return _ACTION_TIMEOUT_FALLBACK_S
	try:
		parsed = float(raw)
	except ValueError:
		logger.warning(
			'Invalid AGENTYC_ACTION_TIMEOUT_S=%r; falling back to %.0fs',
			raw,
			_ACTION_TIMEOUT_FALLBACK_S,
		)
		return _ACTION_TIMEOUT_FALLBACK_S
	if not math.isfinite(parsed) or parsed <= 0:
		logger.warning(
			'AGENTYC_ACTION_TIMEOUT_S=%r is not a finite positive number; falling back to %.0fs',
			raw,
			_ACTION_TIMEOUT_FALLBACK_S,
		)
		return _ACTION_TIMEOUT_FALLBACK_S
	return parsed


_DEFAULT_ACTION_TIMEOUT_S = parse_env_action_timeout(os.getenv('AGENTYC_ACTION_TIMEOUT_S'))


def coerce_valid_action_timeout(value: float | None) -> float:
	"""Normalize a caller-supplied action_timeout to a finite positive value."""
	if value is None:
		return _DEFAULT_ACTION_TIMEOUT_S
	if not math.isfinite(value) or value <= 0:
		logger.warning(
			'action_timeout=%r is not a finite positive number; falling back to %.0fs',
			value,
			_DEFAULT_ACTION_TIMEOUT_S,
		)
		return _DEFAULT_ACTION_TIMEOUT_S
	return float(value)


def should_use_compact_action_context(query: str, *, output_schema: dict | None) -> bool:
	if output_schema is not None:
		return False
	normalized = query.lower()
	return any(hint in normalized for hint in _SUMMARY_QUERY_HINTS) and any(
		hint in normalized for hint in _ACTION_SUMMARY_QUERY_HINTS
	)


def collect_markdown_headings(markdown: str, *, limit: int = 8) -> list[str]:
	headings: list[str] = []
	for line in markdown.splitlines():
		stripped = line.strip()
		if not stripped.startswith('#'):
			continue
		heading = stripped.lstrip('#').strip()
		if heading:
			headings.append(heading)
		if len(headings) >= limit:
			break
	return headings


def collect_markdown_intro_lines(markdown: str, *, limit: int = 6) -> list[str]:
	intro_lines: list[str] = []
	for line in markdown.splitlines():
		stripped = line.strip()
		if not stripped or stripped.startswith('#') or stripped.startswith('|') or stripped.startswith('- '):
			continue
		intro_lines.append(stripped)
		if len(intro_lines) >= limit:
			break
	return intro_lines


def build_compact_action_context(
	*,
	query: str,
	current_url: str,
	markdown: str,
	selector_map: dict[int, EnhancedDOMTreeNode],
) -> str | None:
	if not selector_map:
		return None
	from agentyc.mcp.state import select_elements_for_min_mode, summarize_interactive_element

	selected_elements = (
		select_elements_for_min_mode(selector_map=selector_map, max_elements=18)
		if len(selector_map) > 18
		else list(selector_map.values())
	)
	if not selected_elements:
		return None

	lines = [f'Query: {query}', f'URL: {current_url}']
	headings = collect_markdown_headings(markdown)
	if headings:
		lines.append('Headings:')
		lines.extend(f'- {heading}' for heading in headings)
	intro_lines = collect_markdown_intro_lines(markdown)
	if intro_lines:
		lines.append('Intro copy:')
		lines.extend(f'- {line}' for line in intro_lines)
	lines.append('Interactive elements:')
	for element in selected_elements:
		summary = summarize_interactive_element(element)
		parts = [summary.get('tag', 'element')]
		if summary.get('text'):
			parts.append(str(summary['text']))
		if summary.get('context'):
			parts.append(f'context={summary["context"]}')
		if summary.get('placeholder'):
			parts.append(f'placeholder={summary["placeholder"]}')
		if summary.get('href'):
			parts.append(f'href={summary["href"]}')
		lines.append('- ' + ' | '.join(parts))
	return '\n'.join(lines)



	for domain_or_key, content in sensitive_data.items():
		if isinstance(content, dict):
			for key, value in content.items():
				if value and value == text:
					return key
		elif content and content == text:
			return domain_or_key

	return None


def handle_browser_error(error: BrowserError) -> ActionResult:
	if error.long_term_memory is not None:
		if error.short_term_memory is not None:
			return ActionResult(
				extracted_content=error.short_term_memory,
				error=error.long_term_memory,
				include_extracted_content_only_once=True,
			)
		return ActionResult(error=error.long_term_memory)

	logger.warning(
		'⚠️ A BrowserError was raised without long_term_memory - always set long_term_memory when raising BrowserError to propagate right messages to LLM.'
	)
	raise error
