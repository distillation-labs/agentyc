import logging
from collections.abc import Callable
from types import UnionType
from typing import Any, Generic, TypeVar, Union

from pydantic import BaseModel, Field, RootModel, create_model

from agentyc.browser import BrowserSession
from agentyc.filesystem.file_system import FileSystem
from agentyc.llm.base import BaseChatModel
from agentyc.observability import observe_debug
from agentyc.telemetry.service import ProductTelemetry
from agentyc.tools.registry.sensitive_data import log_sensitive_data_usage, replace_sensitive_data
from agentyc.tools.registry.signature_helpers import create_param_model, normalize_action_function_signature
from agentyc.tools.registry.views import (
	ActionModel,
	ActionRegistry,
	RegisteredAction,
)
from agentyc.utils import time_execution_async

Context = TypeVar('Context')

logger = logging.getLogger(__name__)


class Registry(Generic[Context]):
	"""Service for registering and managing actions"""

	def __init__(self, exclude_actions: list[str] | None = None):
		self.registry = ActionRegistry()
		self.telemetry = ProductTelemetry()
		# Create a new list to avoid mutable default argument issues
		self.exclude_actions = list(exclude_actions) if exclude_actions is not None else []

	def exclude_action(self, action_name: str) -> None:
		"""Exclude an action from the registry after initialization.

		If the action is already registered, it will be removed from the registry.
		The action is also added to the exclude_actions list to prevent re-registration.
		"""
		# Add to exclude list to prevent future registration
		if action_name not in self.exclude_actions:
			self.exclude_actions.append(action_name)

		# Remove from registry if already registered
		if action_name in self.registry.actions:
			del self.registry.actions[action_name]
			logger.debug(f'Excluded action "{action_name}" from registry')

	def _get_special_param_types(self) -> dict[str, type | UnionType | None]:
		"""Get the expected types for special parameters from SpecialActionParameters."""
		from agentyc.tools.registry.signature_helpers import get_special_param_types

		return get_special_param_types()

	def _normalize_action_function_signature(
		self,
		func: Callable,
		description: str,
		param_model: type[BaseModel] | None = None,
	) -> tuple[Callable, type[BaseModel]]:
		return normalize_action_function_signature(func, description, param_model)

	# @time_execution_sync('--create_param_model')
	def _create_param_model(self, function: Callable) -> type[BaseModel]:
		"""Creates a Pydantic model from function signature."""
		return create_param_model(function)

	def action(
		self,
		description: str,
		param_model: type[BaseModel] | None = None,
		domains: list[str] | None = None,
		allowed_domains: list[str] | None = None,
		terminates_sequence: bool = False,
	):
		"""Decorator for registering actions"""
		# Handle aliases: domains and allowed_domains are the same parameter
		if allowed_domains is not None and domains is not None:
			raise ValueError("Cannot specify both 'domains' and 'allowed_domains' - they are aliases for the same parameter")

		final_domains = allowed_domains if allowed_domains is not None else domains

		def decorator(func: Callable):
			# Skip registration if action is in exclude_actions
			if func.__name__ in self.exclude_actions:
				return func

			# Normalize the function signature
			normalized_func, actual_param_model = self._normalize_action_function_signature(func, description, param_model)

			action = RegisteredAction(
				name=func.__name__,
				description=description,
				function=normalized_func,
				param_model=actual_param_model,
				domains=final_domains,
				terminates_sequence=terminates_sequence,
			)
			self.registry.actions[func.__name__] = action

			# Return the normalized function so it can be called with kwargs
			return normalized_func

		return decorator

	@observe_debug(ignore_input=True, ignore_output=True, name='execute_action')
	@time_execution_async('--execute_action')
	async def execute_action(
		self,
		action_name: str,
		params: dict,
		browser_session: BrowserSession | None = None,
		page_extraction_llm: BaseChatModel | None = None,
		file_system: FileSystem | None = None,
		sensitive_data: dict[str, str | dict[str, str]] | None = None,
		available_file_paths: list[str] | None = None,
		extraction_schema: dict | None = None,
	) -> Any:
		"""Execute a registered action with simplified parameter handling"""
		if action_name not in self.registry.actions:
			raise ValueError(f'Action {action_name} not found')

		action = self.registry.actions[action_name]
		try:
			# Create the validated Pydantic model
			try:
				validated_params = action.param_model(**params)
			except Exception as e:
				raise ValueError(f'Invalid parameters {params} for action {action_name}: {type(e)}: {e}') from e

			if sensitive_data:
				# Get current URL if browser_session is provided
				current_url = None
				if browser_session and browser_session.agent_focus_target_id:
					try:
						# Get current page info from session_manager
						target = browser_session.session_manager.get_target(browser_session.agent_focus_target_id)
						if target:
							current_url = target.url
					except Exception:
						pass
				validated_params = self._replace_sensitive_data(validated_params, sensitive_data, current_url)

			# Build special context dict
			special_context = {
				'browser_session': browser_session,
				'page_extraction_llm': page_extraction_llm,
				'available_file_paths': available_file_paths,
				'has_sensitive_data': action_name == 'input' and bool(sensitive_data),
				'file_system': file_system,
				'extraction_schema': extraction_schema,
			}

			# Only pass sensitive_data to actions that explicitly need it (input)
			if action_name == 'input':
				special_context['sensitive_data'] = sensitive_data

			# Add CDP-related parameters if browser_session is available
			if browser_session:
				# Add page_url
				try:
					special_context['page_url'] = await browser_session.get_current_page_url()
				except Exception:
					special_context['page_url'] = None

				# Add cdp_client
				special_context['cdp_client'] = browser_session.cdp_client

			# All functions are now normalized to accept kwargs only
			# Call with params and unpacked special context
			try:
				return await action.function(params=validated_params, **special_context)
			except Exception as e:
				raise

		except ValueError as e:
			# Preserve ValueError messages from validation
			if 'requires browser_session but none provided' in str(e) or 'requires page_extraction_llm but none provided' in str(
				e
			):
				raise RuntimeError(str(e)) from e
			else:
				raise RuntimeError(f'Error executing action {action_name}: {str(e)}') from e
		except TimeoutError as e:
			raise RuntimeError(f'Error executing action {action_name} due to timeout.') from e
		except Exception as e:
			raise RuntimeError(f'Error executing action {action_name}: {str(e)}') from e

	def _log_sensitive_data_usage(self, placeholders_used: set[str], current_url: str | None) -> None:
		"""Log when sensitive data is being used on a page."""
		log_sensitive_data_usage(placeholders_used, current_url)

	def _replace_sensitive_data(
		self, params: BaseModel, sensitive_data: dict[str, Any], current_url: str | None = None
	) -> BaseModel:
		"""Replace sensitive data placeholders in params with actual values."""
		return replace_sensitive_data(params, sensitive_data, current_url)

	# @time_execution_sync('--create_action_model')
	def create_action_model(self, include_actions: list[str] | None = None, page_url: str | None = None) -> type[ActionModel]:
		"""Creates a Union of individual action models from registered actions,
		used by LLM APIs that support tool calling & enforce a schema.

		Each action model contains only the specific action being used,
		rather than all actions with most set to None.
		"""

		# Filter actions based on page_url if provided:
		#   if page_url is None, only include actions with no filters
		#   if page_url is provided, only include actions that match the URL

		available_actions: dict[str, RegisteredAction] = {}
		for name, action in self.registry.actions.items():
			if include_actions is not None and name not in include_actions:
				continue

			# If no page_url provided, only include actions with no filters
			if page_url is None:
				if action.domains is None:
					available_actions[name] = action
				continue

			# Check domain filter if present
			domain_is_allowed = self.registry._match_domains(action.domains, page_url)

			# Include action if domain filter matches
			if domain_is_allowed:
				available_actions[name] = action

		# Create individual action models for each action
		individual_action_models: list[type[BaseModel]] = []

		for name, action in available_actions.items():
			# Create an individual model for each action that contains only one field
			individual_model = create_model(
				f'{name.title().replace("_", "")}ActionModel',
				__base__=ActionModel,
				**{
					name: (
						action.param_model,
						Field(description=action.description),
					)  # type: ignore
				},
			)
			individual_action_models.append(individual_model)

		# If no actions available, return empty ActionModel
		if not individual_action_models:
			return create_model('EmptyActionModel', __base__=ActionModel)

		# Create proper Union type that maintains ActionModel interface
		if len(individual_action_models) == 1:
			# If only one action, return it directly (no Union needed)
			result_model = individual_action_models[0]

		# Meaning the length is more than 1
		else:
			# Create a Union type using RootModel that properly delegates ActionModel methods
			union_type = Union[tuple(individual_action_models)]  # type: ignore : Typing doesn't understand that the length is >= 2 (by design)

			class ActionModelUnion(RootModel[union_type]):  # type: ignore
				def get_index(self) -> int | None:
					"""Delegate get_index to the underlying action model"""
					if hasattr(self.root, 'get_index'):
						return self.root.get_index()  # type: ignore
					return None

				def set_index(self, index: int):
					"""Delegate set_index to the underlying action model"""
					if hasattr(self.root, 'set_index'):
						self.root.set_index(index)  # type: ignore

				def model_dump(self, **kwargs):
					"""Delegate model_dump to the underlying action model"""
					if hasattr(self.root, 'model_dump'):
						return self.root.model_dump(**kwargs)  # type: ignore
					return super().model_dump(**kwargs)

			# Set the name for better debugging
			ActionModelUnion.__name__ = 'ActionModel'
			ActionModelUnion.__qualname__ = 'ActionModel'

			result_model = ActionModelUnion

		return result_model  # type:ignore

	def get_prompt_description(self, page_url: str | None = None) -> str:
		"""Get a description of all actions for the prompt

		If page_url is provided, only include actions that are available for that URL
		based on their domain filters
		"""
		return self.registry.get_prompt_description(page_url=page_url)
