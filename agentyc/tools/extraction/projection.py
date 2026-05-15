from __future__ import annotations

from typing import Any

from agentyc.tools.extraction.common import (
	COLUMN_COLLECTION_NAMES,
	FIELD_COLLECTION_NAMES,
	FIELD_VALUE_NAMES,
	KEY_VALUE_COLLECTION_NAMES,
	KEY_VALUE_COUNT_NAMES,
	KEY_VALUE_FIELD_NAMES,
	LINK_COLLECTION_NAMES,
	LINK_ITEM_VALUE_NAMES,
	LIST_COLLECTION_NAMES,
	LIST_ITEM_VALUE_NAMES,
	TABLE_ROW_COLLECTION_NAMES,
	coerce_form_field_value,
	coerce_scalar_value,
	find_matching_key,
	normalize_identifier,
)


def build_table_structured_payload(*, tables: list[dict[str, Any]], output_schema: dict | None) -> dict[str, Any] | None:
	if output_schema is None:
		return None
	properties = output_schema.get('properties', {})
	if not properties:
		return None
	first_table = tables[0] if tables else {'columns': [], 'rows': []}
	result: dict[str, Any] = {}
	matched = False
	single_array_fallback = len(properties) == 1
	for prop_name, prop_schema in properties.items():
		normalized = normalize_identifier(prop_name)
		value: Any | None = None
		if normalized in COLUMN_COLLECTION_NAMES:
			value = list(first_table['columns'])
		elif normalized in TABLE_ROW_COLLECTION_NAMES:
			value = project_table_rows(rows=first_table['rows'], prop_schema=prop_schema)
		elif normalized == 'tables':
			value = project_tables(tables=tables, prop_schema=prop_schema)
		elif normalized == 'tablecount':
			value = len(tables)
		elif normalized == 'rowcount':
			value = sum(len(table['rows']) for table in tables)
		elif single_array_fallback and prop_schema.get('type') == 'array':
			value = project_table_rows(rows=first_table['rows'], prop_schema=prop_schema)
		elif normalized in {'table', 'data'} and prop_schema.get('type') == 'object':
			value = project_single_table(table=first_table, schema=prop_schema)
		if value is None:
			continue
		result[prop_name] = value
		matched = True
	return result if matched else None


def build_list_structured_payload(*, items: list[str], output_schema: dict | None) -> dict[str, Any] | None:
	if output_schema is None:
		return None
	properties = output_schema.get('properties', {})
	if not properties:
		return None
	result: dict[str, Any] = {}
	matched = False
	single_array_fallback = len(properties) == 1
	for prop_name, prop_schema in properties.items():
		normalized = normalize_identifier(prop_name)
		value: Any | None = None
		if normalized in LIST_COLLECTION_NAMES:
			value = project_list_items(items=items, prop_schema=prop_schema)
		elif normalized in {'itemcount', 'stepcount'}:
			value = len(items)
		elif single_array_fallback and prop_schema.get('type') == 'array':
			value = project_list_items(items=items, prop_schema=prop_schema)
		if value is None:
			continue
		result[prop_name] = value
		matched = True
	return result if matched else None


def build_link_collection_structured_payload(*, items: list[dict[str, str]], output_schema: dict | None) -> dict[str, Any] | None:
	if output_schema is None:
		return None
	properties = output_schema.get('properties', {})
	if not properties:
		return None
	result: dict[str, Any] = {}
	matched = False
	single_array_fallback = len(properties) == 1
	for prop_name, prop_schema in properties.items():
		normalized = normalize_identifier(prop_name)
		value: Any | None = None
		if normalized in LINK_COLLECTION_NAMES:
			value = project_link_collection_items(items=items, prop_schema=prop_schema)
		elif normalized in {'resultcount', 'itemcount', 'linkcount', 'entrycount', 'pagecount'}:
			value = len(items)
		elif single_array_fallback and prop_schema.get('type') == 'array':
			value = project_link_collection_items(items=items, prop_schema=prop_schema)
		if value is None:
			continue
		result[prop_name] = value
		matched = True
	return result if matched else None


def build_image_structured_payload(*, images: list[dict[str, str]], output_schema: dict | None) -> dict[str, Any] | None:
	if output_schema is None:
		return None
	properties = output_schema.get('properties', {})
	if not properties:
		return None
	result: dict[str, Any] = {}
	matched = False
	single_array_fallback = len(properties) == 1
	for prop_name, prop_schema in properties.items():
		normalized = normalize_identifier(prop_name)
		value: Any | None = None
		if normalized in {'images', 'items', 'results', 'entries', 'cards', 'products'}:
			value = project_image_items(images=images, prop_schema=prop_schema)
		elif normalized in {'imagecount', 'itemcount', 'resultcount', 'entrycount', 'productcount'}:
			value = len(images)
		elif normalized in {'image', 'picture', 'photo', 'thumbnail'} and prop_schema.get('type') == 'object':
			value = project_single_image(image=images[0], schema=prop_schema) if images else None
		elif single_array_fallback and prop_schema.get('type') == 'array':
			value = project_image_items(images=images, prop_schema=prop_schema)
		if value is None:
			continue
		result[prop_name] = value
		matched = True
	return result if matched else None


def build_key_value_structured_payload(*, pairs: list[dict[str, str]], output_schema: dict | None) -> dict[str, Any] | None:
	if output_schema is None:
		return None
	properties = output_schema.get('properties', {})
	if not properties:
		return None
	normalized_pairs = {normalize_identifier(pair['key']): pair['value'] for pair in pairs}
	result: dict[str, Any] = {}
	matched = False
	single_array_fallback = len(properties) == 1
	for prop_name, prop_schema in properties.items():
		normalized = normalize_identifier(prop_name)
		value: Any | None = None
		if normalized in KEY_VALUE_COLLECTION_NAMES:
			value = project_key_value_pairs(pairs=pairs, prop_schema=prop_schema)
		elif normalized in KEY_VALUE_COUNT_NAMES:
			value = len(pairs)
		elif normalized in normalized_pairs:
			value = coerce_scalar_value(normalized_pairs[normalized], prop_schema)
		elif prop_schema.get('type') == 'object':
			value = project_key_value_object(pairs=pairs, prop_schema=prop_schema)
		elif single_array_fallback and prop_schema.get('type') == 'array':
			value = project_key_value_pairs(pairs=pairs, prop_schema=prop_schema)
		if value is None:
			continue
		result[prop_name] = value
		matched = True
	return result if matched else None


def build_form_structured_payload(*, fields: list[dict[str, Any]], output_schema: dict | None) -> dict[str, Any] | None:
	if output_schema is None:
		return None
	properties = output_schema.get('properties', {})
	if not properties:
		return None
	result: dict[str, Any] = {}
	matched = False
	single_array_fallback = len(properties) == 1
	for prop_name, prop_schema in properties.items():
		normalized = normalize_identifier(prop_name)
		value: Any | None = None
		if normalized in FIELD_COLLECTION_NAMES:
			value = project_form_fields(fields=fields, prop_schema=prop_schema)
		elif normalized == 'fieldcount':
			value = len(fields)
		elif single_array_fallback and prop_schema.get('type') == 'array':
			value = project_form_fields(fields=fields, prop_schema=prop_schema)
		if value is None:
			continue
		result[prop_name] = value
		matched = True
	return result if matched else None


def project_image_items(*, images: list[dict[str, str]], prop_schema: dict[str, Any]) -> list[Any] | None:
	if prop_schema.get('type') != 'array':
		return None
	item_schema = prop_schema.get('items', {})
	if item_schema.get('type') == 'string':
		return [image['url'] for image in images]
	if item_schema.get('type') == 'object' or item_schema.get('properties'):
		return [project_single_image(image=image, schema=item_schema) for image in images]
	return list(images)


def project_single_image(*, image: dict[str, str], schema: dict[str, Any]) -> dict[str, Any]:
	properties = schema.get('properties', {})
	if not properties:
		return dict(image)
	result: dict[str, Any] = {}
	for prop_name, prop_schema in properties.items():
		normalized = normalize_identifier(prop_name)
		if normalized in {'url', 'src', 'href', 'link'}:
			result[prop_name] = coerce_scalar_value(image['url'], prop_schema)
			continue
		if normalized == 'title':
			result[prop_name] = coerce_scalar_value(image['title'] or image['alt'] or image['url'], prop_schema)
			continue
		if normalized in {'alt', 'label', 'name', 'caption'}:
			result[prop_name] = coerce_scalar_value(image['alt'] or image['title'] or image['url'], prop_schema)
	return result


def project_link_collection_items(*, items: list[dict[str, str]], prop_schema: dict[str, Any]) -> list[Any] | None:
	if prop_schema.get('type') != 'array':
		return None
	item_schema = prop_schema.get('items', {})
	if item_schema.get('type') == 'string':
		return [item['title'] for item in items]
	if item_schema.get('type') == 'object' or item_schema.get('properties'):
		return [project_link_collection_item(item=item, item_schema=item_schema) for item in items]
	return list(items)


def project_link_collection_item(*, item: dict[str, str], item_schema: dict[str, Any]) -> dict[str, Any]:
	properties = item_schema.get('properties', {})
	if not properties:
		return dict(item)
	result: dict[str, Any] = {}
	for prop_name, prop_schema in properties.items():
		normalized = normalize_identifier(prop_name)
		source_key = None
		for canonical_key, aliases in LINK_ITEM_VALUE_NAMES.items():
			if normalized in aliases:
				source_key = canonical_key
				break
		if source_key is None:
			continue
		result[prop_name] = coerce_scalar_value(item[source_key], prop_schema)
	return result


def project_key_value_pairs(*, pairs: list[dict[str, str]], prop_schema: dict[str, Any]) -> list[Any] | None:
	if prop_schema.get('type') != 'array':
		return None
	item_schema = prop_schema.get('items', {})
	if item_schema.get('type') == 'string':
		return [f'{pair["key"]}: {pair["value"]}' for pair in pairs]
	if item_schema.get('type') == 'object' or item_schema.get('properties'):
		return [project_key_value_pair(pair=pair, item_schema=item_schema) for pair in pairs]
	return list(pairs)


def project_key_value_pair(*, pair: dict[str, str], item_schema: dict[str, Any]) -> dict[str, Any]:
	properties = item_schema.get('properties', {})
	if not properties:
		return dict(pair)
	result: dict[str, Any] = {}
	for prop_name, prop_schema in properties.items():
		normalized = normalize_identifier(prop_name)
		if normalized in KEY_VALUE_FIELD_NAMES['key']:
			result[prop_name] = coerce_scalar_value(pair['key'], prop_schema)
			continue
		if normalized in KEY_VALUE_FIELD_NAMES['value']:
			result[prop_name] = coerce_scalar_value(pair['value'], prop_schema)
			continue
		if normalized == normalize_identifier(pair['key']):
			result[prop_name] = coerce_scalar_value(pair['value'], prop_schema)
	return result


def project_key_value_object(*, pairs: list[dict[str, str]], prop_schema: dict[str, Any]) -> dict[str, Any]:
	properties = prop_schema.get('properties', {})
	if not properties:
		return {pair['key']: pair['value'] for pair in pairs}
	pair_lookup = {normalize_identifier(pair['key']): pair['value'] for pair in pairs}
	result: dict[str, Any] = {}
	for prop_name, nested_schema in properties.items():
		normalized = normalize_identifier(prop_name)
		if normalized not in pair_lookup:
			continue
		result[prop_name] = coerce_scalar_value(pair_lookup[normalized], nested_schema)
	return result


def project_tables(*, tables: list[dict[str, Any]], prop_schema: dict[str, Any]) -> list[dict[str, Any]] | None:
	if prop_schema.get('type') != 'array':
		return None
	item_schema = prop_schema.get('items', {})
	return [project_single_table(table=table, schema=item_schema) for table in tables]


def project_single_table(*, table: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
	properties = schema.get('properties', {})
	if not properties:
		return {'columns': table['columns'], 'rows': table['rows']}
	result: dict[str, Any] = {}
	for prop_name, prop_schema in properties.items():
		normalized = normalize_identifier(prop_name)
		value: Any | None = None
		if normalized in COLUMN_COLLECTION_NAMES:
			value = list(table['columns'])
		elif normalized in TABLE_ROW_COLLECTION_NAMES:
			value = project_table_rows(rows=table['rows'], prop_schema=prop_schema)
		elif normalized == 'rowcount':
			value = len(table['rows'])
		if value is None:
			continue
		result[prop_name] = value
	return result


def project_table_rows(*, rows: list[dict[str, str]], prop_schema: dict[str, Any]) -> list[Any] | None:
	if prop_schema.get('type') != 'array':
		return None
	item_schema = prop_schema.get('items', {})
	if item_schema.get('type') == 'string':
		return ['; '.join(f'{key}: {value}' for key, value in row.items()) for row in rows]
	if item_schema.get('type') == 'object' or item_schema.get('properties'):
		return [project_table_row(row=row, item_schema=item_schema) for row in rows]
	return list(rows)


def project_table_row(*, row: dict[str, str], item_schema: dict[str, Any]) -> dict[str, Any]:
	properties = item_schema.get('properties', {})
	if not properties:
		return dict(row)
	result: dict[str, Any] = {}
	for prop_name, prop_schema in properties.items():
		source_key = find_matching_key(prop_name, row.keys())
		if source_key is None:
			continue
		result[prop_name] = coerce_scalar_value(row[source_key], prop_schema)
	return result


def project_list_items(*, items: list[str], prop_schema: dict[str, Any]) -> list[Any] | None:
	if prop_schema.get('type') != 'array':
		return None
	item_schema = prop_schema.get('items', {})
	if item_schema.get('type') == 'string' or not item_schema:
		return list(items)
	if item_schema.get('type') == 'object' or item_schema.get('properties'):
		return [project_list_item(item=item, item_schema=item_schema) for item in items]
	return None


def project_list_item(*, item: str, item_schema: dict[str, Any]) -> dict[str, Any]:
	properties = item_schema.get('properties', {})
	if not properties:
		return {'text': item}
	result: dict[str, Any] = {}
	for prop_name, prop_schema in properties.items():
		normalized = normalize_identifier(prop_name)
		if normalized in LIST_ITEM_VALUE_NAMES:
			result[prop_name] = coerce_scalar_value(item, prop_schema)
	return result


def project_form_fields(*, fields: list[dict[str, Any]], prop_schema: dict[str, Any]) -> list[Any] | None:
	if prop_schema.get('type') != 'array':
		return None
	item_schema = prop_schema.get('items', {})
	if item_schema.get('type') == 'string':
		return [field['label'] for field in fields]
	if item_schema.get('type') == 'object' or item_schema.get('properties'):
		return [project_form_field(field=field, item_schema=item_schema) for field in fields]
	return list(fields)


def project_form_field(*, field: dict[str, Any], item_schema: dict[str, Any]) -> dict[str, Any]:
	properties = item_schema.get('properties', {})
	if not properties:
		return dict(field)
	result: dict[str, Any] = {}
	for prop_name, prop_schema in properties.items():
		normalized = normalize_identifier(prop_name)
		source_key = None
		for canonical_key, aliases in FIELD_VALUE_NAMES.items():
			if normalized in aliases:
				source_key = canonical_key
				break
		if source_key is None:
			continue
		result[prop_name] = coerce_form_field_value(field[source_key], prop_schema)
	return result
