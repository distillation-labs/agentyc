import logging
import re

from dotenv import load_dotenv

load_dotenv()

# Pre-compiled regex for URL detection - used in URL shortening
URL_PATTERN = re.compile(r'https?://[^\s<>"\']+|www\.[^\s<>"\']+|[^\s<>"\']+\.[a-z]{2,}(?:/[^\s<>"\']*)?', re.IGNORECASE)

logger = logging.getLogger(__name__)

# Lazy import for error types
# Use sentinel to avoid retrying import when package is not installed
_IMPORT_NOT_FOUND: type = type('_ImportNotFound', (), {})
_openai_bad_request_error: type | None = None
_groq_bad_request_error: type | None = None


def _get_openai_bad_request_error() -> type | None:
	"""Lazy loader for OpenAI BadRequestError."""
	global _openai_bad_request_error
	if _openai_bad_request_error is None:
		try:
			from openai import BadRequestError

			_openai_bad_request_error = BadRequestError
		except ImportError:
			_openai_bad_request_error = _IMPORT_NOT_FOUND
	return _openai_bad_request_error if _openai_bad_request_error is not _IMPORT_NOT_FOUND else None


def _get_groq_bad_request_error() -> type | None:
	"""Lazy loader for Groq BadRequestError."""
	global _groq_bad_request_error
	if _groq_bad_request_error is None:
		try:
			from groq import BadRequestError  # type: ignore[import-not-found]

			_groq_bad_request_error = BadRequestError
		except ImportError:
			_groq_bad_request_error = _IMPORT_NOT_FOUND
	return _groq_bad_request_error if _groq_bad_request_error is not _IMPORT_NOT_FOUND else None
