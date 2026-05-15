import asyncio
import logging
import time
from collections.abc import Callable, Coroutine
from functools import wraps
from typing import Any, ParamSpec, TypeVar

from agentyc._utils_core import logger

R = TypeVar('R')
T = TypeVar('T')
P = ParamSpec('P')


def time_execution_sync(additional_text: str = '') -> Callable[[Callable[P, R]], Callable[P, R]]:
	def decorator(func: Callable[P, R]) -> Callable[P, R]:
		@wraps(func)
		def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
			start_time = time.time()
			result = func(*args, **kwargs)
			execution_time = time.time() - start_time
			if execution_time > 0.25:
				if args and getattr(args[0], 'logger', None):
					log = getattr(args[0], 'logger')
				elif 'agent' in kwargs:
					log = getattr(kwargs['agent'], 'logger')
				elif 'browser_session' in kwargs:
					log = getattr(kwargs['browser_session'], 'logger')
				else:
					log = logging.getLogger(__name__)
				log.debug(f'⏳ {additional_text.strip("-")}() took {execution_time:.2f}s')
			return result

		return wrapper

	return decorator


def time_execution_async(
	additional_text: str = '',
) -> Callable[[Callable[P, Coroutine[Any, Any, R]]], Callable[P, Coroutine[Any, Any, R]]]:
	def decorator(func: Callable[P, Coroutine[Any, Any, R]]) -> Callable[P, Coroutine[Any, Any, R]]:
		@wraps(func)
		async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
			start_time = time.time()
			result = await func(*args, **kwargs)
			execution_time = time.time() - start_time
			if execution_time > 0.25:
				if args and getattr(args[0], 'logger', None):
					log = getattr(args[0], 'logger')
				elif 'agent' in kwargs:
					log = getattr(kwargs['agent'], 'logger')
				elif 'browser_session' in kwargs:
					log = getattr(kwargs['browser_session'], 'logger')
				else:
					log = logging.getLogger(__name__)
				log.debug(f'⏳ {additional_text.strip("-")}() took {execution_time:.2f}s')
			return result

		return wrapper

	return decorator


def singleton(cls):
	instance = [None]

	def wrapper(*args, **kwargs):
		if instance[0] is None:
			instance[0] = cls(*args, **kwargs)
		return instance[0]

	return wrapper


def create_task_with_error_handling(
	coro: Coroutine[Any, Any, T],
	*,
	name: str | None = None,
	logger_instance: logging.Logger | None = None,
	suppress_exceptions: bool = False,
) -> asyncio.Task[T]:
	"""Create an asyncio task with proper exception handling."""
	task = asyncio.create_task(coro, name=name)
	log = logger_instance or logger

	def _handle_task_exception(t: asyncio.Task[T]) -> None:
		exc_to_raise = None
		try:
			exc = t.exception()
			if exc is not None:
				task_name = t.get_name() if hasattr(t, 'get_name') else 'unnamed'
				if suppress_exceptions:
					log.error(f'Exception in background task [{task_name}]: {type(exc).__name__}: {exc}', exc_info=exc)
				else:
					log.warning(f'Exception in background task [{task_name}]: {type(exc).__name__}: {exc}', exc_info=exc)
					exc_to_raise = exc
		except asyncio.CancelledError:
			pass
		except Exception as e:
			task_name = t.get_name() if hasattr(t, 'get_name') else 'unnamed'
			log.error(f'Error handling exception in task [{task_name}]: {type(e).__name__}: {e}')

		if exc_to_raise is not None:
			raise exc_to_raise

	task.add_done_callback(_handle_task_exception)
	return task
