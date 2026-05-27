"""MCP tool schema catalog for the agentyc server."""

from __future__ import annotations

import mcp.types as types

from agentyc.mcp.tool_schemas_core import CORE_TOOL_SCHEMAS
from agentyc.mcp.tool_schemas_debug_network import DEBUG_NETWORK_TOOL_SCHEMAS
from agentyc.mcp.tool_schemas_page_ops import PAGE_OPERATION_TOOL_SCHEMAS


def get_tool_schemas() -> list[types.Tool]:
	"""Return the public MCP tool catalog."""
	return [*CORE_TOOL_SCHEMAS, *PAGE_OPERATION_TOOL_SCHEMAS, *DEBUG_NETWORK_TOOL_SCHEMAS]
