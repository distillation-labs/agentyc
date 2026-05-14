"""MCP (Model Context Protocol) support for agentyc."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
	from agentyc.mcp.client import MCPClient
	from agentyc.mcp.controller import MCPToolWrapper
	from agentyc.mcp.server import AgentycServer

_LAZY_IMPORTS = {
	'MCPClient': ('agentyc.mcp.client', 'MCPClient'),
	'MCPToolWrapper': ('agentyc.mcp.controller', 'MCPToolWrapper'),
	'AgentycServer': ('agentyc.mcp.server', 'AgentycServer'),
}

__all__ = ['MCPClient', 'MCPToolWrapper', 'AgentycServer']


def __getattr__(name: str):
	"""Lazy import MCP surfaces so server import does not pull client code into the hot path."""
	if name in _LAZY_IMPORTS:
		module_path, attr_name = _LAZY_IMPORTS[name]
		from importlib import import_module

		module = import_module(module_path)
		attr = getattr(module, attr_name)
		globals()[name] = attr
		return attr
	raise AttributeError(f"module '{__name__}' has no attribute '{name}'")
