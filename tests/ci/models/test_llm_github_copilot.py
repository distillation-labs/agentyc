import sys
import tempfile
import types

import pytest
from pydantic import BaseModel

from traverse.llm.copilot.chat import ChatGitHubCopilot
from traverse.llm.messages import ContentPartImageParam, ContentPartTextParam, ImageURL, SystemMessage, UserMessage


class StructuredResponse(BaseModel):
	message: str


class _FakeAssistantData:
	def __init__(self, content: str):
		self.content = content


class _FakeAssistantEvent:
	def __init__(self, content: str):
		self.data = _FakeAssistantData(content)


class _FakeSession:
	def __init__(self, response_text: str):
		self.response_text = response_text
		self.sent: list[dict] = []
		self.disconnected = False

	async def send_and_wait(self, options, timeout=60.0):
		self.sent.append(
			{
				'prompt': options['prompt'],
				'attachments': options.get('attachments'),
				'timeout': timeout,
				'mode': options.get('mode'),
			}
		)
		return _FakeAssistantEvent(self.response_text)

	async def disconnect(self):
		self.disconnected = True


class _FakeCopilotClient:
	instances = []
	default_response_text = 'ok'

	def __init__(self, config):
		self.config = config
		self.started = False
		self.force_stopped = False
		self.create_session_calls: list[dict] = []
		self.next_response_text = self.default_response_text
		self.session: _FakeSession | None = None
		_FakeCopilotClient.instances.append(self)

	async def start(self):
		self.started = True

	async def force_stop(self):
		self.force_stopped = True

	async def list_models(self):
		return [types.SimpleNamespace(id='gpt-5'), types.SimpleNamespace(id='gpt-4.1')]

	async def create_session(self, config):
		self.create_session_calls.append(config)
		self.session = _FakeSession(self.next_response_text)
		return self.session


class _FakePermissionRequestResult:
	def __init__(self, kind: str):
		self.kind = kind


@pytest.fixture
def fake_copilot_sdk(monkeypatch):
	_FakeCopilotClient.instances.clear()
	_FakeCopilotClient.default_response_text = 'ok'

	copilot_module = types.ModuleType('copilot')
	copilot_module.CopilotClient = _FakeCopilotClient
	copilot_module.PermissionRequestResult = _FakePermissionRequestResult

	monkeypatch.setitem(sys.modules, 'copilot', copilot_module)
	return _FakeCopilotClient


@pytest.mark.asyncio
async def test_github_copilot_text_completion(fake_copilot_sdk):
	llm = ChatGitHubCopilot(model='default')
	response = await llm.ainvoke([UserMessage(content='hello')])

	assert response.completion == 'ok'
	assert response.usage is None

	client = fake_copilot_sdk.instances[-1]
	assert client.started is True
	assert client.config['cwd'] == client.create_session_calls[0]['working_directory']
	assert client.create_session_calls[0]['available_tools'] == []
	assert client.create_session_calls[0]['streaming'] is False
	assert client.create_session_calls[0]['infinite_sessions'] == {'enabled': False}
	assert client.session is not None
	assert client.session.disconnected is True

	models = await llm.list_models()
	assert models == ['gpt-5', 'gpt-4.1']

	await llm.aclose()
	assert client.force_stopped is True


@pytest.mark.asyncio
async def test_github_copilot_structured_output_and_image_attachments(fake_copilot_sdk):
	_FakeCopilotClient.default_response_text = '```json\n{"message":"done"}\n```'
	llm = ChatGitHubCopilot(model='gpt-5')

	image = ContentPartImageParam(
		image_url=ImageURL(
			url='data:image/png;base64,ZmFrZQ==',
			detail='auto',
			media_type='image/png',
		)
	)

	response = await llm.ainvoke(
		[
			SystemMessage(content='You are Traverse.'),
			UserMessage(content=[ContentPartTextParam(text='Inspect this page', type='text'), image]),
		],
		output_format=StructuredResponse,
	)

	assert response.completion == StructuredResponse(message='done')

	client = fake_copilot_sdk.instances[-1]
	assert client.session is not None
	attachments = client.session.sent[0]['attachments']
	assert attachments is not None
	assert len(attachments) == 1
	assert attachments[0]['type'] == 'file'
	assert attachments[0]['path'].endswith('.png')
	assert attachments[0]['displayName'].endswith('.png')
	assert '<output_instructions>' in client.session.sent[0]['prompt']
	assert 'Inspect this page' in client.session.sent[0]['prompt']
	assert client.create_session_calls[0]['system_message']['mode'] == 'replace'
	assert 'Do not invoke Copilot tools' in client.create_session_calls[0]['system_message']['content']

	_FakeCopilotClient.default_response_text = 'ok'


@pytest.mark.asyncio
async def test_github_copilot_respects_explicit_cwd(fake_copilot_sdk):
	with tempfile.TemporaryDirectory() as temp_dir:
		llm = ChatGitHubCopilot(model='gpt-5', cwd=temp_dir)
		await llm.ainvoke([UserMessage(content='hello')])

	client = fake_copilot_sdk.instances[-1]
	assert client.config['cwd'] == temp_dir
	assert client.create_session_calls[0]['working_directory'] == temp_dir


@pytest.mark.asyncio
async def test_github_copilot_missing_sdk_raises(monkeypatch):
	monkeypatch.setitem(sys.modules, 'copilot', None)

	llm = ChatGitHubCopilot(model='gpt-5')

	with pytest.raises(Exception, match='GitHub Copilot SDK is not installed'):
		await llm.ainvoke([UserMessage(content='hello')])
