def collect_sensitive_data_values(sensitive_data: dict[str, str | dict[str, str]] | None) -> dict[str, str]:
	"""Flatten legacy and domain-scoped sensitive data into placeholder -> value mappings."""
	if not sensitive_data:
		return {}

	sensitive_values: dict[str, str] = {}
	for key_or_domain, content in sensitive_data.items():
		if isinstance(content, dict):
			for key, val in content.items():
				if val:
					sensitive_values[key] = val
		elif content:
			sensitive_values[key_or_domain] = content

	return sensitive_values


def redact_sensitive_string(value: str, sensitive_values: dict[str, str]) -> str:
	"""Replace sensitive values with placeholders, longest matches first to avoid partial leaks."""
	for key, secret in sorted(sensitive_values.items(), key=lambda item: len(item[1]), reverse=True):
		value = value.replace(secret, f'<secret>{key}</secret>')
	return value


def sanitize_surrogates(text: str) -> str:
	"""Remove surrogate characters that can't be encoded in UTF-8."""
	return text.encode('utf-8', errors='ignore').decode('utf-8')
