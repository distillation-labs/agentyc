"""Server-facing MCP surface for agentyc."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
	from agentyc.mcp.server import AgentycServer

_LAZY_IMPORTS = {
	'AgentycServer': ('agentyc.mcp.server', 'AgentycServer'),
}

__all__ = ['AgentycServer']


def __getattr__(name: str):
	"""Lazy import the MCP server surface."""
	if name in _LAZY_IMPORTS:
		module_path, attr_name = _LAZY_IMPORTS[name]
		from importlib import import_module

		module = import_module(module_path)
		attr = getattr(module, attr_name)
		globals()[name] = attr
		return attr
	raise AttributeError(f"module '{__name__}' has no attribute '{name}'")
