"""Entry point for running MCP server as a module.

Usage:
    python -m agentyc.mcp
"""

import asyncio

from agentyc.mcp.server import main

if __name__ == '__main__':
	asyncio.run(main())
