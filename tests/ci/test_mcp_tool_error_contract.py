import mcp.types as types
import pytest

from agentyc.browser import BrowserProfile, BrowserSession
from agentyc.mcp.server import AgentycServer
from agentyc.tools.service import Tools


@pytest.fixture
async def browser_session():
	session = BrowserSession(
		browser_profile=BrowserProfile(
			headless=True,
			user_data_dir=None,
		)
	)
	await session.start()
	yield session
	await session.stop()


async def test_call_tool_sets_iserror_for_textual_tool_failures(browser_session: BrowserSession):
	server = AgentycServer()
	server.browser_session = browser_session
	server.tools = Tools()
	handler = server.server.request_handlers[types.CallToolRequest]

	request = types.CallToolRequest(
		params=types.CallToolRequestParams(
			name='browser_wait_for_element',
			arguments={'text': 'never appears', 'timeout_seconds': 0.5},
		)
	)

	try:
		result = await handler(request)
	finally:
		await server._shutdown()

	call_result = result.root
	assert isinstance(call_result, types.CallToolResult)
	assert call_result.isError is True
	text_blocks = [item for item in call_result.content if isinstance(item, types.TextContent)]
	assert text_blocks
	assert text_blocks[0].text.startswith('Error [timeout]:')
