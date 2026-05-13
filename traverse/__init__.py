import os
from asyncio import base_subprocess
from typing import TYPE_CHECKING

from traverse.logging_config import setup_logging

# Only set up logging if not in MCP mode or if explicitly requested
if os.environ.get('TRAVERSE_SETUP_LOGGING', 'true').lower() != 'false':
	from traverse.config import CONFIG

	debug_log_file = getattr(CONFIG, 'TRAVERSE_DEBUG_LOG_FILE', None)
	info_log_file = getattr(CONFIG, 'TRAVERSE_INFO_LOG_FILE', None)
	logger = setup_logging(debug_log_file=debug_log_file, info_log_file=info_log_file)
else:
	import logging

	logger = logging.getLogger('traverse')

_original_del = base_subprocess.BaseSubprocessTransport.__del__


def _patched_del(self):
	"""Avoid noisy RuntimeError when subprocess cleanup runs after loop shutdown."""
	try:
		if hasattr(self, '_loop') and self._loop and self._loop.is_closed():
			return
		_original_del(self)
	except RuntimeError as e:
		if 'Event loop is closed' not in str(e):
			raise


base_subprocess.BaseSubprocessTransport.__del__ = _patched_del


if TYPE_CHECKING:
	from traverse.actions import ActionModel, ActionResult
	from traverse.browser import BrowserProfile, BrowserSession
	from traverse.browser import BrowserSession as Browser
	from traverse.dom.service import DomService
	from traverse.llm import models
	from traverse.llm.anthropic.chat import ChatAnthropic
	from traverse.llm.azure.chat import ChatAzureOpenAI
	from traverse.llm.copilot.chat import ChatGitHubCopilot
	from traverse.llm.google.chat import ChatGoogle
	from traverse.llm.groq.chat import ChatGroq
	from traverse.llm.litellm.chat import ChatLiteLLM
	from traverse.llm.mistral.chat import ChatMistral
	from traverse.llm.oci_raw.chat import ChatOCIRaw
	from traverse.llm.ollama.chat import ChatOllama
	from traverse.llm.openai.chat import ChatOpenAI
	from traverse.llm.traverse.chat import ChatTraverse
	from traverse.llm.vercel.chat import ChatVercel
	from traverse.mcp.server import TraverseServer
	from traverse.tools.service import Controller, Tools


_LAZY_IMPORTS = {
	'ActionModel': ('traverse.actions', 'ActionModel'),
	'ActionResult': ('traverse.actions', 'ActionResult'),
	'BrowserSession': ('traverse.browser', 'BrowserSession'),
	'Browser': ('traverse.browser', 'BrowserSession'),
	'BrowserProfile': ('traverse.browser', 'BrowserProfile'),
	'Tools': ('traverse.tools.service', 'Tools'),
	'Controller': ('traverse.tools.service', 'Controller'),
	'DomService': ('traverse.dom.service', 'DomService'),
	'ChatOpenAI': ('traverse.llm.openai.chat', 'ChatOpenAI'),
	'ChatGoogle': ('traverse.llm.google.chat', 'ChatGoogle'),
	'ChatAnthropic': ('traverse.llm.anthropic.chat', 'ChatAnthropic'),
	'ChatTraverse': ('traverse.llm.traverse.chat', 'ChatTraverse'),
	'ChatGitHubCopilot': ('traverse.llm.copilot.chat', 'ChatGitHubCopilot'),
	'ChatGroq': ('traverse.llm.groq.chat', 'ChatGroq'),
	'ChatLiteLLM': ('traverse.llm.litellm.chat', 'ChatLiteLLM'),
	'ChatMistral': ('traverse.llm.mistral.chat', 'ChatMistral'),
	'ChatAzureOpenAI': ('traverse.llm.azure.chat', 'ChatAzureOpenAI'),
	'ChatOCIRaw': ('traverse.llm.oci_raw.chat', 'ChatOCIRaw'),
	'ChatOllama': ('traverse.llm.ollama.chat', 'ChatOllama'),
	'ChatVercel': ('traverse.llm.vercel.chat', 'ChatVercel'),
	'TraverseServer': ('traverse.mcp.server', 'TraverseServer'),
	'models': ('traverse.llm.models', None),
}


def __getattr__(name: str):
	"""Lazy import mechanism - only import modules when they're actually accessed."""
	if name in _LAZY_IMPORTS:
		module_path, attr_name = _LAZY_IMPORTS[name]
		try:
			from importlib import import_module

			module = import_module(module_path)
			attr = module if attr_name is None else getattr(module, attr_name)
			globals()[name] = attr
			return attr
		except ImportError as e:
			raise ImportError(f'Failed to import {name} from {module_path}: {e}') from e

	raise AttributeError(f"module '{__name__}' has no attribute '{name}'")


__all__ = [
	'ActionModel',
	'ActionResult',
	'BrowserSession',
	'Browser',
	'BrowserProfile',
	'Controller',
	'DomService',
	'TraverseServer',
	'ChatOpenAI',
	'ChatGoogle',
	'ChatAnthropic',
	'ChatTraverse',
	'ChatGitHubCopilot',
	'ChatGroq',
	'ChatLiteLLM',
	'ChatMistral',
	'ChatAzureOpenAI',
	'ChatOCIRaw',
	'ChatOllama',
	'ChatVercel',
	'Tools',
	'models',
]
