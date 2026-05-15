"""Pure helpers for the downloads watchdog."""

from __future__ import annotations

import os


def check_url_for_pdf(url: str) -> bool:
	"""Check if URL indicates a PDF file."""
	if not url:
		return False

	url_lower = url.lower()
	if url_lower.endswith('.pdf'):
		return True
	if '.pdf' in url_lower:
		return True
	if any(
		param in url_lower
		for param in [
			'content-type=application/pdf',
			'content-type=application%2fpdf',
			'mimetype=application/pdf',
			'type=application/pdf',
		]
	):
		return True
	return False


def is_chrome_pdf_viewer_url(url: str) -> bool:
	"""Check if this is Chrome's internal PDF viewer URL."""
	if not url:
		return False

	url_lower = url.lower()
	if 'chrome-extension://' in url_lower and 'pdf' in url_lower:
		return True
	if url_lower.startswith('chrome://') and 'pdf' in url_lower:
		return True
	return False


async def get_unique_filename(directory: str, filename: str) -> str:
	"""Generate a unique filename for downloads by appending counters."""
	base, ext = os.path.splitext(filename)
	counter = 1
	new_filename = filename
	while os.path.exists(os.path.join(directory, new_filename)):
		new_filename = f'{base} ({counter}){ext}'
		counter += 1
	return new_filename
