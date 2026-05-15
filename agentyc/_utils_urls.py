import logging
from fnmatch import fnmatch
from pathlib import Path
from urllib.parse import urlparse


def is_unsafe_pattern(pattern: str) -> bool:
	"""Check if a domain pattern has complex wildcards that could match too many domains."""
	if '://' in pattern:
		_, pattern = pattern.split('://', 1)

	bare_domain = pattern.replace('.*', '').replace('*.', '')
	return '*' in bare_domain


def is_new_tab_page(url: str) -> bool:
	"""Check if a URL is a browser new-tab page."""
	return url in ('about:blank', 'chrome://new-tab-page/', 'chrome://new-tab-page', 'chrome://newtab/', 'chrome://newtab')


def match_url_with_domain_pattern(url: str, domain_pattern: str, log_warnings: bool = False) -> bool:
	"""Check if a URL matches a domain pattern. SECURITY CRITICAL."""
	try:
		if is_new_tab_page(url):
			return False

		parsed_url = urlparse(url)
		scheme = parsed_url.scheme.lower() if parsed_url.scheme else ''
		domain = parsed_url.hostname.lower() if parsed_url.hostname else ''

		if not scheme or not domain:
			return False

		domain_pattern = domain_pattern.lower()
		if '://' in domain_pattern:
			pattern_scheme, pattern_domain = domain_pattern.split('://', 1)
		else:
			pattern_scheme = 'https'
			pattern_domain = domain_pattern

		if ':' in pattern_domain and not pattern_domain.startswith(':'):
			pattern_domain = pattern_domain.split(':', 1)[0]

		if not fnmatch(scheme, pattern_scheme):
			return False

		if pattern_domain == '*' or domain == pattern_domain:
			return True

		if '*' in pattern_domain:
			if pattern_domain.count('*.') > 1 or pattern_domain.count('.*') > 1:
				if log_warnings:
					logging.getLogger(__name__).error(
						f'⛔️ Multiple wildcards in pattern=[{domain_pattern}] are not supported'
					)
				return False

			if pattern_domain.endswith('.*'):
				if log_warnings:
					logging.getLogger(__name__).error(
						f'⛔️ Wildcard TLDs like in pattern=[{domain_pattern}] are not supported for security'
					)
				return False

			bare_domain = pattern_domain.replace('*.', '')
			if '*' in bare_domain:
				if log_warnings:
					logging.getLogger(__name__).error(
						f'⛔️ Only *.domain style patterns are supported, ignoring pattern=[{domain_pattern}]'
					)
				return False

			if pattern_domain.startswith('*.'):
				parent_domain = pattern_domain[2:]
				if domain == parent_domain or fnmatch(domain, parent_domain):
					return True

			if fnmatch(domain, pattern_domain):
				return True

		return False
	except Exception as e:
		logging.getLogger(__name__).error(
			f'⛔️ Error matching URL {url} with pattern {domain_pattern}: {type(e).__name__}: {e}'
		)
		return False


def _log_pretty_path(path: str | Path | None) -> str:
	"""Pretty-print a path, shorten home dir to ~ and cwd to ."""
	if not path or not str(path).strip():
		return ''
	if not isinstance(path, (str, Path)):
		return f'<{type(path).__name__}>'

	pretty_path = str(path).replace(str(Path.home()), '~').replace(str(Path.cwd().resolve()), '.')
	if pretty_path.strip() and ' ' in pretty_path:
		pretty_path = f'"{pretty_path}"'
	return pretty_path


def _log_pretty_url(s: str, max_len: int | None = 22) -> str:
	"""Truncate/pretty-print a URL with a maximum length, removing the protocol and www. prefix."""
	s = s.replace('https://', '').replace('http://', '').replace('www.', '')
	if max_len is not None and len(s) > max_len:
		return s[:max_len] + '…'
	return s
