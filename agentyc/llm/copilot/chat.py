import asyncio
import base64
import json
import weakref
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, TypeVar, cast, overload
from uuid import uuid4

from agentyc.llm.base import BaseChatModel
from agentyc.llm.exceptions import ModelProviderError
from agentyc.llm.messages import (
	AssistantMessage,
	BaseMessage,
	ContentPartImageParam,
	ContentPartRefusalParam,
	ContentPartTextParam,
	SystemMessage,
	UserMessage,
)
from agentyc.llm.schema import SchemaOptimizer
from agentyc.llm.views import ChatInvokeCompletion
from pydantic import BaseModel

T = TypeVar('T', bound=BaseModel)


def _stop_client_sync(client: Any) -> None:
	try:
		asyncio.run(client.force_stop())
	except Exception:
		try:
			loop = asyncio.new_event_loop()
			loop.run_until_complete(client.force_stop())
			loop.close()
		except Exception:
			pass


@dataclass
class ChatGitHubCopilot(BaseChatModel):
	"""Experimental Agentyc adapter for the GitHub Copilot SDK.

	This keeps Agentyc as the only browser/tool loop by creating a fresh,
	tool-less Copilot session for each invocation and treating Copilot as a
	stateless text/JSON backend.
	"""

	model: str = 'default'
	github_token: str | None = None
	use_logged_in_user: bool | None = None
	cli_path: str | None = None
	cli_url: str | None = None
	cwd: str | None = None
	env: dict[str, str] | None = None
	timeout: float = 120.0
	include_images: bool = True

	_client: Any = field(default=None, init=False, repr=False)
	_client_finalizer: weakref.finalize | None = field(default=None, init=False, repr=False)
	_managed_cwd: TemporaryDirectory[str] | None = field(default=None, init=False, repr=False)

	@property
	def provider(self) -> str:
		return 'github-copilot'

	@property
	def name(self) -> str:
		return self.model

	def _import_sdk(self) -> tuple[Any, Any]:
		try:
			import copilot as copilot_sdk
		except ImportError as e:
			raise ModelProviderError(
				message=(
					'GitHub Copilot SDK is not installed. Install it with '
					'`uv add "agentyc[copilot]"` or `uv add github-copilot-sdk`, '
					'then authenticate with `copilot auth login`.'
				),
				model=self.name,
			) from e

		copilot_client = getattr(cast(Any, copilot_sdk), 'CopilotClient')
		permission_request_result = getattr(cast(Any, copilot_sdk), 'PermissionRequestResult')
		return copilot_client, permission_request_result

	async def _ensure_client(self) -> Any:
		if self._client is not None:
			return self._client

		CopilotClient, _ = self._import_sdk()
		session_cwd = self._session_cwd()

		config: dict[str, Any] = {
			'cwd': session_cwd,
			'log_level': 'error',
		}
		if self.cli_url is not None:
			config['cli_url'] = self.cli_url
		else:
			if self.cli_path is not None:
				config['cli_path'] = self.cli_path
			if self.env is not None:
				config['env'] = self.env
			if self.github_token is not None:
				config['github_token'] = self.github_token
			if self.use_logged_in_user is not None:
				config['use_logged_in_user'] = self.use_logged_in_user

		client = CopilotClient(config)
		await client.start()
		self._client = client
		self._client_finalizer = weakref.finalize(self, _stop_client_sync, client)
		self._verified_api_keys = True
		return client

	async def aclose(self) -> None:
		client = self._client
		self._client = None
		if self._client_finalizer is not None:
			self._client_finalizer.detach()
			self._client_finalizer = None

		try:
			if client is not None:
				await client.force_stop()
		finally:
			self._cleanup_managed_cwd()

	async def list_models(self) -> list[str]:
		client = await self._ensure_client()
		models = await client.list_models()
		return [str(model.id) for model in models]

	def _build_system_message(self, system_messages: list[SystemMessage], output_format: type[T] | None) -> str:
		parts = [
			'You are the language model backend for Agentyc.',
			'You are not the browser runtime. Do not invoke Copilot tools or assume you can inspect files, modify files, run shell commands, browse the web, or take actions outside the provided conversation transcript.',
			'Respond only to the provided Agentyc conversation transcript and attached images.',
		]

		if output_format is not None:
			parts.append(
				'When a JSON schema is provided, respond with ONLY valid JSON that matches it exactly. Do not use markdown fences.'
			)

		if system_messages:
			parts.append('<agentyc_system_messages>')
			parts.extend(self._message_text(message) for message in system_messages if self._message_text(message))
			parts.append('</agentyc_system_messages>')

		return '\n\n'.join(parts)

	def _message_text(self, message: BaseMessage) -> str:
		if isinstance(message, (UserMessage, SystemMessage, AssistantMessage)):
			if isinstance(message.content, str):
				text = message.content
			elif isinstance(message.content, list):
				chunks: list[str] = []
				for part in message.content:
					if isinstance(part, ContentPartTextParam):
						chunks.append(part.text)
					elif isinstance(part, ContentPartRefusalParam):
						chunks.append(f'[Refusal] {part.refusal}')
				text = '\n'.join(chunks)
			else:
				text = ''
		else:
			text = ''

		if isinstance(message, AssistantMessage) and message.tool_calls:
			tool_lines = [f'{tool_call.function.name}({tool_call.function.arguments})' for tool_call in message.tool_calls]
			if tool_lines:
				text = f'{text}\nTool calls:\n' + '\n'.join(tool_lines)

		return text.strip()

	def _serialize_prompt(self, messages: list[BaseMessage], output_format: type[T] | None) -> tuple[str, list[dict[str, Any]]]:
		prompt_lines = [
			'<agentyc_conversation>',
		]
		attachments: list[dict[str, Any]] = []
		attachment_index = 1

		for index, message in enumerate(messages, start=1):
			if isinstance(message, SystemMessage):
				continue

			prompt_lines.append(f'<message index="{index}" role="{message.role}">')
			text = self._message_text(message)
			if text:
				prompt_lines.append(text)

			if self.include_images and isinstance(message, UserMessage) and isinstance(message.content, list):
				for part in message.content:
					if isinstance(part, ContentPartImageParam):
						attachment = self._image_attachment(part)
						if attachment is not None:
							attachments.append(attachment)
							prompt_lines.append(f'[Attached image {attachment_index} belongs to this user message.]')
							attachment_index += 1

			prompt_lines.append('</message>')

		prompt_lines.append('</agentyc_conversation>')

		if output_format is not None:
			schema = SchemaOptimizer.create_optimized_json_schema(output_format)
			prompt_lines.extend(
				[
					'<output_instructions>',
					'Return ONLY a valid JSON object that matches this schema exactly:',
					json.dumps(schema, indent=2),
					'</output_instructions>',
				]
			)

		return '\n'.join(prompt_lines), attachments

	def _attachment_path(self, mime_type: str) -> Path:
		attachments_dir = Path(self._session_cwd()) / 'attachments'
		attachments_dir.mkdir(parents=True, exist_ok=True)
		suffix = {
			'image/png': '.png',
			'image/jpeg': '.jpg',
			'image/gif': '.gif',
			'image/webp': '.webp',
		}.get(mime_type, '.bin')
		return attachments_dir / f'{uuid4().hex}{suffix}'

	def _image_attachment(self, part: ContentPartImageParam) -> dict[str, Any] | None:
		url = part.image_url.url
		if not url.startswith('data:'):
			return None

		try:
			header, data = url.split(',', 1)
		except ValueError:
			return None

		mime_type = part.image_url.media_type
		if ';' in header:
			parsed_mime = header.split(':', 1)[1].split(';', 1)[0]
			if parsed_mime:
				mime_type = parsed_mime

		try:
			image_bytes = base64.b64decode(data, validate=True)
		except Exception:
			try:
				image_bytes = base64.b64decode(data)
			except Exception:
				return None

		attachment_path = self._attachment_path(mime_type)
		attachment_path.write_bytes(image_bytes)

		return {
			'type': 'file',
			'path': str(attachment_path),
			'displayName': attachment_path.name,
		}

	def _session_id(self, provided_session_id: Any) -> str:
		prefix = str(provided_session_id).strip() if provided_session_id else 'agentyc'
		return f'{prefix}-{uuid4().hex[:8]}'

	def _extract_response_text(self, response: Any) -> str:
		data = getattr(response, 'data', None)
		content = getattr(data, 'content', None)
		if isinstance(content, str):
			return content
		raise ModelProviderError(message='GitHub Copilot SDK returned no assistant message content', model=self.name)

	def _extract_json_candidate(self, text: str) -> str:
		candidate = text.strip()
		if candidate.startswith('```json') and candidate.endswith('```'):
			candidate = candidate[7:-3].strip()
		elif candidate.startswith('```') and candidate.endswith('```'):
			candidate = candidate[3:-3].strip()

		if candidate.startswith('{') and candidate.endswith('}'):
			return candidate

		start = candidate.find('{')
		end = candidate.rfind('}')
		if start != -1 and end != -1 and end > start:
			return candidate[start : end + 1]

		return candidate

	def _response_text_preview(self, text: str, limit: int = 300) -> str:
		text = text.replace('\n', '\\n')
		return text[:limit]

	def _reject_permissions(self, request: Any, invocation: dict[str, str]) -> Any:
		_, PermissionRequestResult = self._import_sdk()
		return PermissionRequestResult(kind='denied-no-approval-rule-and-could-not-request-from-user')

	def _session_cwd(self) -> str:
		if self.cwd is not None:
			return self.cwd

		if self._managed_cwd is None:
			self._managed_cwd = TemporaryDirectory(prefix='agentyc-copilot-')

		return self._managed_cwd.name

	def _cleanup_managed_cwd(self) -> None:
		if self._managed_cwd is None:
			return

		self._managed_cwd.cleanup()
		self._managed_cwd = None

	async def _run_copilot_session(
		self, prompt: str, attachments: list[dict[str, Any]], system_message: str, session_id: str
	) -> str:
		client = await self._ensure_client()
		working_directory = self._session_cwd()
		session_config: dict[str, Any] = {
			'on_permission_request': self._reject_permissions,
			'session_id': session_id,
			'system_message': {'mode': 'replace', 'content': system_message},
			'available_tools': [],
			'working_directory': working_directory,
			'streaming': False,
			'skill_directories': [],
			'infinite_sessions': {'enabled': False},
		}
		if self.model != 'default':
			session_config['model'] = self.model
		session = await client.create_session(session_config)

		try:
			message_options: dict[str, Any] = {'prompt': prompt}
			if attachments:
				message_options['attachments'] = attachments
			response = await session.send_and_wait(message_options, timeout=self.timeout)
			if response is None:
				raise ModelProviderError(message='GitHub Copilot SDK returned no final response', model=self.name)
			return self._extract_response_text(response)
		finally:
			await session.disconnect()

	@overload
	async def ainvoke(
		self, messages: list[BaseMessage], output_format: None = None, **kwargs: Any
	) -> ChatInvokeCompletion[str]: ...

	@overload
	async def ainvoke(self, messages: list[BaseMessage], output_format: type[T], **kwargs: Any) -> ChatInvokeCompletion[T]: ...

	async def ainvoke(
		self, messages: list[BaseMessage], output_format: type[T] | None = None, **kwargs: Any
	) -> ChatInvokeCompletion[T] | ChatInvokeCompletion[str]:
		system_messages = [message for message in messages if isinstance(message, SystemMessage)]
		system_message = self._build_system_message(system_messages, output_format)
		prompt, attachments = self._serialize_prompt(messages, output_format)
		session_id = self._session_id(kwargs.get('session_id'))

		try:
			response_text = await self._run_copilot_session(prompt, attachments, system_message, session_id)
		except ModelProviderError:
			raise
		except TimeoutError as e:
			raise ModelProviderError(message=f'GitHub Copilot SDK timed out: {e}', model=self.name) from e
		except Exception as e:
			raise ModelProviderError(message=str(e), model=self.name) from e

		if output_format is None:
			return ChatInvokeCompletion(completion=response_text, usage=None)

		candidate = self._extract_json_candidate(response_text)
		try:
			parsed = output_format.model_validate_json(candidate)
		except Exception as e:
			raise ModelProviderError(
				message=(
					'Failed to parse GitHub Copilot structured output: '
					f'{e}. Raw response: {self._response_text_preview(response_text)}'
				),
				model=self.name,
			) from e

		return ChatInvokeCompletion(completion=parsed, usage=None)
