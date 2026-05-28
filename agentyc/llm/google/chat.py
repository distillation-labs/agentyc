import asyncio
import json
import logging
import random
import time
from dataclasses import dataclass, field
from typing import Any, Literal, TypeVar, overload

from google import genai
from google.auth.credentials import Credentials
from google.genai import types
from pydantic import BaseModel

from agentyc.llm.base import BaseChatModel
from agentyc.llm.exceptions import ModelProviderError
from agentyc.llm.google.serializer import GoogleMessageSerializer
from agentyc.llm.messages import BaseMessage
from agentyc.llm.schema import SchemaOptimizer
from agentyc.llm.views import ChatInvokeCompletion

T = TypeVar('T', bound=BaseModel)


from agentyc.llm.google.models import VerifiedGeminiModels
from agentyc.llm.google.response_helpers import get_stop_reason, get_usage
from agentyc.llm.google.schema import fix_gemini_schema


@dataclass
class ChatGoogle(BaseChatModel):
	"""
	A wrapper around Google's Gemini chat model using the genai client.

	This class accepts all genai.Client parameters while adding model,
	temperature, and config parameters for the LLM interface.

	Args:
		model: The Gemini model to use
		temperature: Temperature for response generation
		config: Additional configuration parameters to pass to generate_content
			(e.g., tools, safety_settings, etc.).
		api_key: Google API key
		vertexai: Whether to use Vertex AI
		credentials: Google credentials object
		project: Google Cloud project ID
		location: Google Cloud location
		http_options: HTTP options for the client
		include_system_in_user: If True, system messages are included in the first user message
		supports_structured_output: If True, uses native JSON mode; if False, uses prompt-based fallback
		max_retries: Number of retries for retryable errors (default: 5)
		retryable_status_codes: List of HTTP status codes to retry on (default: [429, 500, 502, 503, 504])
		retry_base_delay: Base delay in seconds for exponential backoff (default: 1.0)
		retry_max_delay: Maximum delay in seconds between retries (default: 60.0)

	Example:
		from google.genai import types

		llm = ChatGoogle(
			model='gemini-2.0-flash-exp',
			config={
				'tools': [types.Tool(code_execution=types.ToolCodeExecution())]
			},
			max_retries=5,
			retryable_status_codes=[429, 500, 502, 503, 504],
			retry_base_delay=1.0,
			retry_max_delay=60.0,
		)
	"""

	# Model configuration
	model: VerifiedGeminiModels | str
	temperature: float | None = None
	top_p: float | None = None
	seed: int | None = None
	thinking_budget: int | None = None  # for Gemini 2.5: -1 for dynamic (default), 0 disables, or token count
	thinking_level: Literal['minimal', 'low', 'medium', 'high'] | None = (
		None  # for Gemini 3: Pro supports low/high, Flash supports all levels
	)
	max_output_tokens: int | None = 8096
	config: types.GenerateContentConfigDict | None = None
	include_system_in_user: bool = False
	supports_structured_output: bool = True  # New flag
	max_retries: int = 5  # Number of retries for retryable errors
	retryable_status_codes: list[int] = field(default_factory=lambda: [429, 500, 502, 503, 504])  # Status codes to retry on
	retry_base_delay: float = 1.0  # Base delay in seconds for exponential backoff
	retry_max_delay: float = 60.0  # Maximum delay in seconds between retries

	# Client initialization parameters
	api_key: str | None = None
	vertexai: bool | None = None
	credentials: Credentials | None = None
	project: str | None = None
	location: str | None = None
	http_options: types.HttpOptions | types.HttpOptionsDict | None = None

	# Internal client cache to prevent connection issues
	_client: genai.Client | None = None

	# Static
	@property
	def provider(self) -> str:
		return 'google'

	@property
	def logger(self) -> logging.Logger:
		"""Get logger for this chat instance"""
		return logging.getLogger(f'agentyc.llm.google.{self.model}')

	def _get_client_params(self) -> dict[str, Any]:
		"""Prepare client parameters dictionary."""
		# Define base client params
		base_params = {
			'api_key': self.api_key,
			'vertexai': self.vertexai,
			'credentials': self.credentials,
			'project': self.project,
			'location': self.location,
			'http_options': self.http_options,
		}

		# Create client_params dict with non-None values
		client_params = {k: v for k, v in base_params.items() if v is not None}

		return client_params

	def get_client(self) -> genai.Client:
		"""
		Returns a genai.Client instance.

		Returns:
			genai.Client: An instance of the Google genai client.
		"""
		if self._client is not None:
			return self._client

		client_params = self._get_client_params()
		self._client = genai.Client(**client_params)
		return self._client

	@property
	def name(self) -> str:
		return str(self.model)

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
		Invoke the model with the given messages.

		Args:
			messages: List of chat messages
			output_format: Optional Pydantic model class for structured output

		Returns:
			Either a string response or an instance of output_format
		"""

		# Serialize messages to Google format with the include_system_in_user flag
		contents, system_instruction = GoogleMessageSerializer.serialize_messages(
			messages, include_system_in_user=self.include_system_in_user
		)

		# Build config dictionary starting with user-provided config
		config: types.GenerateContentConfigDict = {}
		if self.config:
			config = self.config.copy()

		# Apply model-specific configuration (these can override config)
		if self.temperature is not None:
			config['temperature'] = self.temperature
		else:
			config['temperature'] = 1.0 if 'gemini-3' in self.model else 0.5

		# Add system instruction if present
		if system_instruction:
			config['system_instruction'] = system_instruction

		if self.top_p is not None:
			config['top_p'] = self.top_p

		if self.seed is not None:
			config['seed'] = self.seed

		# Configure thinking based on model version
		# Gemini 3 Pro: uses thinking_level only
		# Gemini 3 Flash: supports both, defaults to thinking_budget=-1
		# Gemini 2.5: uses thinking_budget only
		is_gemini_3_pro = 'gemini-3-pro' in self.model
		is_gemini_3_flash = 'gemini-3-flash' in self.model

		if is_gemini_3_pro:
			# Validate: thinking_budget should not be set for Gemini 3 Pro
			if self.thinking_budget is not None:
				self.logger.warning(
					f'thinking_budget={self.thinking_budget} is deprecated for Gemini 3 Pro and may cause '
					f'suboptimal performance. Use thinking_level instead.'
				)

			# Validate: minimal/medium only supported on Flash, not Pro
			if self.thinking_level in ('minimal', 'medium'):
				self.logger.warning(
					f'thinking_level="{self.thinking_level}" is not supported for Gemini 3 Pro. '
					f'Only "low" and "high" are valid. Falling back to "low".'
				)
				self.thinking_level = 'low'

			# Default to 'low' for Gemini 3 Pro
			if self.thinking_level is None:
				self.thinking_level = 'low'

			# Map to ThinkingLevel enum (SDK accepts string values)
			level = types.ThinkingLevel(self.thinking_level.upper())
			config['thinking_config'] = types.ThinkingConfigDict(thinking_level=level)
		elif is_gemini_3_flash:
			# Gemini 3 Flash supports both thinking_level and thinking_budget
			# If user set thinking_level, use that; otherwise default to thinking_budget=-1
			if self.thinking_level is not None:
				level = types.ThinkingLevel(self.thinking_level.upper())
				config['thinking_config'] = types.ThinkingConfigDict(thinking_level=level)
			else:
				if self.thinking_budget is None:
					self.thinking_budget = -1
				config['thinking_config'] = types.ThinkingConfigDict(thinking_budget=self.thinking_budget)
		else:
			# Gemini 2.5 and earlier: use thinking_budget only
			if self.thinking_level is not None:
				self.logger.warning(
					f'thinking_level="{self.thinking_level}" is not supported for this model. '
					f'Use thinking_budget instead (0 to disable, -1 for dynamic, or token count).'
				)
			# Default to -1 for dynamic/auto on 2.5 models
			if self.thinking_budget is None and ('gemini-2.5' in self.model or 'gemini-flash' in self.model):
				self.thinking_budget = -1
			if self.thinking_budget is not None:
				config['thinking_config'] = types.ThinkingConfigDict(thinking_budget=self.thinking_budget)

		if self.max_output_tokens is not None:
			config['max_output_tokens'] = self.max_output_tokens

		async def _make_api_call():
			start_time = time.time()
			self.logger.debug(f'🚀 Starting API call to {self.model}')

			try:
				if output_format is None:
					# Return string response
					self.logger.debug('📄 Requesting text response')

					response = await self.get_client().aio.models.generate_content(
						model=self.model,
						contents=contents,  # type: ignore
						config=config,
					)

					elapsed = time.time() - start_time
					self.logger.debug(f'✅ Got text response in {elapsed:.2f}s')

					# Handle case where response.text might be None
					text = response.text or ''
					if not text:
						self.logger.warning('⚠️ Empty text response received')

					usage = get_usage(response)

					return ChatInvokeCompletion(
						completion=text,
						usage=usage,
						stop_reason=get_stop_reason(response),
					)

				else:
					# Handle structured output
					if self.supports_structured_output:
						# Use native JSON mode
						self.logger.debug(f'🔧 Requesting structured output for {output_format.__name__}')
						config['response_mime_type'] = 'application/json'
						# Convert Pydantic model to Gemini-compatible schema
						optimized_schema = SchemaOptimizer.create_gemini_optimized_schema(output_format)

						gemini_schema = fix_gemini_schema(optimized_schema)
						config['response_schema'] = gemini_schema

						response = await self.get_client().aio.models.generate_content(
							model=self.model,
							contents=contents,
							config=config,
						)

						elapsed = time.time() - start_time
						self.logger.debug(f'✅ Got structured response in {elapsed:.2f}s')

						usage = get_usage(response)

						# Handle case where response.parsed might be None
						if response.parsed is None:
							self.logger.debug('📝 Parsing JSON from text response')
							# When using response_schema, Gemini returns JSON as text
							if response.text:
								try:
									# Handle JSON wrapped in markdown code blocks (common Gemini behavior)
									text = response.text.strip()
									if text.startswith('```json') and text.endswith('```'):
										text = text[7:-3].strip()
										self.logger.debug('🔧 Stripped ```json``` wrapper from response')
									elif text.startswith('```') and text.endswith('```'):
										text = text[3:-3].strip()
										self.logger.debug('🔧 Stripped ``` wrapper from response')

									# Parse the JSON text and validate with the Pydantic model
									parsed_data = json.loads(text)
									return ChatInvokeCompletion(
										completion=output_format.model_validate(parsed_data),
										usage=usage,
										stop_reason=get_stop_reason(response),
									)
								except (json.JSONDecodeError, ValueError) as e:
									self.logger.error(f'❌ Failed to parse JSON response: {str(e)}')
									self.logger.debug(f'Raw response text: {response.text[:200]}...')
									raise ModelProviderError(
										message=f'Failed to parse or validate response {response}: {str(e)}',
										status_code=500,
										model=self.model,
									) from e
							else:
								self.logger.error('❌ No response text received')
								raise ModelProviderError(
									message=f'No response from model {response}',
									status_code=500,
									model=self.model,
								)

						# Ensure we return the correct type
						if isinstance(response.parsed, output_format):
							return ChatInvokeCompletion(
								completion=response.parsed,
								usage=usage,
								stop_reason=get_stop_reason(response),
							)
						else:
							# If it's not the expected type, try to validate it
							return ChatInvokeCompletion(
								completion=output_format.model_validate(response.parsed),
								usage=usage,
								stop_reason=get_stop_reason(response),
							)
					else:
						# Fallback: Request JSON in the prompt for models without native JSON mode
						self.logger.debug(f'🔄 Using fallback JSON mode for {output_format.__name__}')
						# Create a copy of messages to modify
						modified_messages = [m.model_copy(deep=True) for m in messages]

						# Add JSON instruction to the last message
						if modified_messages and isinstance(modified_messages[-1].content, str):
							json_instruction = f'\n\nPlease respond with a valid JSON object that matches this schema: {SchemaOptimizer.create_optimized_json_schema(output_format)}'
							modified_messages[-1].content += json_instruction

						# Re-serialize with modified messages
						fallback_contents, fallback_system = GoogleMessageSerializer.serialize_messages(
							modified_messages, include_system_in_user=self.include_system_in_user
						)

						# Update config with fallback system instruction if present
						fallback_config = config.copy()
						if fallback_system:
							fallback_config['system_instruction'] = fallback_system

						response = await self.get_client().aio.models.generate_content(
							model=self.model,
							contents=fallback_contents,  # type: ignore
							config=fallback_config,
						)

						elapsed = time.time() - start_time
						self.logger.debug(f'✅ Got fallback response in {elapsed:.2f}s')

						usage = get_usage(response)

						# Try to extract JSON from the text response
						if response.text:
							try:
								# Try to find JSON in the response
								text = response.text.strip()

								# Common patterns: JSON wrapped in markdown code blocks
								if text.startswith('```json') and text.endswith('```'):
									text = text[7:-3].strip()
								elif text.startswith('```') and text.endswith('```'):
									text = text[3:-3].strip()

								# Parse and validate
								parsed_data = json.loads(text)
								return ChatInvokeCompletion(
									completion=output_format.model_validate(parsed_data),
									usage=usage,
									stop_reason=get_stop_reason(response),
								)
							except (json.JSONDecodeError, ValueError) as e:
								self.logger.error(f'❌ Failed to parse fallback JSON: {str(e)}')
								self.logger.debug(f'Raw response text: {response.text[:200]}...')
								raise ModelProviderError(
									message=f'Model does not support JSON mode and failed to parse JSON from text response: {str(e)}',
									status_code=500,
									model=self.model,
								) from e
						else:
							self.logger.error('❌ No response text in fallback mode')
							raise ModelProviderError(
								message='No response from model',
								status_code=500,
								model=self.model,
							)
			except Exception as e:
				elapsed = time.time() - start_time
				self.logger.error(f'💥 API call failed after {elapsed:.2f}s: {type(e).__name__}: {e}')
				# Re-raise the exception
				raise

		# Retry logic for certain errors with exponential backoff
		assert self.max_retries >= 1, 'max_retries must be at least 1'

		for attempt in range(self.max_retries):
			try:
				return await _make_api_call()
			except ModelProviderError as e:
				# Retry if status code is in retryable list and we have attempts left
				if e.status_code in self.retryable_status_codes and attempt < self.max_retries - 1:
					# Exponential backoff with jitter: base_delay * 2^attempt + random jitter
					delay = min(self.retry_base_delay * (2**attempt), self.retry_max_delay)
					jitter = random.uniform(0, delay * 0.1)  # 10% jitter
					total_delay = delay + jitter
					self.logger.warning(
						f'⚠️ Got {e.status_code} error, retrying in {total_delay:.1f}s... (attempt {attempt + 1}/{self.max_retries})'
					)
					await asyncio.sleep(total_delay)
					continue
				# Otherwise raise
				raise
			except Exception as e:
				# For non-ModelProviderError, wrap and raise
				error_message = str(e)
				status_code: int | None = None

				# Try to extract status code if available
				if hasattr(e, 'response'):
					response_obj = getattr(e, 'response', None)
					if response_obj and hasattr(response_obj, 'status_code'):
						status_code = getattr(response_obj, 'status_code', None)

				# Enhanced timeout error handling
				if 'timeout' in error_message.lower() or 'cancelled' in error_message.lower():
					if isinstance(e, asyncio.CancelledError) or 'CancelledError' in str(type(e)):
						error_message = 'Gemini API request was cancelled (likely timeout). Consider: 1) Reducing input size, 2) Using a different model, 3) Checking network connectivity.'
						status_code = 504
					else:
						status_code = 408
				elif any(indicator in error_message.lower() for indicator in ['forbidden', '403']):
					status_code = 403
				elif any(
					indicator in error_message.lower()
					for indicator in ['rate limit', 'resource exhausted', 'quota exceeded', 'too many requests', '429']
				):
					status_code = 429
				elif any(
					indicator in error_message.lower()
					for indicator in ['service unavailable', 'internal server error', 'bad gateway', '503', '502', '500']
				):
					status_code = 503

				raise ModelProviderError(
					message=error_message,
					status_code=status_code or 502,
					model=self.name,
				) from e

		raise RuntimeError('Retry loop completed without return or exception')
