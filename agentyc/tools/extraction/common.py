from __future__ import annotations

import re
from typing import Any

FIELD_COLLECTION_NAMES = {'fields', 'formfields', 'inputs', 'controls'}
LIST_COLLECTION_NAMES = {'items', 'steps', 'checklist', 'entries', 'results'}
TABLE_ROW_COLLECTION_NAMES = {'rows', 'tablerows', 'entries', 'items', 'results'}
COLUMN_COLLECTION_NAMES = {'columns', 'headers'}
LINK_COLLECTION_NAMES = {'results', 'items', 'links', 'entries', 'cards', 'pages', 'navigation', 'menuitems'}
KEY_VALUE_COLLECTION_NAMES = {'pairs', 'items', 'entries', 'properties', 'settings', 'details'}
KEY_VALUE_COUNT_NAMES = {'paircount', 'itemcount', 'entrycount', 'propertycount'}
FIELD_VALUE_NAMES = {
	'label': {'label', 'name', 'title'},
	'field_type': {'fieldtype', 'type', 'inputtype', 'kind'},
	'required': {'required', 'isrequired', 'mandatory'},
	'placeholder': {'placeholder', 'hint', 'example'},
	'options': {'options', 'choices', 'values'},
}
LIST_ITEM_VALUE_NAMES = {'text', 'item', 'label', 'step', 'value', 'name'}
LINK_ITEM_VALUE_NAMES = {
	'title': {'title', 'name', 'label', 'text'},
	'url': {'url', 'href', 'link', 'target'},
	'summary': {'summary', 'description', 'details', 'snippet'},
}
KEY_VALUE_FIELD_NAMES = {
	'key': {'key', 'name', 'label', 'property', 'field', 'setting'},
	'value': {'value', 'content', 'detail', 'status'},
}


def normalize_text(text: str) -> str:
	return ' '.join(text.lower().split())


def normalize_identifier(text: str) -> str:
	return re.sub(r'[^a-z0-9]+', '', normalize_text(text))


def find_matching_key(target: str, candidates: Any) -> str | None:
	normalized_target = normalize_identifier(target)
	for candidate in candidates:
		if normalize_identifier(candidate) == normalized_target:
			return candidate
	return None


def coerce_scalar_value(value: Any, prop_schema: dict[str, Any]) -> Any:
	type_name = prop_schema.get('type')
	if type_name == 'integer':
		if isinstance(value, str):
			match = re.search(r'-?\d+', value)
			return int(match.group()) if match else 0
		return int(value)
	if type_name == 'number':
		if isinstance(value, str):
			match = re.search(r'-?\d+(?:\.\d+)?', value.replace(',', ''))
			return float(match.group()) if match else 0.0
		return float(value)
	if type_name == 'boolean':
		if isinstance(value, str):
			return normalize_text(value) in {'true', 'yes', 'required', '1'}
		return bool(value)
	return value


def coerce_form_field_value(value: Any, prop_schema: dict[str, Any]) -> Any:
	if prop_schema.get('type') == 'array':
		return list(value) if isinstance(value, list) else [value]
	return coerce_scalar_value(value, prop_schema)
