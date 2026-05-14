"""
We have switched all of our code from langchain to openai.types.chat.chat_completion_message_param.

For easier transition we have
"""

from typing import TYPE_CHECKING

# Lightweight imports that are commonly used
from agentyc.llm.base import BaseChatModel
from agentyc.llm.messages import (
	AssistantMessage,
	BaseMessage,
	SystemMessage,
	UserMessage,
)
from agentyc.llm.messages import (
	ContentPartImageParam as ContentImage,
)
from agentyc.llm.messages import (
	ContentPartRefusalParam as ContentRefusal,
)
from agentyc.llm.messages import (
	ContentPartTextParam as ContentText,
)

# Type stubs for lazy imports
if TYPE_CHECKING:
	from agentyc.llm.anthropic.chat import ChatAnthropic
	from agentyc.llm.aws.chat_anthropic import ChatAnthropicBedrock
	from agentyc.llm.aws.chat_bedrock import ChatAWSBedrock
	from agentyc.llm.azure.chat import ChatAzureOpenAI
	from agentyc.llm.cerebras.chat import ChatCerebras
	from agentyc.llm.copilot.chat import ChatGitHubCopilot
	from agentyc.llm.deepseek.chat import ChatDeepSeek
	from agentyc.llm.google.chat import ChatGoogle
	from agentyc.llm.groq.chat import ChatGroq
	from agentyc.llm.mistral.chat import ChatMistral
	from agentyc.llm.oci_raw.chat import ChatOCIRaw
	from agentyc.llm.ollama.chat import ChatOllama
	from agentyc.llm.openai.chat import ChatOpenAI
	from agentyc.llm.openrouter.chat import ChatOpenRouter
	from agentyc.llm.agentyc.chat import ChatAgentyc
	from agentyc.llm.vercel.chat import ChatVercel

	# Type stubs for model instances - enables IDE autocomplete
	openai_gpt_4o: ChatOpenAI
	openai_gpt_4o_mini: ChatOpenAI
	openai_gpt_4_1_mini: ChatOpenAI
	openai_o1: ChatOpenAI
	openai_o1_mini: ChatOpenAI
	openai_o1_pro: ChatOpenAI
	openai_o3: ChatOpenAI
	openai_o3_mini: ChatOpenAI
	openai_o3_pro: ChatOpenAI
	openai_o4_mini: ChatOpenAI
	openai_gpt_5: ChatOpenAI
	openai_gpt_5_mini: ChatOpenAI
	openai_gpt_5_nano: ChatOpenAI

	azure_gpt_4o: ChatAzureOpenAI
	azure_gpt_4o_mini: ChatAzureOpenAI
	azure_gpt_4_1_mini: ChatAzureOpenAI
	azure_o1: ChatAzureOpenAI
	azure_o1_mini: ChatAzureOpenAI
	azure_o1_pro: ChatAzureOpenAI
	azure_o3: ChatAzureOpenAI
	azure_o3_mini: ChatAzureOpenAI
	azure_o3_pro: ChatAzureOpenAI
	azure_gpt_5: ChatAzureOpenAI
	azure_gpt_5_mini: ChatAzureOpenAI

	google_gemini_2_0_flash: ChatGoogle
	google_gemini_2_0_pro: ChatGoogle
	google_gemini_2_5_pro: ChatGoogle
	google_gemini_2_5_flash: ChatGoogle
	google_gemini_2_5_flash_lite: ChatGoogle

# Models are imported on-demand via __getattr__

# Lazy imports mapping for heavy chat models
_LAZY_IMPORTS = {
	'ChatAnthropic': ('agentyc.llm.anthropic.chat', 'ChatAnthropic'),
	'ChatAnthropicBedrock': ('agentyc.llm.aws.chat_anthropic', 'ChatAnthropicBedrock'),
	'ChatAWSBedrock': ('agentyc.llm.aws.chat_bedrock', 'ChatAWSBedrock'),
	'ChatAzureOpenAI': ('agentyc.llm.azure.chat', 'ChatAzureOpenAI'),
	'ChatAgentyc': ('agentyc.llm.agentyc.chat', 'ChatAgentyc'),
	'ChatGitHubCopilot': ('agentyc.llm.copilot.chat', 'ChatGitHubCopilot'),
	'ChatCerebras': ('agentyc.llm.cerebras.chat', 'ChatCerebras'),
	'ChatDeepSeek': ('agentyc.llm.deepseek.chat', 'ChatDeepSeek'),
	'ChatGoogle': ('agentyc.llm.google.chat', 'ChatGoogle'),
	'ChatGroq': ('agentyc.llm.groq.chat', 'ChatGroq'),
	'ChatMistral': ('agentyc.llm.mistral.chat', 'ChatMistral'),
	'ChatOCIRaw': ('agentyc.llm.oci_raw.chat', 'ChatOCIRaw'),
	'ChatOllama': ('agentyc.llm.ollama.chat', 'ChatOllama'),
	'ChatOpenAI': ('agentyc.llm.openai.chat', 'ChatOpenAI'),
	'ChatOpenRouter': ('agentyc.llm.openrouter.chat', 'ChatOpenRouter'),
	'ChatVercel': ('agentyc.llm.vercel.chat', 'ChatVercel'),
}

# Cache for model instances - only created when accessed
_model_cache: dict[str, 'BaseChatModel'] = {}


def __getattr__(name: str):
	"""Lazy import mechanism for heavy chat model imports and model instances."""
	if name in _LAZY_IMPORTS:
		module_path, attr_name = _LAZY_IMPORTS[name]
		try:
			from importlib import import_module

			module = import_module(module_path)
			attr = getattr(module, attr_name)
			return attr
		except ImportError as e:
			raise ImportError(f'Failed to import {name} from {module_path}: {e}') from e

	# Check cache first for model instances
	if name in _model_cache:
		return _model_cache[name]

	# Try to get model instances from models module on-demand
	try:
		from agentyc.llm.models import __getattr__ as models_getattr

		attr = models_getattr(name)
		# Cache in our clean cache dict
		_model_cache[name] = attr
		return attr
	except (AttributeError, ImportError):
		pass

	raise AttributeError(f"module '{__name__}' has no attribute '{name}'")


__all__ = [
	# Message types -> for easier transition from langchain
	'BaseMessage',
	'UserMessage',
	'SystemMessage',
	'AssistantMessage',
	# Content parts with better names
	'ContentText',
	'ContentRefusal',
	'ContentImage',
	# Chat models
	'BaseChatModel',
	'ChatOpenAI',
	'ChatAgentyc',
	'ChatGitHubCopilot',
	'ChatDeepSeek',
	'ChatGoogle',
	'ChatAnthropic',
	'ChatAnthropicBedrock',
	'ChatAWSBedrock',
	'ChatGroq',
	'ChatMistral',
	'ChatAzureOpenAI',
	'ChatOCIRaw',
	'ChatOllama',
	'ChatOpenRouter',
	'ChatVercel',
	'ChatCerebras',
]
