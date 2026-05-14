"""Minimal CLI entrypoint for MCP server mode."""

from __future__ import annotations

import argparse
import asyncio

from agentyc.mcp.server import main as mcp_main


def main() -> None:
	"""Run the MCP server over stdio."""
	parser = argparse.ArgumentParser(description='agentyc MCP server')
	parser.add_argument(
		'--session-timeout-minutes',
		type=int,
		default=10,
		help='Idle timeout for managed browser sessions',
	)
	args = parser.parse_args()
	asyncio.run(mcp_main(session_timeout_minutes=args.session_timeout_minutes))
