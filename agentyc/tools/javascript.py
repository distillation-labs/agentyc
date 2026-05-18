import json
import logging
import re

logger = logging.getLogger(__name__)

SEARCH_PAGE_JS_BODY = """\
try {
	var scope = CSS_SCOPE ? document.querySelector(CSS_SCOPE) : document.body;
	if (!scope) {
		return {error: 'CSS scope selector not found: ' + CSS_SCOPE, matches: [], total: 0};
	}
	var walker = document.createTreeWalker(scope, NodeFilter.SHOW_TEXT);
	var fullText = '';
	var nodeOffsets = [];
	while (walker.nextNode()) {
		var node = walker.currentNode;
		var text = node.textContent;
		if (text && text.trim()) {
			nodeOffsets.push({offset: fullText.length, length: text.length, node: node});
			fullText += text;
		}
	}
	var re;
	try {
		var flags = CASE_SENSITIVE ? 'g' : 'gi';
		if (IS_REGEX) {
			re = new RegExp(PATTERN, flags);
		} else {
			re = new RegExp(PATTERN.replace(/[.*+?^${}()|[\\]\\\\]/g, '\\\\$&'), flags);
		}
	} catch (e) {
		return {error: 'Invalid regex pattern: ' + e.message, matches: [], total: 0};
	}
	var matches = [];
	var match;
	var totalFound = 0;
	while ((match = re.exec(fullText)) !== null) {
		totalFound++;
		if (matches.length < MAX_RESULTS) {
			var start = Math.max(0, match.index - CONTEXT_CHARS);
			var end = Math.min(fullText.length, match.index + match[0].length + CONTEXT_CHARS);
			var context = fullText.slice(start, end);
			var elementPath = '';
			for (var i = 0; i < nodeOffsets.length; i++) {
				var no = nodeOffsets[i];
				if (no.offset <= match.index && no.offset + no.length > match.index) {
					elementPath = _getPath(no.node.parentElement);
					break;
				}
			}
			matches.push({
				match_text: match[0],
				context: (start > 0 ? '...' : '') + context + (end < fullText.length ? '...' : ''),
				element_path: elementPath,
				char_position: match.index
			});
		}
		if (match[0].length === 0) re.lastIndex++;
	}
	return {matches: matches, total: totalFound, has_more: totalFound > MAX_RESULTS};
} catch (e) {
	return {error: 'search_page error: ' + e.message, matches: [], total: 0};
}
function _getPath(el) {
	var parts = [];
	var current = el;
	while (current && current !== document.body && current !== document) {
		var desc = current.tagName ? current.tagName.toLowerCase() : '';
		if (!desc) break;
		if (current.id) desc += '#' + current.id;
		else if (current.className && typeof current.className === 'string') {
			var classes = current.className.trim().split(/\\s+/).slice(0, 2).join('.');
			if (classes) desc += '.' + classes;
		}
		parts.unshift(desc);
		current = current.parentElement;
	}
	return parts.join(' > ');
}
"""

FIND_ELEMENTS_JS_BODY = """\
try {
	function _compactText(value) {
		return String(value || '').replace(/\\s+/g, ' ').trim();
	}
	function _readReferencedText(ids) {
		if (!ids) {
			return '';
		}
		var seen = {};
		var parts = [];
		var tokens = String(ids).trim().split(/\\s+/);
		for (var k = 0; k < tokens.length; k++) {
			var refEl = document.getElementById(tokens[k]);
			var refText = refEl ? _compactText(refEl.textContent || '') : '';
			if (refText && !seen[refText]) {
				seen[refText] = true;
				parts.push(refText);
			}
		}
		return _compactText(parts.join(' '));
	}
	function _fallbackText(el) {
		var candidates = [];
		var seen = {};
		function addCandidate(value) {
			var text = _compactText(value);
			if (!text || seen[text]) {
				return;
			}
			seen[text] = true;
			candidates.push(text);
		}
		addCandidate(el.getAttribute('aria-label'));
		addCandidate(_readReferencedText(el.getAttribute('aria-labelledby')));
		if (el.labels && el.labels.length) {
			for (var labelIndex = 0; labelIndex < el.labels.length; labelIndex++) {
				addCandidate(el.labels[labelIndex].textContent || '');
			}
		}
		if (typeof el.closest === 'function') {
			var wrapperLabel = el.closest('label');
			if (wrapperLabel) {
				addCandidate(wrapperLabel.textContent || '');
			}
		}
		addCandidate(el.getAttribute('placeholder'));
		addCandidate(el.getAttribute('name'));
		addCandidate(el.getAttribute('title'));
		return candidates.length > 0 ? candidates[0] : '';
	}
	var elements;
	try {
		elements = document.querySelectorAll(SELECTOR);
	} catch (e) {
		return {error: 'Invalid CSS selector: ' + e.message, elements: [], total: 0};
	}
	var total = elements.length;
	var limit = Math.min(total, MAX_RESULTS);
	var results = [];
	for (var i = 0; i < limit; i++) {
		var el = elements[i];
		var item = {index: i, tag: el.tagName.toLowerCase()};
		if (INCLUDE_TEXT) {
			var text = _compactText(el.textContent || '');
			if (!text && item.tag === 'input') {
				var inputType = (el.getAttribute('type') || '').toLowerCase();
				if (inputType === 'button' || inputType === 'submit' || inputType === 'reset') {
					text = _compactText(el.value || el.getAttribute('value') || '');
				}
			}
			if (!text) {
				text = _fallbackText(el);
			}
			item.text = text.length > 300 ? text.slice(0, 300) + '...' : text;
		}
		if (ATTRIBUTES && ATTRIBUTES.length > 0) {
			item.attrs = {};
			for (var j = 0; j < ATTRIBUTES.length; j++) {
				var attrName = ATTRIBUTES[j];
				var val;
				if ((attrName === 'src' || attrName === 'href') && typeof el[attrName] === 'string' && el[attrName] !== '') {
					val = el[attrName];
				} else {
					val = el.getAttribute(attrName);
				}
				if (val !== null) {
					item.attrs[attrName] = val.length > 500 ? val.slice(0, 500) + '...' : val;
				}
			}
		}
		item.children_count = el.children.length;
		results.push(item);
	}
	return {elements: results, total: total, showing: limit};
} catch (e) {
	return {error: 'find_elements error: ' + e.message, elements: [], total: 0};
}
"""


def build_search_page_js(
	pattern: str,
	regex: bool,
	case_sensitive: bool,
	context_chars: int,
	css_scope: str | None,
	max_results: int,
) -> str:
	params_js = (
		f'var PATTERN = {json.dumps(pattern)};\n'
		f'var IS_REGEX = {json.dumps(regex)};\n'
		f'var CASE_SENSITIVE = {json.dumps(case_sensitive)};\n'
		f'var CONTEXT_CHARS = {json.dumps(context_chars)};\n'
		f'var CSS_SCOPE = {json.dumps(css_scope)};\n'
		f'var MAX_RESULTS = {json.dumps(max_results)};\n'
	)
	return '(function() {\n' + params_js + SEARCH_PAGE_JS_BODY + '\n})()'


def build_find_elements_js(
	selector: str,
	attributes: list[str] | None,
	max_results: int,
	include_text: bool,
) -> str:
	params_js = (
		f'var SELECTOR = {json.dumps(selector)};\n'
		f'var ATTRIBUTES = {json.dumps(attributes)};\n'
		f'var MAX_RESULTS = {json.dumps(max_results)};\n'
		f'var INCLUDE_TEXT = {json.dumps(include_text)};\n'
	)
	return '(function() {\n' + params_js + FIND_ELEMENTS_JS_BODY + '\n})()'


def format_search_results(data: dict, pattern: str) -> str:
	if not isinstance(data, dict):
		return f'search_page returned unexpected result: {data}'

	matches = data.get('matches', [])
	total = data.get('total', 0)
	has_more = data.get('has_more', False)

	if total == 0:
		return f'No matches found for "{pattern}" on page.'

	lines = [f'Found {total} match{"es" if total != 1 else ""} for "{pattern}" on page:']
	lines.append('')
	for i, match in enumerate(matches):
		context = match.get('context', '')
		path = match.get('element_path', '')
		location = f' (in {path})' if path else ''
		lines.append(f'[{i + 1}] {context}{location}')

	if has_more:
		lines.append(f'\n... showing {len(matches)} of {total} total matches. Increase max_results to see more.')

	return '\n'.join(lines)


def format_find_results(data: dict, selector: str) -> str:
	if not isinstance(data, dict):
		return f'find_elements returned unexpected result: {data}'

	elements = data.get('elements', [])
	total = data.get('total', 0)
	showing = data.get('showing', 0)

	if total == 0:
		return f'No elements found matching "{selector}".'

	lines = [f'Found {total} element{"s" if total != 1 else ""} matching "{selector}":']
	lines.append('')
	for element in elements:
		idx = element.get('index', 0)
		tag = element.get('tag', '?')
		text = element.get('text', '')
		attrs = element.get('attrs', {})
		children = element.get('children_count', 0)

		parts = [f'[{idx}] <{tag}>']
		if text:
			display_text = ' '.join(text.split())
			if len(display_text) > 120:
				display_text = display_text[:120] + '...'
			parts.append(f'"{display_text}"')
		if attrs:
			attr_strs = [f'{key}="{value}"' for key, value in attrs.items()]
			parts.append('{' + ', '.join(attr_strs) + '}')
		parts.append(f'({children} children)')
		lines.append(' '.join(parts))

	if showing < total:
		lines.append(f'\nShowing {showing} of {total} total elements. Increase max_results to see more.')

	return '\n'.join(lines)


def validate_and_fix_javascript(code: str) -> str:
	fixed_code = code
	fixed_code = re.sub(r'\\\\([dDsSwWbBnrtfv])', r'\\\1', fixed_code)
	fixed_code = re.sub(r'\\\\([.*+?^${}()|[\]])', r'\\\1', fixed_code)

	xpath_pattern = r'document\.evaluate\s*\(\s*"([^"]*)"\s*,'

	def fix_xpath_quotes(match: re.Match[str]) -> str:
		xpath_with_quotes = match.group(1)
		return f'document.evaluate(`{xpath_with_quotes}`,'

	fixed_code = re.sub(xpath_pattern, fix_xpath_quotes, fixed_code)

	selector_pattern = r'(querySelector(?:All)?)\s*\(\s*"([^"]*)"\s*\)'

	def fix_selector_quotes(match: re.Match[str]) -> str:
		method_name = match.group(1)
		selector_with_quotes = match.group(2)
		return f'{method_name}(`{selector_with_quotes}`)'

	fixed_code = re.sub(selector_pattern, fix_selector_quotes, fixed_code)

	closest_pattern = r'\.closest\s*\(\s*"([^"]*)"\s*\)'

	def fix_closest_quotes(match: re.Match[str]) -> str:
		selector_with_quotes = match.group(1)
		return f'.closest(`{selector_with_quotes}`)'

	fixed_code = re.sub(closest_pattern, fix_closest_quotes, fixed_code)

	matches_pattern = r'\.matches\s*\(\s*"([^"]*)"\s*\)'

	def fix_matches_quotes(match: re.Match[str]) -> str:
		selector_with_quotes = match.group(1)
		return f'.matches(`{selector_with_quotes}`)'

	fixed_code = re.sub(matches_pattern, fix_matches_quotes, fixed_code)

	changes_made = []
	if '`' in fixed_code and '`' not in code:
		changes_made.append('converted mixed quotes to template literals')

	if changes_made:
		logger.debug(f'JavaScript fixes applied: {", ".join(changes_made)}')

	return fixed_code
