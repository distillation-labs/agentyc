import json
import logging
import re
from typing import Any

from agentyc.actions import ActionResult
from agentyc.browser import BrowserSession
from agentyc.filesystem.file_system import FileSystem

logger = logging.getLogger(__name__)


def register_file_and_eval_actions(tools: Any) -> None:
	@tools.registry.action(
		'Write content to a file. By default this OVERWRITES the entire file - use append=true to add to an existing file, or use replace_file for targeted edits within a file. FILENAME RULES: Use only letters, numbers, underscores, hyphens, dots, parentheses. Spaces are auto-converted to hyphens. SUPPORTED EXTENSIONS: .txt, .md, .json, .jsonl, .csv, .html, .xml, .pdf, .docx. CANNOT write binary/image files (.png, .jpg, .mp4, etc.) - do not attempt to save screenshots as files. For PDF files, write content in markdown format and it will be auto-converted to PDF.'
	)
	async def write_file(
		file_name: str,
		content: str,
		file_system: FileSystem,
		append: bool = False,
		trailing_newline: bool = True,
		leading_newline: bool = False,
	):
		if trailing_newline:
			content += '\n'
		if leading_newline:
			content = '\n' + content
		if append:
			result = await file_system.append_file(file_name, content)
		else:
			result = await file_system.write_file(file_name, content)

		resolved_name, _ = file_system._resolve_filename(file_name)
		file_path = file_system.get_dir() / resolved_name
		logger.info(f'💾 {result} File location: {file_path}')
		return ActionResult(extracted_content=result, long_term_memory=result)

	@tools.registry.action(
		'Replace specific text within a file by searching for old_str and replacing with new_str. Use this for targeted edits like updating todo checkboxes or modifying specific lines without rewriting the entire file.'
	)
	async def replace_file(file_name: str, old_str: str, new_str: str, file_system: FileSystem):
		result = await file_system.replace_file_str(file_name, old_str, new_str)
		logger.info(f'💾 {result}')
		return ActionResult(extracted_content=result, long_term_memory=result)

	@tools.registry.action(
		'Read the complete content of a file. Use this to view file contents before editing or to retrieve data from files. Supports text files (txt, md, json, csv, jsonl), documents (pdf, docx), and images (jpg, png).'
	)
	async def read_file(file_name: str, available_file_paths: list[str], file_system: FileSystem):
		if available_file_paths and file_name in available_file_paths:
			structured_result = await file_system.read_file_structured(file_name, external_file=True)
		else:
			structured_result = await file_system.read_file_structured(file_name)

		result = structured_result['message']
		images = structured_result.get('images')
		max_memory_size = 1000
		if images:
			memory = f'Read image file {file_name}'
		elif len(result) > max_memory_size:
			lines = result.splitlines()
			display = ''
			lines_count = 0
			for line in lines:
				if len(display) + len(line) < max_memory_size:
					display += line + '\n'
					lines_count += 1
				else:
					break
			remaining_lines = len(lines) - lines_count
			memory = f'{display}{remaining_lines} more lines...' if remaining_lines > 0 else display
		else:
			memory = result

		logger.info(f'💾 {memory}')
		return ActionResult(
			extracted_content=result,
			long_term_memory=memory,
			images=images,
			include_extracted_content_only_once=True,
		)

	@tools.registry.action(
		"""Execute browser JavaScript. Best practice: wrap in IIFE (function(){...})() with try-catch for safety. Use ONLY browser APIs (document, window, DOM). NO Node.js APIs (fs, require, process). Example: (function(){try{const el=document.querySelector('#id');return el?el.value:'not found'}catch(e){return 'Error: '+e.message}})() Avoid comments. Use for hover, drag, zoom, custom selectors, extract/filter links, or analysing page structure. IMPORTANT: Shadow DOM elements with [index] markers can be clicked directly with click(index) — do NOT use evaluate() to click them. Only use evaluate for shadow DOM elements that are NOT indexed. Limit output size.""",
		terminates_sequence=True,
	)
	async def evaluate(code: str, browser_session: BrowserSession):
		cdp_session = await browser_session.get_or_create_cdp_session()
		try:
			validated_code = tools._validate_and_fix_javascript(code)
			result = await cdp_session.cdp_client.send.Runtime.evaluate(
				params={'expression': validated_code, 'returnByValue': True, 'awaitPromise': True},
				session_id=cdp_session.session_id,
			)

			if result.get('exceptionDetails'):
				exception = result['exceptionDetails']
				error_msg = f'JavaScript execution error: {exception.get("text", "Unknown error")}'
				enhanced_msg = (
					'JavaScript Execution Failed:\n'
					f'{error_msg}\n\n'
					'Validated Code (after quote fixing):\n'
					f'{validated_code[:500]}{"..." if len(validated_code) > 500 else ""}\n'
				)
				logger.debug(enhanced_msg)
				return ActionResult(error=enhanced_msg)

			result_data = result.get('result', {})
			if result_data.get('wasThrown'):
				logger.debug(f'JavaScript code: {code} execution failed (wasThrown=true)')
				return ActionResult(error=f'JavaScript code: {code} execution failed (wasThrown=true)')

			value = result_data.get('value')
			if value is None:
				result_text = str(value) if 'value' in result_data else 'undefined'
			elif isinstance(value, (dict, list)):
				try:
					result_text = json.dumps(value, ensure_ascii=False)
				except (TypeError, ValueError):
					result_text = str(value)
			else:
				result_text = str(value)

			image_pattern = r'(data:image/[^;]+;base64,[A-Za-z0-9+/=]+)'
			found_images = re.findall(image_pattern, result_text)
			metadata = None
			if found_images:
				metadata = {'images': found_images}
				modified_text = result_text
				for image_data in found_images:
					modified_text = modified_text.replace(image_data, '[Image]')
				result_text = modified_text

			if len(result_text) > 20000:
				result_text = result_text[:19950] + '\n... [Truncated after 20000 characters]'

			logger.debug(f'JavaScript executed successfully, result length: {len(result_text)}')
			max_memory_length = 10000
			if len(result_text) < max_memory_length:
				memory = result_text
				include_extracted_content_only_once = False
			else:
				memory = f'JavaScript executed successfully, result length: {len(result_text)} characters.'
				include_extracted_content_only_once = True

			return ActionResult(
				extracted_content=result_text,
				long_term_memory=memory,
				include_extracted_content_only_once=include_extracted_content_only_once,
				metadata=metadata,
			)
		except Exception as error:
			logger.debug(f'JavaScript code that failed: {code[:200]}...')
			return ActionResult(error=f'Failed to execute JavaScript: {type(error).__name__}: {error}')
