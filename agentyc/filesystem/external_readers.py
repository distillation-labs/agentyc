import base64
import math
import os
import re
from typing import Any


async def read_external_file_structured(full_filename: str, supported_extensions: list[str]) -> dict[str, Any]:
	"""Read an external file and return structured content and image data when applicable."""
	result: dict[str, Any] = {'message': '', 'images': None}

	try:
		try:
			_, extension = full_filename.rsplit('.', 1)
			extension = extension.lower()
		except Exception:
			result['message'] = (
				f'Error: Invalid filename format {full_filename}. Must be alphanumeric with a supported extension.'
			)
			return result

		special_extensions = {'docx', 'pdf', 'jpg', 'jpeg', 'png'}
		text_extensions = [ext for ext in supported_extensions if ext not in special_extensions]

		if extension in text_extensions:
			import anyio

			async with await anyio.open_file(full_filename, 'r') as f:
				content = await f.read()
				result['message'] = f'Read from file {full_filename}.\n<content>\n{content}\n</content>'
				return result

		if extension == 'docx':
			from docx import Document

			doc = Document(full_filename)
			content = '\n'.join([para.text for para in doc.paragraphs])
			result['message'] = f'Read from file {full_filename}.\n<content>\n{content}\n</content>'
			return result

		if extension == 'pdf':
			import pypdf

			reader = pypdf.PdfReader(full_filename)
			num_pages = len(reader.pages)
			max_chars = 60000
			page_texts: list[tuple[int, str]] = []
			total_chars = 0

			for i, page in enumerate(reader.pages, 1):
				text = page.extract_text() or ''
				page_texts.append((i, text))
				total_chars += len(text)

			if total_chars <= max_chars:
				content_parts = []
				for page_num, text in page_texts:
					if text.strip():
						content_parts.append(f'--- Page {page_num} ---\n{text}')
				extracted_text = '\n\n'.join(content_parts)
				result['message'] = (
					f'Read from file {full_filename} ({num_pages} pages, {total_chars:,} chars).\n'
					f'<content>\n{extracted_text}\n</content>'
				)
				return result

			word_to_pages: dict[str, set[int]] = {}
			page_words: dict[int, set[str]] = {}
			for page_num, text in page_texts:
				words = set(re.findall(r'\b[a-zA-Z]{4,}\b', text.lower()))
				page_words[page_num] = words
				for word in words:
					if word not in word_to_pages:
						word_to_pages[word] = set()
					word_to_pages[word].add(page_num)

			page_scores: dict[int, float] = {}
			for page_num, words in page_words.items():
				score = 0.0
				for word in words:
					score += math.log(num_pages / len(word_to_pages[word]))
				page_scores[page_num] = score

			sorted_pages = sorted(page_scores.items(), key=lambda x: -x[1])
			priority_pages = [1]
			for page_num, _ in sorted_pages:
				if page_num not in priority_pages:
					priority_pages.append(page_num)
			for page_num, _ in page_texts:
				if page_num not in priority_pages:
					priority_pages.append(page_num)

			content_parts = []
			chars_used = 0
			pages_included = []
			for page_num in priority_pages:
				text = page_texts[page_num - 1][1]
				if not text.strip():
					continue
				page_header = f'--- Page {page_num} ---\n'
				truncation_suffix = '\n[...truncated]'
				remaining = max_chars - chars_used
				min_useful = len(page_header) + len(truncation_suffix) + 50
				if remaining < min_useful:
					break
				page_content = page_header + text
				if len(page_content) > remaining:
					page_content = page_content[: remaining - len(truncation_suffix)] + truncation_suffix
				content_parts.append((page_num, page_content))
				chars_used += len(page_content)
				pages_included.append(page_num)
				if chars_used >= max_chars:
					break

			content_parts.sort(key=lambda x: x[0])
			extracted_text = '\n\n'.join(part for _, part in content_parts)
			if num_pages - len(pages_included) > 0:
				skipped = [p for p in range(1, num_pages + 1) if p not in pages_included]
				truncation_note = (
					f'\n\n[Showing {len(pages_included)} of {num_pages} pages. '
					f'Skipped pages: {skipped[:10]}{"..." if len(skipped) > 10 else ""}. '
					f'Use extract with start_from_char to read further into the file.]'
				)
			else:
				truncation_note = ''

			result['message'] = (
				f'Read from file {full_filename} ({num_pages} pages, {total_chars:,} chars total).\n'
				f'<content>\n{extracted_text}{truncation_note}\n</content>'
			)
			return result

		if extension in ['jpg', 'jpeg', 'png']:
			import anyio

			async with await anyio.open_file(full_filename, 'rb') as f:
				img_data = await f.read()

			base64_str = base64.b64encode(img_data).decode('utf-8')
			result['message'] = f'Read image file {full_filename}.'
			result['images'] = [{'name': os.path.basename(full_filename), 'data': base64_str}]
			return result

		result['message'] = f'Error: Cannot read file {full_filename} as {extension} extension is not supported.'
		return result
	except FileNotFoundError:
		result['message'] = f"Error: File '{full_filename}' not found."
		return result
	except PermissionError:
		result['message'] = f"Error: Permission denied to read file '{full_filename}'."
		return result
	except Exception as e:
		result['message'] = f"Error: Could not read file '{full_filename}'. {str(e)}"
		return result
