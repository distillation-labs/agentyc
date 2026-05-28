import asyncio
import functools
import inspect
from collections.abc import Callable
from inspect import Parameter, iscoroutinefunction, signature
from types import UnionType
from typing import Optional, Union, get_args, get_origin

from pydantic import BaseModel, create_model

from agentyc.browser import BrowserSession
from agentyc.filesystem.file_system import FileSystem
from agentyc.llm.base import BaseChatModel
from agentyc.tools.registry.views import ActionModel


def get_special_param_types() -> dict[str, type | UnionType | None]:
	"""Get the expected types for special parameters from SpecialActionParameters."""
	return {
		'context': None,
		'browser_session': BrowserSession,
		'page_url': str,
		'cdp_client': None,
		'page_extraction_llm': BaseChatModel,
		'available_file_paths': list,
		'has_sensitive_data': bool,
		'file_system': FileSystem,
		'extraction_schema': None,
	}


def normalize_action_function_signature(
	func: Callable,
	description: str,
	param_model: type[BaseModel] | None = None,
) -> tuple[Callable, type[BaseModel]]:
	"""
	Normalize action function to accept only kwargs.

	Returns:
		- Normalized function that accepts (*_, params: ParamModel, **special_params)
		- The param model to use for registration
	"""
	sig = signature(func)
	parameters = list(sig.parameters.values())
	special_param_types = get_special_param_types()
	special_param_names = set(special_param_types.keys())

	for param in parameters:
		if param.kind == Parameter.VAR_KEYWORD:
			raise ValueError(
				f"Action '{func.__name__}' has **{param.name} which is not allowed. "
				f'Actions must have explicit positional parameters only.'
			)

	action_params = []
	special_params = []
	param_model_provided = param_model is not None

	for i, param in enumerate(parameters):
		if i == 0 and param_model_provided and param.name not in special_param_names:
			continue

		if param.name in special_param_names:
			expected_type = special_param_types.get(param.name)
			if param.annotation != Parameter.empty and expected_type is not None:
				param_type = param.annotation
				origin = get_origin(param_type)
				if origin in {Union, UnionType}:
					args = get_args(param_type)
					param_type = next((arg for arg in args if arg is not type(None)), param_type)

				types_compatible = (
					param_type == expected_type
					or (
						inspect.isclass(param_type)
						and inspect.isclass(expected_type)
						and issubclass(param_type, expected_type)
					)
					or (expected_type is list and (param_type is list or get_origin(param_type) is list))
				)

				if not types_compatible:
					expected_type_name = getattr(expected_type, '__name__', str(expected_type))
					param_type_name = getattr(param_type, '__name__', str(param_type))
					raise ValueError(
						f"Action '{func.__name__}' parameter '{param.name}: {param_type_name}' "
						f"conflicts with special argument injected by tools: '{param.name}: {expected_type_name}'"
					)
			special_params.append(param)
		else:
			action_params.append(param)

	if not param_model_provided:
		if action_params:
			params_dict = {}
			for param in action_params:
				annotation = param.annotation if param.annotation != Parameter.empty else str
				default = ... if param.default == Parameter.empty else param.default
				params_dict[param.name] = (annotation, default)
			param_model = create_model(f'{func.__name__}_Params', __base__=ActionModel, **params_dict)
		else:
			param_model = create_model(f'{func.__name__}_Params', __base__=ActionModel)

	assert param_model is not None, f'param_model is None for {func.__name__}'

	@functools.wraps(func)
	async def normalized_wrapper(*args, params: BaseModel | None = None, **kwargs):
		"""Normalized action that only accepts kwargs."""
		if args:
			raise TypeError(f'{func.__name__}() does not accept positional arguments, only keyword arguments are allowed')

		call_args = []

		if param_model_provided and parameters and parameters[0].name not in special_param_names:
			if params is None:
				raise ValueError(f"{func.__name__}() missing required 'params' argument")
		else:
			if params is None and action_params:
				action_kwargs = {}
				for param in action_params:
					if param.name in kwargs:
						action_kwargs[param.name] = kwargs[param.name]
				if action_kwargs:
					params = param_model(**action_kwargs)

		params_dict = params.model_dump() if params is not None else {}

		for i, param in enumerate(parameters):
			if param_model_provided and i == 0 and param.name not in special_param_names:
				call_args.append(params)
			elif param.name in special_param_names:
				if param.name in kwargs:
					value = kwargs[param.name]
					if value is None and param.default == Parameter.empty:
						raise ValueError(_missing_special_param_message(func.__name__, param.name))
					call_args.append(value)
				elif param.default != Parameter.empty:
					call_args.append(param.default)
				else:
					raise ValueError(_missing_special_param_message(func.__name__, param.name))
			else:
				if param.name in params_dict:
					call_args.append(params_dict[param.name])
				elif param.default != Parameter.empty:
					call_args.append(param.default)
				else:
					raise ValueError(f"{func.__name__}() missing required parameter '{param.name}'")

		if iscoroutinefunction(func):
			return await func(*call_args)
		return await asyncio.to_thread(func, *call_args)

	new_params = [Parameter('params', Parameter.KEYWORD_ONLY, default=None, annotation=Optional[param_model])]
	for special_param in special_params:
		new_params.append(
			Parameter(special_param.name, Parameter.KEYWORD_ONLY, default=special_param.default, annotation=special_param.annotation)
		)
	new_params.append(Parameter('kwargs', Parameter.VAR_KEYWORD))
	normalized_wrapper.__signature__ = sig.replace(parameters=new_params)  # type: ignore[attr-defined]
	return normalized_wrapper, param_model


def create_param_model(function: Callable) -> type[BaseModel]:
	"""Create a Pydantic parameter model from a function signature."""
	from agentyc.tools.registry.views import SpecialActionParameters

	sig = signature(function)
	special_param_names = set(SpecialActionParameters.model_fields.keys())
	params = {
		name: (param.annotation, ... if param.default == param.empty else param.default)
		for name, param in sig.parameters.items()
		if name not in special_param_names
	}
	return create_model(
		f'{function.__name__}_parameters',
		__base__=ActionModel,
		**params,  # type: ignore[arg-type]
	)


def _missing_special_param_message(function_name: str, param_name: str) -> str:
	if param_name == 'browser_session':
		return f'Action {function_name} requires browser_session but none provided.'
	if param_name == 'page_extraction_llm':
		return f'Action {function_name} requires page_extraction_llm but none provided.'
	if param_name == 'file_system':
		return f'Action {function_name} requires file_system but none provided.'
	if param_name == 'page':
		return f'Action {function_name} requires page but none provided.'
	if param_name == 'available_file_paths':
		return f'Action {function_name} requires available_file_paths but none provided.'
	return f"{function_name}() missing required special parameter '{param_name}'"
