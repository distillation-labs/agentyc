"""Shared helpers for browser event models."""

from __future__ import annotations

import os


def _get_timeout(env_var: str, default: float) -> float | None:
	"""
	Safely parse environment variable timeout values with robust error handling.

	Args:
		env_var: Environment variable name (e.g. 'TIMEOUT_NavigateToUrlEvent')
		default: Default timeout value as float (e.g. 15.0)

	Returns:
		Parsed float value or the default if parsing fails

	Raises:
		ValueError: Only if both env_var and default are invalid (should not happen with valid defaults)
	"""
	# Try environment variable first
	env_value = os.getenv(env_var)
	if env_value:
		try:
			parsed = float(env_value)
			if parsed < 0:
				print(f'Warning: {env_var}={env_value} is negative, using default {default}')
				return default
			return parsed
		except (ValueError, TypeError):
			print(f'Warning: {env_var}={env_value} is not a valid number, using default {default}')

	# Fall back to default
	return default
