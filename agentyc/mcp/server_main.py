"""MCP server entrypoint kept separate from AgentycServer implementation."""

from __future__ import annotations

import asyncio
import sys
import time
from typing import Any

from agentyc.mcp.server_bootstrap import get_parent_process_cmdline


async def main(
	session_timeout_minutes: int = 0,
	cdp_url: str | None = None,
	*,
	hud_overlay: bool | None = None,
	runtime_label: str | None = None,
	runtime_role: str = 'primary',
	parent_runtime_id: str | None = None,
	shared_browser_mode: str = 'tab',
	shared_browser_window_bounds: dict[str, Any] | None = None,
	shared_browser_focus_policy: str = 'preserve',
) -> None:
	from agentyc.mcp.server import MCP_AVAILABLE, AgentycServer

	if not MCP_AVAILABLE:
		print('MCP SDK is required. Install with: pip install mcp', file=sys.stderr)
		sys.exit(1)

	server = AgentycServer(
		session_timeout_minutes=session_timeout_minutes,
		cdp_url=cdp_url,
		hud_overlay=hud_overlay,
		runtime_label=runtime_label,
		runtime_role=runtime_role,
		parent_runtime_id=parent_runtime_id,
		shared_browser_mode=shared_browser_mode,
		shared_browser_window_bounds=shared_browser_window_bounds,
		shared_browser_focus_policy=shared_browser_focus_policy,
	)
	from agentyc.telemetry import MCPServerTelemetryEvent
	from agentyc.utils import get_agentyc_version

	server._telemetry.capture(
		MCPServerTelemetryEvent(
			version=get_agentyc_version(),
			action='start',
			parent_process_cmdline=get_parent_process_cmdline(),
		)
	)

	try:
		await server.run()
	finally:
		hud_overlay_instance = getattr(server, '_hud_overlay', None)
		if hud_overlay_instance is not None:
			hud_overlay_instance.stop()
		duration = time.time() - server._start_time
		server._telemetry.capture(
			MCPServerTelemetryEvent(
				version=get_agentyc_version(),
				action='stop',
				duration_seconds=duration,
				parent_process_cmdline=get_parent_process_cmdline(),
			)
		)
		server._telemetry.flush()


if __name__ == '__main__':
	asyncio.run(main())
