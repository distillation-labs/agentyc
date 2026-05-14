import os
from asyncio import base_subprocess
from typing import TYPE_CHECKING

from agentyc.logging_config import setup_logging

# Only set up logging if not in MCP mode or if explicitly requested
if os.environ.get('AGENTYC_SETUP_LOGGING', 'true').lower() != 'false':
	from agentyc.config import CONFIG

	debug_log_file = getattr(CONFIG, 'AGENTYC_DEBUG_LOG_FILE', None)
	info_log_file = getattr(CONFIG, 'AGENTYC_INFO_LOG_FILE', None)
	logger = setup_logging(debug_log_file=debug_log_file, info_log_file=info_log_file)
else:
	import logging

	logger = logging.getLogger('agentyc')

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
	from agentyc.actions import ActionModel, ActionResult
	from agentyc.browser import BrowserProfile, BrowserSession
	from agentyc.browser import BrowserSession as Browser
	from agentyc.dom.service import DomService
	from agentyc.llm import models
	from agentyc.llm.agentyc.chat import ChatAgentyc
	from agentyc.llm.anthropic.chat import ChatAnthropic
	from agentyc.llm.azure.chat import ChatAzureOpenAI
	from agentyc.llm.copilot.chat import ChatGitHubCopilot
	from agentyc.llm.google.chat import ChatGoogle
	from agentyc.llm.groq.chat import ChatGroq
	from agentyc.llm.litellm.chat import ChatLiteLLM
	from agentyc.llm.mistral.chat import ChatMistral
	from agentyc.llm.oci_raw.chat import ChatOCIRaw
	from agentyc.llm.ollama.chat import ChatOllama
	from agentyc.llm.openai.chat import ChatOpenAI
	from agentyc.llm.vercel.chat import ChatVercel
	from agentyc.mcp.server import AgentycServer
	from agentyc.tools.service import Controller, Tools


_LAZY_IMPORTS = {
	'ActionModel': ('agentyc.actions', 'ActionModel'),
	'ActionResult': ('agentyc.actions', 'ActionResult'),
	'BrowserSession': ('agentyc.browser', 'BrowserSession'),
	'Browser': ('agentyc.browser', 'BrowserSession'),
	'BrowserProfile': ('agentyc.browser', 'BrowserProfile'),
	'Tools': ('agentyc.tools.service', 'Tools'),
	'Controller': ('agentyc.tools.service', 'Controller'),
	'DomService': ('agentyc.dom.service', 'DomService'),
	'ChatOpenAI': ('agentyc.llm.openai.chat', 'ChatOpenAI'),
	'ChatGoogle': ('agentyc.llm.google.chat', 'ChatGoogle'),
	'ChatAnthropic': ('agentyc.llm.anthropic.chat', 'ChatAnthropic'),
	'ChatAgentyc': ('agentyc.llm.agentyc.chat', 'ChatAgentyc'),
	'ChatGitHubCopilot': ('agentyc.llm.copilot.chat', 'ChatGitHubCopilot'),
	'ChatGroq': ('agentyc.llm.groq.chat', 'ChatGroq'),
	'ChatLiteLLM': ('agentyc.llm.litellm.chat', 'ChatLiteLLM'),
	'ChatMistral': ('agentyc.llm.mistral.chat', 'ChatMistral'),
	'ChatAzureOpenAI': ('agentyc.llm.azure.chat', 'ChatAzureOpenAI'),
	'ChatOCIRaw': ('agentyc.llm.oci_raw.chat', 'ChatOCIRaw'),
	'ChatOllama': ('agentyc.llm.ollama.chat', 'ChatOllama'),
	'ChatVercel': ('agentyc.llm.vercel.chat', 'ChatVercel'),
	'AgentycServer': ('agentyc.mcp.server', 'AgentycServer'),
	'models': ('agentyc.llm.models', None),
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
	'AgentycServer',
	'ChatOpenAI',
	'ChatGoogle',
	'ChatAnthropic',
	'ChatAgentyc',
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
