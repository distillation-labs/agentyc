import shutil
from pathlib import Path
from typing import Any

from agentyc.filesystem.errors import FileSystemError
from agentyc.filesystem.external_readers import read_external_file_structured
from agentyc.filesystem.file_types import BaseFile
from agentyc.filesystem.file_types import CsvFile
from agentyc.filesystem.file_types import DocxFile
from agentyc.filesystem.file_types import FILE_TYPE_CLASSES
from agentyc.filesystem.file_types import FILE_TYPE_NAME_MAP
from agentyc.filesystem.file_types import HtmlFile
from agentyc.filesystem.file_types import JsonFile
from agentyc.filesystem.file_types import JsonlFile
from agentyc.filesystem.file_types import MarkdownFile
from agentyc.filesystem.file_types import PdfFile
from agentyc.filesystem.file_types import TxtFile
from agentyc.filesystem.file_types import XmlFile
from agentyc.filesystem.filename_policy import build_filename_error_message
from agentyc.filesystem.filename_policy import is_valid_filename
from agentyc.filesystem.filename_policy import parse_filename
from agentyc.filesystem.filename_policy import resolve_filename
from agentyc.filesystem.filename_policy import sanitize_filename as _sanitize_filename
from agentyc.filesystem.state import FileSystemState

DEFAULT_FILE_SYSTEM_PATH = 'agentyc_agent_data'


class FileSystem:
	"""Enhanced file system with in-memory storage and multiple file type support."""

	def __init__(self, base_dir: str | Path, create_default_files: bool = True):
		self.base_dir = Path(base_dir) if isinstance(base_dir, str) else base_dir
		self.base_dir.mkdir(parents=True, exist_ok=True)

		self.data_dir = self.base_dir / DEFAULT_FILE_SYSTEM_PATH
		if self.data_dir.exists():
			shutil.rmtree(self.data_dir)
		self.data_dir.mkdir(exist_ok=True)

		self._file_types: dict[str, type[BaseFile]] = dict(FILE_TYPE_CLASSES)
		self.files: dict[str, BaseFile] = {}
		if create_default_files:
			self.default_files = ['todo.md']
			self._create_default_files()

		self.extracted_content_count = 0

	def get_allowed_extensions(self) -> list[str]:
		return list(self._file_types.keys())

	def _get_file_type_class(self, extension: str) -> type[BaseFile] | None:
		return self._file_types.get(extension.lower(), None)

	def _create_default_files(self) -> None:
		for full_filename in self.default_files:
			name_without_ext, extension = self._parse_filename(full_filename)
			file_class = self._get_file_type_class(extension)
			if not file_class:
				raise ValueError(f"Error: Invalid file extension '{extension}' for file '{full_filename}'.")

			file_obj = file_class(name=name_without_ext)
			self.files[full_filename] = file_obj
			file_obj.sync_to_disk_sync(self.data_dir)

	def _is_valid_filename(self, file_name: str) -> bool:
		return is_valid_filename(file_name, self.get_allowed_extensions())

	@staticmethod
	def sanitize_filename(file_name: str) -> str:
		return _sanitize_filename(file_name)

	def _resolve_filename(self, file_name: str) -> tuple[str, bool]:
		return resolve_filename(file_name, self.get_allowed_extensions())

	def _parse_filename(self, filename: str) -> tuple[str, str]:
		return parse_filename(filename)

	def get_dir(self) -> Path:
		return self.data_dir

	def get_file(self, full_filename: str) -> BaseFile | None:
		resolved, _ = self._resolve_filename(full_filename)
		if not self._is_valid_filename(resolved):
			return None
		return self.files.get(resolved)

	def list_files(self) -> list[str]:
		return [file_obj.full_name for file_obj in self.files.values()]

	def display_file(self, full_filename: str) -> str | None:
		resolved, _ = self._resolve_filename(full_filename)
		if not self._is_valid_filename(resolved):
			return None
		file_obj = self.files.get(resolved)
		return file_obj.read() if file_obj else None

	async def read_file_structured(self, full_filename: str, external_file: bool = False) -> dict[str, Any]:
		result: dict[str, Any] = {'message': '', 'images': None}
		if external_file:
			return await read_external_file_structured(full_filename, self.get_allowed_extensions())

		resolved, was_sanitized = self._resolve_filename(full_filename)
		if not self._is_valid_filename(resolved):
			result['message'] = build_filename_error_message(full_filename, self.get_allowed_extensions())
			return result

		file_obj = self.files.get(resolved)
		if not file_obj:
			if was_sanitized:
				result['message'] = f"File '{resolved}' not found. (Filename was auto-corrected from '{full_filename}')"
			else:
				result['message'] = f"File '{full_filename}' not found."
			return result

		try:
			content = file_obj.read()
			sanitize_note = f"Note: filename was auto-corrected from '{full_filename}' to '{resolved}'. " if was_sanitized else ''
			result['message'] = f'{sanitize_note}Read from file {resolved}.\n<content>\n{content}\n</content>'
			return result
		except FileSystemError as e:
			result['message'] = str(e)
			return result
		except Exception as e:
			result['message'] = f"Error: Could not read file '{full_filename}'. {str(e)}"
			return result

	async def read_file(self, full_filename: str, external_file: bool = False) -> str:
		result = await self.read_file_structured(full_filename, external_file)
		return result['message']

	async def write_file(self, full_filename: str, content: str) -> str:
		original_filename = full_filename
		resolved, was_sanitized = self._resolve_filename(full_filename)
		if not self._is_valid_filename(resolved):
			return build_filename_error_message(full_filename, self.get_allowed_extensions())
		full_filename = resolved

		try:
			name_without_ext, extension = self._parse_filename(full_filename)
			file_class = self._get_file_type_class(extension)
			if not file_class:
				raise ValueError(f"Error: Invalid file extension '{extension}' for file '{full_filename}'.")

			file_obj = self.files.get(full_filename)
			if file_obj is None:
				file_obj = file_class(name=name_without_ext)
				self.files[full_filename] = file_obj

			await file_obj.write(content, self.data_dir)
			sanitize_note = f" (auto-corrected from '{original_filename}')" if was_sanitized else ''
			return f'Data written to file {full_filename} successfully.{sanitize_note}'
		except FileSystemError as e:
			return str(e)
		except Exception as e:
			return f"Error: Could not write to file '{full_filename}'. {str(e)}"

	async def append_file(self, full_filename: str, content: str) -> str:
		original_filename = full_filename
		resolved, was_sanitized = self._resolve_filename(full_filename)
		if not self._is_valid_filename(resolved):
			return build_filename_error_message(full_filename, self.get_allowed_extensions())
		full_filename = resolved

		file_obj = self.files.get(full_filename)
		if not file_obj:
			if was_sanitized:
				return f"File '{full_filename}' not found. (Filename was auto-corrected from '{original_filename}')"
			return f"File '{full_filename}' not found."

		try:
			await file_obj.append(content, self.data_dir)
			sanitize_note = f" (auto-corrected from '{original_filename}')" if was_sanitized else ''
			return f'Data appended to file {full_filename} successfully.{sanitize_note}'
		except FileSystemError as e:
			return str(e)
		except Exception as e:
			return f"Error: Could not append to file '{full_filename}'. {str(e)}"

	async def replace_file_str(self, full_filename: str, old_str: str, new_str: str) -> str:
		original_filename = full_filename
		resolved, was_sanitized = self._resolve_filename(full_filename)
		if not self._is_valid_filename(resolved):
			return build_filename_error_message(full_filename, self.get_allowed_extensions())
		full_filename = resolved

		if not old_str:
			return 'Error: Cannot replace empty string. Please provide a non-empty string to replace.'

		file_obj = self.files.get(full_filename)
		if not file_obj:
			if was_sanitized:
				return f"File '{full_filename}' not found. (Filename was auto-corrected from '{original_filename}')"
			return f"File '{full_filename}' not found."

		try:
			content = file_obj.read().replace(old_str, new_str)
			await file_obj.write(content, self.data_dir)
			sanitize_note = f" (auto-corrected from '{original_filename}')" if was_sanitized else ''
			return f'Successfully replaced all occurrences of "{old_str}" with "{new_str}" in file {full_filename}{sanitize_note}'
		except FileSystemError as e:
			return str(e)
		except Exception as e:
			return f"Error: Could not replace string in file '{full_filename}'. {str(e)}"

	async def save_extracted_content(self, content: str) -> str:
		initial_filename = f'extracted_content_{self.extracted_content_count}'
		extracted_filename = f'{initial_filename}.md'
		file_obj = MarkdownFile(name=initial_filename)
		await file_obj.write(content, self.data_dir)
		self.files[extracted_filename] = file_obj
		self.extracted_content_count += 1
		return extracted_filename

	def describe(self) -> str:
		display_chars = 400
		description = ''
		for file_obj in self.files.values():
			if file_obj.full_name == 'todo.md':
				continue

			content = file_obj.read()
			if not content:
				description += f'<file>\n{file_obj.full_name} - [empty file]\n</file>\n'
				continue

			lines = content.splitlines()
			line_count = len(lines)
			whole_file_description = f'<file>\n{file_obj.full_name} - {line_count} lines\n<content>\n{content}\n</content>\n</file>\n'
			if len(content) < int(1.5 * display_chars):
				description += whole_file_description
				continue

			half_display_chars = display_chars // 2
			start_preview = ''
			start_line_count = 0
			chars_count = 0
			for line in lines:
				if chars_count + len(line) + 1 > half_display_chars:
					break
				start_preview += line + '\n'
				chars_count += len(line) + 1
				start_line_count += 1

			end_preview = ''
			end_line_count = 0
			chars_count = 0
			for line in reversed(lines):
				if chars_count + len(line) + 1 > half_display_chars:
					break
				end_preview = line + '\n' + end_preview
				chars_count += len(line) + 1
				end_line_count += 1

			middle_line_count = line_count - start_line_count - end_line_count
			if middle_line_count <= 0:
				description += whole_file_description
				continue

			start_preview = start_preview.strip('\n').rstrip()
			end_preview = end_preview.strip('\n').rstrip()
			if not (start_preview or end_preview):
				description += f'<file>\n{file_obj.full_name} - {line_count} lines\n<content>\n{middle_line_count} lines...\n</content>\n</file>\n'
			else:
				description += f'<file>\n{file_obj.full_name} - {line_count} lines\n<content>\n{start_preview}\n'
				description += f'... {middle_line_count} more lines ...\n'
				description += f'{end_preview}\n'
				description += '</content>\n</file>\n'

		return description.strip('\n')

	def get_todo_contents(self) -> str:
		todo_file = self.get_file('todo.md')
		return todo_file.read() if todo_file else ''

	def get_state(self) -> FileSystemState:
		files_data = {
			full_filename: {'type': file_obj.__class__.__name__, 'data': file_obj.model_dump()}
			for full_filename, file_obj in self.files.items()
		}
		return FileSystemState(
			files=files_data,
			base_dir=str(self.base_dir),
			extracted_content_count=self.extracted_content_count,
		)

	def nuke(self) -> None:
		shutil.rmtree(self.data_dir)

	@classmethod
	def from_state(cls, state: FileSystemState) -> 'FileSystem':
		fs = cls(base_dir=Path(state.base_dir), create_default_files=False)
		fs.extracted_content_count = state.extracted_content_count
		for full_filename, file_data in state.files.items():
			file_class = FILE_TYPE_NAME_MAP.get(file_data['type'])
			if not file_class:
				continue
			file_obj = file_class(**file_data['data'])
			fs.files[full_filename] = file_obj
			file_obj.sync_to_disk_sync(fs.data_dir)
		return fs


__all__ = [
	'BaseFile',
	'CsvFile',
	'DEFAULT_FILE_SYSTEM_PATH',
	'DocxFile',
	'FileSystem',
	'FileSystemError',
	'FileSystemState',
	'HtmlFile',
	'JsonFile',
	'JsonlFile',
	'MarkdownFile',
	'PdfFile',
	'TxtFile',
	'XmlFile',
]
