def redact_sensitive_string(value: str, sensitive_values: dict[str, str]) -> str:
	"""Replace sensitive values with placeholders, longest matches first to avoid partial leaks."""
	for key, secret in sorted(sensitive_values.items(), key=lambda item: len(item[1]), reverse=True):
		value = value.replace(secret, f'<secret>{key}</secret>')
	return value


def sanitize_surrogates(text: str) -> str:
	"""Remove surrogate characters that can't be encoded in UTF-8."""
	return text.encode('utf-8', errors='ignore').decode('utf-8')
