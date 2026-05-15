from collections.abc import Iterable
from urllib.parse import urlparse

from agentyc.utils import logger


def validate_url(url: str, schemes: Iterable[str] = ()) -> str:
	"""Validate URL format and optionally check for specific schemes."""
	parsed_url = urlparse(url)
	if not parsed_url.netloc:
		raise ValueError(f'Invalid URL format: {url}')
	if schemes and parsed_url.scheme and parsed_url.scheme.lower() not in schemes:
		raise ValueError(f'URL has invalid scheme: {url} (expected one of {schemes})')
	return url


def validate_float_range(value: float, min_val: float, max_val: float) -> float:
	"""Validate that float is within specified range."""
	if not min_val <= value <= max_val:
		raise ValueError(f'Value {value} outside of range {min_val}-{max_val}')
	return value


def validate_cli_arg(arg: str) -> str:
	"""Validate that arg is a valid CLI argument."""
	if not arg.startswith('--'):
		raise ValueError(f'Invalid CLI argument: {arg} (should start with --, e.g. --some-key="some value here")')
	return arg


def optimize_large_domain_list(
	domains: list[str] | set[str] | None,
	threshold: int,
) -> list[str] | set[str] | None:
	"""Convert large domain lists to sets for faster exact lookups."""
	if domains is None or isinstance(domains, set):
		return domains

	if len(domains) >= threshold:
		logger.warning(
			f'🔧 Optimizing domain list with {len(domains)} items to set for O(1) lookup. '
			f'Note: Pattern matching (*.domain.com, etc.) is not supported for lists >= {threshold} items. '
			f'Use exact domains only or keep list size < {threshold} for pattern support.'
		)
		return set(domains)

	return domains
