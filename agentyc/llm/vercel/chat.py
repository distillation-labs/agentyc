import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal, TypeVar, overload

import httpx
from openai import APIConnectionError, APIStatusError, AsyncOpenAI, RateLimitError
from openai.types.chat.chat_completion import ChatCompletion
from openai.types.shared_params.response_format_json_schema import (
	JSONSchema,
	ResponseFormatJSONSchema,
)
from pydantic import BaseModel

from agentyc.llm.base import BaseChatModel
from agentyc.llm.exceptions import ModelProviderError, ModelRateLimitError
from agentyc.llm.messages import BaseMessage, ContentPartTextParam, SystemMessage
from agentyc.llm.schema import SchemaOptimizer
from agentyc.llm.vercel.serializer import VercelMessageSerializer
from agentyc.llm.views import ChatInvokeCompletion, ChatInvokeUsage

T = TypeVar('T', bound=BaseModel)

from agentyc.llm.vercel.models import ChatVercelModel


@dataclass
class ChatVercel(BaseChatModel):
	"""
	A wrapper around Vercel AI Gateway's API, which provides OpenAI-compatible access
	to various LLM models with features like rate limiting, caching, and monitoring.

	Examples:
		```python
	        from agentyc import ChatVercel

	        llm = ChatVercel(model='openai/gpt-4o', api_key='your_vercel_api_key')
		```

	Args:
	    model: The model identifier
	    api_key: Your Vercel AI Gateway API key. If not provided, falls back to
	        AI_GATEWAY_API_KEY or VERCEL_OIDC_TOKEN environment variables.
	    base_url: The Vercel AI Gateway endpoint (defaults to https://ai-gateway.vercel.sh/v1)
	    temperature: Sampling temperature (0-2)
	    max_tokens: Maximum tokens to generate
	    reasoning_models: List of reasoning model patterns (e.g., 'o1', 'gpt-oss') that need
	        prompt-based JSON extraction. Auto-detects common reasoning models by default.
	    timeout: Request timeout in seconds
	    max_retries: Maximum number of retries for failed requests
	    provider_options: Provider routing options for the gateway. Use this to control which
	        providers are used and in what order. Example: {'gateway': {'order': ['vertex', 'anthropic']}}
	    reasoning: Optional provider-specific reasoning configuration. Merged into
	        providerOptions under the appropriate provider key. Example for Anthropic:
	        {'anthropic': {'thinking': {'type': 'adaptive'}}}. Example for OpenAI:
	        {'openai': {'reasoningEffort': 'high', 'reasoningSummary': 'detailed'}}.
	    model_fallbacks: Optional list of fallback model IDs tried in order if the primary
	        model fails. Passed as providerOptions.gateway.models.
	    caching: Optional caching mode for the gateway. Currently supports 'auto', which
	        enables provider-specific prompt caching via providerOptions.gateway.caching.
	"""

	# Model configuration
	model: ChatVercelModel | str

	# Model params
	temperature: float | None = None
	max_tokens: int | None = None
	top_p: float | None = None
	reasoning_models: list[str] | None = field(
		default_factory=lambda: [
			'o1',
			'o3',
			'o4',
			'gpt-oss',
			'gpt-5.2-pro',
			'gpt-5.4-pro',
			'deepseek-r1',
			'-thinking',
			'perplexity/sonar-reasoning',
		]
	)

	# Client initialization parameters
	api_key: str | None = None
	base_url: str | httpx.URL = 'https://ai-gateway.vercel.sh/v1'
	timeout: float | httpx.Timeout | None = None
	max_retries: int = 5
	default_headers: Mapping[str, str] | None = None
	default_query: Mapping[str, object] | None = None
	http_client: httpx.AsyncClient | None = None
	_strict_response_validation: bool = False
	provider_options: dict[str, Any] | None = None
	reasoning: dict[str, dict[str, Any]] | None = None
	model_fallbacks: list[str] | None = None
	caching: Literal['auto'] | None = None

	# Static
	@property
	def provider(self) -> str:
		return 'vercel'

	def _get_client_params(self) -> dict[str, Any]:
		"""Prepare client parameters dictionary."""
		api_key = self.api_key or os.getenv('AI_GATEWAY_API_KEY') or os.getenv('VERCEL_OIDC_TOKEN')

		base_params = {
			'api_key': api_key,
			'base_url': self.base_url,
			'timeout': self.timeout,
			'max_retries': self.max_retries,
			'default_headers': self.default_headers,
			'default_query': self.default_query,
			'_strict_response_validation': self._strict_response_validation,
		}

		client_params = {k: v for k, v in base_params.items() if v is not None}

		if self.http_client is not None:
			client_params['http_client'] = self.http_client

		return client_params

	def get_client(self) -> AsyncOpenAI:
		"""
		Returns an AsyncOpenAI client configured for Vercel AI Gateway.

		Returns:
		    AsyncOpenAI: An instance of the AsyncOpenAI client with Vercel base URL.
		"""
		if not hasattr(self, '_client'):
			client_params = self._get_client_params()
			self._client = AsyncOpenAI(**client_params)
		return self._client

	@property
	def name(self) -> str:
		return str(self.model)

	def _get_usage(self, response: ChatCompletion) -> ChatInvokeUsage | None:
		"""Extract usage information from the Vercel response."""
		if response.usage is None:
			return None

		prompt_details = getattr(response.usage, 'prompt_tokens_details', None)
		cached_tokens = prompt_details.cached_tokens if prompt_details else None

		return ChatInvokeUsage(
			prompt_tokens=response.usage.prompt_tokens,
			prompt_cached_tokens=cached_tokens,
			prompt_cache_creation_tokens=None,
			prompt_image_tokens=None,
			completion_tokens=response.usage.completion_tokens,
			total_tokens=response.usage.total_tokens,
		)

	def _fix_gemini_schema(self, schema: dict[str, Any]) -> dict[str, Any]:
		"""
		Convert a Pydantic model to a Gemini-compatible schema.

		This function removes unsupported properties like 'additionalProperties' and resolves
		$ref references that Gemini doesn't support.
		"""

		# Handle $defs and $ref resolution
		if '$defs' in schema:
			defs = schema.pop('$defs')

			def resolve_refs(obj: Any) -> Any:
				if isinstance(obj, dict):
					if '$ref' in obj:
						ref = obj.pop('$ref')
						ref_name = ref.split('/')[-1]
						if ref_name in defs:
							# Replace the reference with the actual definition
							resolved = defs[ref_name].copy()
							# Merge any additional properties from the reference
							for key, value in obj.items():
								if key != '$ref':
									resolved[key] = value
							return resolve_refs(resolved)
						return obj
					else:
						# Recursively process all dictionary values
						return {k: resolve_refs(v) for k, v in obj.items()}
				elif isinstance(obj, list):
					return [resolve_refs(item) for item in obj]
				return obj

			schema = resolve_refs(schema)

		# Remove unsupported properties
		def clean_schema(obj: Any) -> Any:
			if isinstance(obj, dict):
				# Remove unsupported properties
				cleaned = {}
				for key, value in obj.items():
					if key not in ['additionalProperties', 'title', 'default']:
						cleaned_value = clean_schema(value)
						# Handle empty object properties - Gemini doesn't allow empty OBJECT types
						if (
							key == 'properties'
							and isinstance(cleaned_value, dict)
							and len(cleaned_value) == 0
							and isinstance(obj.get('type', ''), str)
							and obj.get('type', '').upper() == 'OBJECT'
						):
							# Convert empty object to have at least one property
							cleaned['properties'] = {'_placeholder': {'type': 'string'}}
						else:
							cleaned[key] = cleaned_value

				# If this is an object type with empty properties, add a placeholder
				if (
					isinstance(cleaned.get('type', ''), str)
					and cleaned.get('type', '').upper() == 'OBJECT'
					and 'properties' in cleaned
					and isinstance(cleaned['properties'], dict)
					and len(cleaned['properties']) == 0
				):
					cleaned['properties'] = {'_placeholder': {'type': 'string'}}

				# Also remove 'title' from the required list if it exists
				if 'required' in cleaned and isinstance(cleaned.get('required'), list):
					cleaned['required'] = [p for p in cleaned['required'] if p != 'title']

				return cleaned
			elif isinstance(obj, list):
				return [clean_schema(item) for item in obj]
			return obj

		return clean_schema(schema)

	@overload
	async def ainvoke(
		self, messages: list[BaseMessage], output_format: None = None, **kwargs: Any
	) -> ChatInvokeCompletion[str]: ...

	@overload
	async def ainvoke(self, messages: list[BaseMessage], output_format: type[T], **kwargs: Any) -> ChatInvokeCompletion[T]: ...

	async def ainvoke(
		self, messages: list[BaseMessage], output_format: type[T] | None = None, **kwargs: Any
	) -> ChatInvokeCompletion[T] | ChatInvokeCompletion[str]:
		"""
		Invoke the model with the given messages through Vercel AI Gateway.

		Args:
		    messages: List of chat messages
		    output_format: Optional Pydantic model class for structured output

		Returns:
		    Either a string response or an instance of output_format
		"""
		vercel_messages = VercelMessageSerializer.serialize_messages(messages)

		try:
			model_params: dict[str, Any] = {}
			if self.temperature is not None:
				model_params['temperature'] = self.temperature
			if self.max_tokens is not None:
				model_params['max_tokens'] = self.max_tokens
			if self.top_p is not None:
				model_params['top_p'] = self.top_p

			extra_body: dict[str, Any] = {}

			provider_opts: dict[str, Any] = {}
			if self.provider_options:
				provider_opts.update(self.provider_options)

			if self.reasoning:
				# Merge provider-specific reasoning options (ex: {'anthropic': {'thinking': ...}})
				for provider_name, opts in self.reasoning.items():
					existing = provider_opts.get(provider_name, {})
					existing.update(opts)
					provider_opts[provider_name] = existing

			gateway_opts: dict[str, Any] = provider_opts.get('gateway', {})

			if self.model_fallbacks:
				gateway_opts['models'] = self.model_fallbacks

			if self.caching:
				gateway_opts['caching'] = self.caching

			if gateway_opts:
				provider_opts['gateway'] = gateway_opts

			if provider_opts:
				extra_body['providerOptions'] = provider_opts

			if extra_body:
				model_params['extra_body'] = extra_body

			if output_format is None:
				# Return string response
				response = await self.get_client().chat.completions.create(
					model=self.model,
					messages=vercel_messages,
					**model_params,
				)

				usage = self._get_usage(response)
				return ChatInvokeCompletion(
					completion=response.choices[0].message.content or '',
					usage=usage,
					stop_reason=response.choices[0].finish_reason if response.choices else None,
				)

			else:
				is_google_model = self.model.startswith('google/')
				is_anthropic_model = self.model.startswith('anthropic/')
				is_reasoning_model = self.reasoning_models and any(
					str(pattern).lower() in str(self.model).lower() for pattern in self.reasoning_models
				)

				if is_google_model or is_anthropic_model or is_reasoning_model:
					modified_messages = [m.model_copy(deep=True) for m in messages]

					schema = SchemaOptimizer.create_gemini_optimized_schema(output_format)
					json_instruction = f'\n\nIMPORTANT: You must respond with ONLY a valid JSON object (no markdown, no code blocks, no explanations) that exactly matches this schema:\n{json.dumps(schema, indent=2)}'

					instruction_added = False
					if modified_messages and modified_messages[0].role == 'system':
						if isinstance(modified_messages[0].content, str):
							modified_messages[0].content += json_instruction
							instruction_added = True
						elif isinstance(modified_messages[0].content, list):
							modified_messages[0].content.append(ContentPartTextParam(text=json_instruction))
							instruction_added = True
					elif modified_messages and modified_messages[-1].role == 'user':
						if isinstance(modified_messages[-1].content, str):
							modified_messages[-1].content += json_instruction
							instruction_added = True
						elif isinstance(modified_messages[-1].content, list):
							modified_messages[-1].content.append(ContentPartTextParam(text=json_instruction))
							instruction_added = True

					if not instruction_added:
						modified_messages.insert(0, SystemMessage(content=json_instruction))

					vercel_messages = VercelMessageSerializer.serialize_messages(modified_messages)

					response = await self.get_client().chat.completions.create(
						model=self.model,
						messages=vercel_messages,
						**model_params,
					)

					content = response.choices[0].message.content if response.choices else None

					if not content:
						raise ModelProviderError(
							message='No response from model',
							status_code=500,
							model=self.name,
						)

					try:
						text = content.strip()
						if text.startswith('```json') and text.endswith('```'):
							text = text[7:-3].strip()
						elif text.startswith('```') and text.endswith('```'):
							text = text[3:-3].strip()

						parsed_data = json.loads(text)
						parsed = output_format.model_validate(parsed_data)

						usage = self._get_usage(response)
						return ChatInvokeCompletion(
							completion=parsed,
							usage=usage,
							stop_reason=response.choices[0].finish_reason if response.choices else None,
						)

					except (json.JSONDecodeError, ValueError) as e:
						raise ModelProviderError(
							message=f'Failed to parse JSON response: {str(e)}. Raw response: {content[:200]}',
							status_code=500,
							model=self.name,
						) from e

				else:
					schema = SchemaOptimizer.create_optimized_json_schema(output_format)

					response_format_schema: JSONSchema = {
						'name': 'agent_output',
						'strict': True,
						'schema': schema,
					}

					response = await self.get_client().chat.completions.create(
						model=self.model,
						messages=vercel_messages,
						response_format=ResponseFormatJSONSchema(
							json_schema=response_format_schema,
							type='json_schema',
						),
						**model_params,
					)

					content = response.choices[0].message.content if response.choices else None

					if not content:
						raise ModelProviderError(
							message='Failed to parse structured output from model response - empty or null content',
							status_code=500,
							model=self.name,
						)

					usage = self._get_usage(response)
					parsed = output_format.model_validate_json(content)

					return ChatInvokeCompletion(
						completion=parsed,
						usage=usage,
						stop_reason=response.choices[0].finish_reason if response.choices else None,
					)

		except RateLimitError as e:
			raise ModelRateLimitError(message=e.message, model=self.name) from e

		except APIConnectionError as e:
			raise ModelProviderError(message=str(e), model=self.name) from e

		except APIStatusError as e:
			raise ModelProviderError(message=e.message, status_code=e.status_code, model=self.name) from e

		except Exception as e:
			raise ModelProviderError(message=str(e), model=self.name) from e
