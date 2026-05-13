"""Entry point for running MCP server as a module.

Usage:
    python -m traverse.mcp
"""

import asyncio

from traverse.mcp.server import main

if __name__ == '__main__':
	asyncio.run(main())
