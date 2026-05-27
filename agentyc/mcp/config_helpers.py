"""Small config accessors shared by MCP components."""

from __future__ import annotations

from typing import Any


def get_default_profile(config: dict[str, Any]) -> dict[str, Any]:
	"""Get default browser profile from a loaded config dict."""
	return config.get('browser_profile', {})


def get_default_llm(config: dict[str, Any]) -> dict[str, Any]:
	"""Get default LLM config from a loaded config dict."""
	return config.get('llm', {})


__all__ = ['get_default_llm', 'get_default_profile']
