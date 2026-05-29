from types import SimpleNamespace
from unittest.mock import AsyncMock, call

from agentyc.mcp.cdp_debug_session_tools import _ensure_runtime_event_domains_enabled


async def test_runtime_event_domains_enabled_once_per_session() -> None:
	runtime_enable = AsyncMock(return_value={})
	network_enable = AsyncMock(return_value={})
	server = SimpleNamespace(
		_cdp_client_for_runtime=SimpleNamespace(
			send=SimpleNamespace(
				Runtime=SimpleNamespace(enable=runtime_enable),
				Network=SimpleNamespace(enable=network_enable),
			)
		)
	)

	await _ensure_runtime_event_domains_enabled(server, 'session-a')
	await _ensure_runtime_event_domains_enabled(server, 'session-a')
	await _ensure_runtime_event_domains_enabled(server, 'session-b')

	assert runtime_enable.await_count == 2
	assert network_enable.await_count == 2
	assert runtime_enable.await_args_list == [call(session_id='session-a'), call(session_id='session-b')]
	assert network_enable.await_args_list == [call(session_id='session-a'), call(session_id='session-b')]
	assert server._runtime_enabled_session_ids == {'session-a', 'session-b'}
