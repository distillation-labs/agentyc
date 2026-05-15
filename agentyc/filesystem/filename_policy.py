import os
import re

UNSUPPORTED_BINARY_EXTENSIONS = {
	'png',
	'jpg',
	'jpeg',
	'gif',
	'bmp',
	'svg',
	'webp',
	'ico',
	'mp3',
	'mp4',
	'wav',
	'avi',
	'mov',
	'zip',
	'tar',
	'gz',
	'rar',
	'exe',
	'bin',
	'dll',
	'so',
}


def build_filename_error_message(file_name: str, supported_extensions: list[str]) -> str:
	"""Build a specific error message explaining why the filename was rejected and how to fix it."""
	base = os.path.basename(file_name)
	if '.' in base:
		_, ext = base.rsplit('.', 1)
		ext_lower = ext.lower()
		if ext_lower in UNSUPPORTED_BINARY_EXTENSIONS:
			return (
				f"Error: Cannot write binary/image file '{base}'. "
				f'The write_file tool only supports text-based files. '
				f'Supported extensions: {", ".join("." + e for e in supported_extensions)}. '
				f'For screenshots, the browser automatically captures them - do not try to save screenshots as files.'
			)
		if ext_lower not in supported_extensions:
			return (
				f"Error: Unsupported file extension '.{ext_lower}' in '{base}'. "
				f'Supported extensions: {", ".join("." + e for e in supported_extensions)}. '
				f'Please rename the file to use a supported extension.'
			)

	if '.' not in base:
		return (
			f"Error: Filename '{base}' has no extension. "
			f'Please add a supported extension: {", ".join("." + e for e in supported_extensions)}.'
		)

	return (
		f"Error: Invalid filename '{base}'. "
		f'Filenames must contain only letters, numbers, underscores, hyphens, dots, parentheses, and spaces. '
		f'Supported extensions: {", ".join("." + e for e in supported_extensions)}.'
	)


def is_valid_filename(file_name: str, allowed_extensions: list[str]) -> bool:
	"""Check if filename matches the required pattern: name.extension."""
	extensions = '|'.join(allowed_extensions)
	pattern = rf'^[a-zA-Z0-9_\-\.\(\) \u4e00-\u9fff]+\.({extensions})$'
	file_name_base = os.path.basename(file_name)
	if not re.match(pattern, file_name_base):
		return False
	name_part = file_name_base.rsplit('.', 1)[0]
	return len(name_part.strip()) > 0


def sanitize_filename(file_name: str) -> str:
	"""Sanitize a filename by replacing/removing invalid characters."""
	base = os.path.basename(file_name)
	if '.' not in base:
		return base

	name_part, ext = base.rsplit('.', 1)
	name_part = name_part.replace(' ', '-')
	name_part = re.sub(r'[^a-zA-Z0-9_\-\.\(\)\u4e00-\u9fff]', '', name_part)
	name_part = re.sub(r'-{2,}', '-', name_part)
	name_part = name_part.strip('-.')
	if not name_part:
		name_part = 'file'
	return f'{name_part}.{ext.lower()}'


def resolve_filename(file_name: str, allowed_extensions: list[str]) -> tuple[str, bool]:
	"""Resolve a filename, attempting sanitization if the original is invalid."""
	base_name = os.path.basename(file_name)
	was_changed = base_name != file_name
	if is_valid_filename(base_name, allowed_extensions):
		return base_name, was_changed

	sanitized = sanitize_filename(base_name)
	if sanitized != base_name and is_valid_filename(sanitized, allowed_extensions):
		return sanitized, True

	return base_name, was_changed


def parse_filename(filename: str) -> tuple[str, str]:
	"""Parse filename into name and extension."""
	name, extension = filename.rsplit('.', 1)
	return name, extension.lower()
