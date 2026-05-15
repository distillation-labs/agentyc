import json
from typing import Any

from agentyc.actions import ActionResult
from agentyc.browser import BrowserSession
from agentyc.filesystem.file_system import FileSystem
from agentyc.tools.views import DoneAction, StructuredOutputAction


def register_done_action(tools: Any, output_model: type[Any] | None, display_files_in_done_text: bool = True) -> None:
	if output_model is not None:
		tools.display_files_in_done_text = display_files_in_done_text

		@tools.registry.action(
			'Complete task with structured output.',
			param_model=StructuredOutputAction[output_model],
		)
		async def done(params: StructuredOutputAction, file_system: FileSystem, browser_session: BrowserSession):
			output_dict = params.data.model_dump(mode='json')
			attachments: list[str] = []

			if params.files_to_display:
				for file_name in params.files_to_display:
					file_content = file_system.display_file(file_name)
					if file_content:
						attachments.append(str(file_system.get_dir() / file_name))

			session_downloads = browser_session.downloaded_files
			if session_downloads:
				existing = set(attachments)
				for file_path in session_downloads:
					if file_path not in existing:
						attachments.append(file_path)

			return ActionResult(
				is_done=True,
				success=params.success,
				extracted_content=json.dumps(output_dict, ensure_ascii=False),
				long_term_memory=f'Task completed. Success Status: {params.success}',
				attachments=attachments,
			)

		return

	@tools.registry.action(
		'Complete task. Only report actions you performed and data you extracted in this session.',
		param_model=DoneAction,
	)
	async def done(params: DoneAction, file_system: FileSystem):
		user_message = params.text
		len_text = len(params.text)
		len_max_memory = 100
		memory = f'Task completed: {params.success} - {params.text[:len_max_memory]}'
		if len_text > len_max_memory:
			memory += f' - {len_text - len_max_memory} more characters'

		attachments: list[str] = []
		if params.files_to_display:
			if tools.display_files_in_done_text:
				file_msg = ''
				for file_name in params.files_to_display:
					file_content = file_system.display_file(file_name)
					if file_content:
						file_msg += f'\n\n{file_name}:\n{file_content}'
						attachments.append(file_name)
				if file_msg:
					user_message += '\n\nAttachments:'
					user_message += file_msg
				else:
					import logging

					logging.getLogger(__name__).warning('Agent wanted to display files but none were found')
			else:
				for file_name in params.files_to_display:
					file_content = file_system.display_file(file_name)
					if file_content:
						attachments.append(file_name)

		attachments = [str(file_system.get_dir() / file_name) for file_name in attachments]
		return ActionResult(
			is_done=True,
			success=params.success,
			extracted_content=user_message,
			long_term_memory=memory,
			attachments=attachments,
		)
