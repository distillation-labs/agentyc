import asyncio
import logging
from contextlib import nullcontext
from typing import Generic, TypeVar, Union

try:
	from lmnr import Laminar  # type: ignore
except ImportError:
	Laminar = None  # type: ignore
from pydantic import BaseModel, create_model

from agentyc.actions import ActionModel, ActionResult
from agentyc.browser import BrowserSession
from agentyc.browser.events import ClickElementEvent, ScrollEvent, TypeTextEvent, UploadFileEvent
from agentyc.browser.views import BrowserError
from agentyc.filesystem.file_system import FileSystem
from agentyc.observability import observe_debug
from agentyc.tools.actions import (
	register_click_action,
	register_done_action,
	register_exploration_actions,
	register_extraction_actions,
	register_file_and_eval_actions,
	register_interaction_actions,
	register_navigation_actions,
	register_rendering_actions,
)
from agentyc.tools.javascript import validate_and_fix_javascript
from agentyc.tools.registry.service import Registry
from agentyc.tools.runtime import coerce_valid_action_timeout, handle_browser_error
from agentyc.utils import time_execution_sync

logger = logging.getLogger(__name__)

ClickElementEvent.model_rebuild()
TypeTextEvent.model_rebuild()
ScrollEvent.model_rebuild()
UploadFileEvent.model_rebuild()

Context = TypeVar('Context')
T = TypeVar('T', bound=BaseModel)


class Tools(Generic[Context]):
	def __init__(
		self,
		exclude_actions: list[str] | None = None,
		output_model: type[T] | None = None,
		display_files_in_done_text: bool = True,
	):
		self.registry = Registry[Context](exclude_actions if exclude_actions is not None else [])
		self.display_files_in_done_text = display_files_in_done_text
		self._output_model: type[BaseModel] | None = output_model
		self._coordinate_clicking_enabled = False

		self._register_done_action(output_model)
		register_navigation_actions(self)
		register_interaction_actions(self)
		register_extraction_actions(self)
		register_exploration_actions(self)
		register_rendering_actions(self)
		register_file_and_eval_actions(self)

	def _validate_and_fix_javascript(self, code: str) -> str:
		return validate_and_fix_javascript(code)

	def _register_done_action(self, output_model: type[T] | None, display_files_in_done_text: bool = True):
		register_done_action(self, output_model, display_files_in_done_text)

	def use_structured_output_action(self, output_model: type[T]):
		self._output_model = output_model
		self._register_done_action(output_model)

	def get_output_model(self) -> type[BaseModel] | None:
		return self._output_model

	def action(self, description: str, **kwargs):
		return self.registry.action(description, **kwargs)

	def exclude_action(self, action_name: str) -> None:
		self.registry.exclude_action(action_name)

	def _register_click_action(self) -> None:
		register_click_action(self)

	def set_coordinate_clicking(self, enabled: bool) -> None:
		if enabled == self._coordinate_clicking_enabled:
			return
		self._coordinate_clicking_enabled = enabled
		self._register_click_action()
		logger.debug(f'Coordinate clicking {"enabled" if enabled else "disabled"}')

	@observe_debug(ignore_input=True, ignore_output=True, name='act')
	@time_execution_sync('--act')
	async def act(
		self,
		action: ActionModel,
		browser_session: BrowserSession,
		page_extraction_llm: None = None,
		available_file_paths: list[str] | None = None,
		file_system: FileSystem | None = None,
		extraction_schema: dict | None = None,
		action_timeout: float | None = None,
	) -> ActionResult:
		timeout_s = coerce_valid_action_timeout(action_timeout)

		for action_name, params in action.model_dump(exclude_unset=True).items():
			if params is None:
				continue

			if Laminar is not None:
				span_context = Laminar.start_as_current_span(
					name=action_name,
					input={'action': action_name, 'params': params},
					span_type='TOOL',
				)
			else:
				span_context = nullcontext()

			with span_context:
				try:
					result = await asyncio.wait_for(
						self.registry.execute_action(
							action_name=action_name,
							params=params,
							browser_session=browser_session,
							file_system=file_system,
									available_file_paths=available_file_paths,
							extraction_schema=extraction_schema,
						),
						timeout=timeout_s,
					)
				except BrowserError as error:
					logger.error(f'❌ Action {action_name} failed with BrowserError: {error}')
					result = handle_browser_error(error)
				except TimeoutError:
					logger.error(
						f'❌ Action {action_name} hit the per-action timeout ({timeout_s:.0f}s) '
						'— likely an unresponsive CDP connection. Returning error so the agent can recover.'
					)
					result = ActionResult(
						error=(
							f'Action {action_name} timed out after {timeout_s:.0f}s. '
							'The browser may be unresponsive (dead CDP WebSocket). '
							'Try again or a different approach.'
						)
					)
				except Exception as error:
					logger.error(f"Action '{action_name}' failed with error: {error}")
					result = ActionResult(error=str(error))

				if Laminar is not None:
					Laminar.set_span_output(result)

			if isinstance(result, str):
				return ActionResult(extracted_content=result)
			if isinstance(result, ActionResult):
				return result
			if result is None:
				return ActionResult()
			raise ValueError(f'Invalid action result type: {type(result)} of {result}')

		return ActionResult()

	def __getattr__(self, name: str):
		if name in self.registry.registry.actions:
			action = self.registry.registry.actions[name]

			async def action_wrapper(**kwargs):
				browser_session = kwargs.get('browser_session')
				special_param_names = {
					'browser_session',
					'file_system',
					'available_file_paths',
					'extraction_schema',
				}
				action_params = {key: value for key, value in kwargs.items() if key not in special_param_names}
				special_kwargs = {
					key: value for key, value in kwargs.items() if key in special_param_names and key != 'browser_session'
				}
				params_instance = action.param_model(**action_params)
				dynamic_action_model = create_model(
					'DynamicActionModel',
					__base__=ActionModel,
					**{name: (Union[action.param_model, None], None)},  # type: ignore
				)
				action_model = dynamic_action_model(**{name: params_instance})
				return await self.act(action=action_model, browser_session=browser_session, **special_kwargs)  # type: ignore[arg-type]

			return action_wrapper

		raise AttributeError(f"'{type(self).__name__}' object has no attribute '{name}'")


Controller = Tools
