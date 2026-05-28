from typing import Any


def fix_gemini_schema(schema: dict[str, Any]) -> dict[str, Any]:
	"""
	Convert a Pydantic model to a Gemini-compatible schema.

	This function removes unsupported properties like 'additionalProperties' and resolves
	$ref references that Gemini doesn't support.
	"""

	# Handle $defs and $ref resolution
	if '$defs' in schema:
		defs = schema.pop('$defs')

		def resolve_refs(obj: Any) -> Any:
			if isinstance(obj, dict):
				if '$ref' in obj:
					ref = obj.pop('$ref')
					ref_name = ref.split('/')[-1]
					if ref_name in defs:
						# Replace the reference with the actual definition
						resolved = defs[ref_name].copy()
						# Merge any additional properties from the reference
						for key, value in obj.items():
							if key != '$ref':
								resolved[key] = value
						return resolve_refs(resolved)
					return obj
				else:
					# Recursively process all dictionary values
					return {k: resolve_refs(v) for k, v in obj.items()}
			elif isinstance(obj, list):
				return [resolve_refs(item) for item in obj]
			return obj

		schema = resolve_refs(schema)

	# Remove unsupported properties
	def clean_schema(obj: Any, parent_key: str | None = None) -> Any:
		if isinstance(obj, dict):
			# Remove unsupported properties
			cleaned = {}
			for key, value in obj.items():
				# Only strip 'title' when it's a JSON Schema metadata field (not inside 'properties')
				# 'title' as a metadata field appears at schema level, not as a property name
				is_metadata_title = key == 'title' and parent_key != 'properties'
				if key not in ['additionalProperties', 'default'] and not is_metadata_title:
					cleaned_value = clean_schema(value, parent_key=key)
					# Handle empty object properties - Gemini doesn't allow empty OBJECT types
					if (
						key == 'properties'
						and isinstance(cleaned_value, dict)
						and len(cleaned_value) == 0
						and isinstance(obj.get('type', ''), str)
						and obj.get('type', '').upper() == 'OBJECT'
					):
						# Convert empty object to have at least one property
						cleaned['properties'] = {'_placeholder': {'type': 'string'}}
					else:
						cleaned[key] = cleaned_value

			# If this is an object type with empty properties, add a placeholder
			if (
				isinstance(cleaned.get('type', ''), str)
				and cleaned.get('type', '').upper() == 'OBJECT'
				and 'properties' in cleaned
				and isinstance(cleaned['properties'], dict)
				and len(cleaned['properties']) == 0
			):
				cleaned['properties'] = {'_placeholder': {'type': 'string'}}

			return cleaned
		elif isinstance(obj, list):
			return [clean_schema(item, parent_key=parent_key) for item in obj]
		return obj

	return clean_schema(schema)
