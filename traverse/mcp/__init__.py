"""MCP (Model Context Protocol) support for traverse."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
	from traverse.mcp.client import MCPClient
	from traverse.mcp.controller import MCPToolWrapper
	from traverse.mcp.server import TraverseServer

_LAZY_IMPORTS = {
	'MCPClient': ('traverse.mcp.client', 'MCPClient'),
	'MCPToolWrapper': ('traverse.mcp.controller', 'MCPToolWrapper'),
	'TraverseServer': ('traverse.mcp.server', 'TraverseServer'),
}

__all__ = ['MCPClient', 'MCPToolWrapper', 'TraverseServer']


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
