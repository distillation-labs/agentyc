import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from agentyc.mcp import cli


def test_cmd_mcp_forwards_reuse_local_browser_flag():
	args = SimpleNamespace(
		session_timeout_minutes=0,
		cdp_url=None,
		hud_overlay=None,
		runtime_label=None,
		runtime_role='primary',
		parent_runtime_id=None,
		shared_browser_mode='tab',
		shared_browser_window_bounds=None,
		shared_browser_focus_policy='preserve',
		reuse_local_browser=True,
	)

	with patch('agentyc.mcp.cli.mcp_main', new_callable=AsyncMock) as mcp_main:
		with patch('agentyc.mcp.cli.asyncio.run', side_effect=lambda coro: coro.close()) as run:
			cli._cmd_mcp(args)

	run.assert_called_once()
	mcp_main.assert_called_once_with(
		session_timeout_minutes=0,
		cdp_url=None,
		hud_overlay=None,
		runtime_label=None,
		runtime_role='primary',
		parent_runtime_id=None,
		shared_browser_mode='tab',
		shared_browser_window_bounds=None,
		shared_browser_focus_policy='preserve',
		reuse_local_browser=True,
	)


def test_cmd_browser_registers_shared_browser_for_reuse():
	args = SimpleNamespace(port=9222, headless=False, detach=True)
	process = SimpleNamespace(pid=1234, wait=Mock(), terminate=Mock(), kill=Mock())

	class _Response:
		def read(self) -> bytes:
			return json.dumps({'webSocketDebuggerUrl': 'ws://127.0.0.1:9222/devtools/browser/test'}).encode('utf-8')

	with patch('shutil.which', return_value='/tmp/chrome'):
		with patch('os.path.isfile', return_value=True):
			with patch('subprocess.Popen', return_value=process):
				with patch('urllib.request.urlopen', return_value=_Response()):
					with patch('agentyc.mcp.shared_browser_registry.register_local_shared_browser') as register_shared:
						cli._cmd_browser(args)

	register_shared.assert_called_once_with(
		cdp_url='ws://127.0.0.1:9222/devtools/browser/test',
		browser_pid=1234,
		headless=False,
		user_data_dir=None,
	)
