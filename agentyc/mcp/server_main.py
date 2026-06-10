"""MCP server entrypoint kept separate from AgentycServer implementation."""

from __future__ import annotations

import asyncio
import sys
from typing import Any

from agentyc.mcp.server_bootstrap import _try_preload_allocator

# Preload mimalloc/jemalloc early to reduce RSS fragmentation on macOS/Linux
_try_preload_allocator()

async def main(
	session_timeout_minutes: int = 0,
	cdp_url: str | None = None,
) -> None:
	from agentyc.mcp.server import MCP_AVAILABLE, AgentycServer

	if not MCP_AVAILABLE:
		print('MCP SDK is required. Install with: pip install mcp', file=sys.stderr)
		sys.exit(1)

	server = AgentycServer(
		session_timeout_minutes=session_timeout_minutes,
		cdp_url=cdp_url,
	)

	try:
		await server.run()
	finally:
		pass

if __name__ == '__main__':
	asyncio.run(main())
