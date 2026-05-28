import logging
import re
from typing import Any

import pyotp
from pydantic import BaseModel

from agentyc.utils import is_new_tab_page, match_url_with_domain_pattern


logger = logging.getLogger('agentyc.tools.registry.service')


def log_sensitive_data_usage(placeholders_used: set[str], current_url: str | None) -> None:
	"""Log when sensitive data is being used on a page."""
	if placeholders_used:
		url_info = f' on {current_url}' if current_url and not is_new_tab_page(current_url) else ''
		logger.info(f'🔒 Using sensitive data placeholders: {", ".join(sorted(placeholders_used))}{url_info}')


def replace_sensitive_data(
	params: BaseModel,
	sensitive_data: dict[str, Any],
	current_url: str | None = None,
) -> BaseModel:
	"""Replace sensitive-data placeholders in params with actual values."""
	secret_pattern = re.compile(r'<secret>(.*?)</secret>')
	all_missing_placeholders = set()
	replaced_placeholders = set()
	applicable_secrets = {}

	for domain_or_key, content in sensitive_data.items():
		if isinstance(content, dict):
			if current_url and not is_new_tab_page(current_url):
				if match_url_with_domain_pattern(current_url, domain_or_key):
					applicable_secrets.update(content)
		else:
			applicable_secrets[domain_or_key] = content

	applicable_secrets = {key: value for key, value in applicable_secrets.items() if value}

	def recursively_replace_secrets(value: str | dict | list) -> str | dict | list:
		if isinstance(value, str):
			matches = secret_pattern.findall(value)
			for placeholder in matches:
				if placeholder in applicable_secrets:
					if placeholder.endswith('bu_2fa_code'):
						totp = pyotp.TOTP(applicable_secrets[placeholder], digits=6)
						replacement_value = totp.now()
					else:
						replacement_value = applicable_secrets[placeholder]

					value = value.replace(f'<secret>{placeholder}</secret>', replacement_value)
					replaced_placeholders.add(placeholder)
				else:
					all_missing_placeholders.add(placeholder)

			if value in applicable_secrets:
				placeholder_name = value
				if placeholder_name.endswith('bu_2fa_code'):
					totp = pyotp.TOTP(applicable_secrets[placeholder_name], digits=6)
					value = totp.now()
				else:
					value = applicable_secrets[placeholder_name]
				replaced_placeholders.add(placeholder_name)

			return value
		if isinstance(value, dict):
			return {key: recursively_replace_secrets(item) for key, item in value.items()}
		if isinstance(value, list):
			return [recursively_replace_secrets(item) for item in value]
		return value

	params_dump = params.model_dump()
	processed_params = recursively_replace_secrets(params_dump)
	log_sensitive_data_usage(replaced_placeholders, current_url)
	if all_missing_placeholders:
		logger.warning(f'Missing or empty keys in sensitive_data dictionary: {", ".join(all_missing_placeholders)}')
	return type(params).model_validate(processed_params)
