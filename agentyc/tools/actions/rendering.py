import asyncio
import base64
import logging
import os
import re
from typing import Any

import anyio

from agentyc.actions import ActionResult
from agentyc.browser import BrowserSession
from agentyc.filesystem.file_system import FileSystem
from agentyc.tools.views import SaveAsPdfAction, ScreenshotAction

logger = logging.getLogger(__name__)


def register_rendering_actions(tools: Any) -> None:
	@tools.registry.action(
		'Take a screenshot of the current viewport. If file_name is provided, saves to that file and returns the path. Otherwise, screenshot is included in the next browser_state observation.',
		param_model=ScreenshotAction,
	)
	async def screenshot(
		params: ScreenshotAction,
		browser_session: BrowserSession,
		file_system: FileSystem,
	):
		if params.file_name:
			file_name = params.file_name
			if not file_name.lower().endswith('.png'):
				file_name = f'{file_name}.png'
			file_name = FileSystem.sanitize_filename(file_name)

			screenshot_bytes = await browser_session.take_screenshot(full_page=False)
			file_path = file_system.get_dir() / file_name
			file_path.write_bytes(screenshot_bytes)

			result = f'Screenshot saved to {file_name}'
			logger.info(f'📸 {result}. Full path: {file_path}')
			return ActionResult(
				extracted_content=result,
				long_term_memory=f'{result}. Full path: {file_path}',
				attachments=[str(file_path)],
			)

		memory = 'Requested screenshot for next observation'
		logger.info(f'📸 {memory}')
		return ActionResult(extracted_content=memory, metadata={'include_screenshot': True})

	@tools.registry.action(
		'Save the current page as a PDF file. Returns the file path of the saved PDF. Use this to capture the full page content (including content below the fold) as a printable document.',
		param_model=SaveAsPdfAction,
	)
	async def save_as_pdf(
		params: SaveAsPdfAction,
		browser_session: BrowserSession,
		file_system: FileSystem,
	):
		paper_sizes: dict[str, tuple[float, float]] = {
			'letter': (8.5, 11),
			'legal': (8.5, 14),
			'a4': (8.27, 11.69),
			'a3': (11.69, 16.54),
			'tabloid': (11, 17),
		}

		paper_key = params.paper_format.lower()
		if paper_key not in paper_sizes:
			paper_key = 'letter'
		paper_width, paper_height = paper_sizes[paper_key]

		cdp_session = await browser_session.get_or_create_cdp_session(focus=True)
		result = await asyncio.wait_for(
			cdp_session.cdp_client.send.Page.printToPDF(
				params={
					'printBackground': params.print_background,
					'landscape': params.landscape,
					'scale': params.scale,
					'paperWidth': paper_width,
					'paperHeight': paper_height,
					'preferCSSPageSize': True,
				},
				session_id=cdp_session.session_id,
			),
			timeout=30.0,
		)

		pdf_data = result.get('data')
		assert pdf_data, 'CDP Page.printToPDF returned no data'
		pdf_bytes = base64.b64decode(pdf_data)

		if params.file_name:
			file_name = params.file_name
		else:
			try:
				page_title = await asyncio.wait_for(browser_session.get_current_page_title(), timeout=2.0)
				safe_title = re.sub(r'[^\w\s-]', '', page_title).strip()[:50]
				file_name = safe_title if safe_title else 'page'
			except Exception:
				file_name = 'page'

		if not file_name.lower().endswith('.pdf'):
			file_name = f'{file_name}.pdf'
		file_name = FileSystem.sanitize_filename(file_name)

		file_path = file_system.get_dir() / file_name
		if file_path.exists():
			base_name, extension = os.path.splitext(file_name)
			counter = 1
			while (file_system.get_dir() / f'{base_name} ({counter}){extension}').exists():
				counter += 1
			file_name = f'{base_name} ({counter}){extension}'
			file_path = file_system.get_dir() / file_name

		async with await anyio.open_file(file_path, 'wb') as file_handle:
			await file_handle.write(pdf_bytes)

		file_size = file_path.stat().st_size
		msg = f'Saved page as PDF: {file_name} ({file_size:,} bytes)'
		logger.info(f'📄 {msg}. Full path: {file_path}')
		return ActionResult(
			extracted_content=msg,
			long_term_memory=f'{msg}. Full path: {file_path}',
			attachments=[str(file_path)],
		)
