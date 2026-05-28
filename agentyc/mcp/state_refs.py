"""Element ref helpers for MCP state payloads."""

from __future__ import annotations

import re

_ELEMENT_REF_PATTERN = re.compile(r'^(?:e)?([1-9]\d*)$')


def make_element_ref(backend_node_id: int) -> str:
	return f'e{backend_node_id}'


def parse_element_ref(ref: str) -> int:
	normalized = ref.strip().lower()
	match = _ELEMENT_REF_PATTERN.fullmatch(normalized)
	if not match:
		raise ValueError(f'Invalid element ref: {ref!r}. Expected e123 or 123.')
	return int(match.group(1))


__all__ = ['make_element_ref', 'parse_element_ref']
