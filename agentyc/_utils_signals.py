import asyncio
import os
import platform
import signal
from collections.abc import Callable
from sys import stderr

from agentyc._utils_core import logger

# Global flag to prevent duplicate exit messages
_exiting = False


class SignalHandler:
	"""
	A modular and reusable signal handling system for managing SIGINT (Ctrl+C), SIGTERM,
	and other signals in asyncio applications.
	"""

	def __init__(
		self,
		loop: asyncio.AbstractEventLoop | None = None,
		pause_callback: Callable[[], None] | None = None,
		resume_callback: Callable[[], None] | None = None,
		custom_exit_callback: Callable[[], None] | None = None,
		exit_on_second_int: bool = True,
		interruptible_task_patterns: list[str] | None = None,
		disabled: bool = False,
	):
		self.loop = loop or asyncio.get_event_loop()
		self.pause_callback = pause_callback
		self.resume_callback = resume_callback
		self.custom_exit_callback = custom_exit_callback
		self.exit_on_second_int = exit_on_second_int
		self.interruptible_task_patterns = interruptible_task_patterns or ['step', 'multi_act', 'get_next_action']
		self.is_windows = platform.system() == 'Windows'
		self.disabled = disabled

		self._initialize_loop_state()
		self.original_sigint_handler = None
		self.original_sigterm_handler = None

	def _initialize_loop_state(self) -> None:
		"""Initialize loop state attributes used for signal handling."""
		setattr(self.loop, 'ctrl_c_pressed', False)
		setattr(self.loop, 'waiting_for_input', False)

	def register(self) -> None:
		"""Register signal handlers for SIGINT and SIGTERM."""
		if self.disabled:
			return

		try:
			if self.is_windows:

				def windows_handler(sig, frame):
					print('\n\n🛑 Got Ctrl+C. Exiting immediately on Windows...\n', file=stderr)
					if self.custom_exit_callback:
						self.custom_exit_callback()
					os._exit(0)

				self.original_sigint_handler = signal.signal(signal.SIGINT, windows_handler)
			else:
				self.original_sigint_handler = self.loop.add_signal_handler(signal.SIGINT, lambda: self.sigint_handler())
				self.original_sigterm_handler = self.loop.add_signal_handler(signal.SIGTERM, lambda: self.sigterm_handler())
		except Exception:
			pass

	def unregister(self) -> None:
		"""Unregister signal handlers and restore original handlers if possible."""
		if self.disabled:
			return

		try:
			if self.is_windows:
				if self.original_sigint_handler:
					signal.signal(signal.SIGINT, self.original_sigint_handler)
			else:
				self.loop.remove_signal_handler(signal.SIGINT)
				self.loop.remove_signal_handler(signal.SIGTERM)
				if self.original_sigint_handler:
					signal.signal(signal.SIGINT, self.original_sigint_handler)
				if self.original_sigterm_handler:
					signal.signal(signal.SIGTERM, self.original_sigterm_handler)
		except Exception as e:
			logger.warning(f'Error while unregistering signal handlers: {e}')

	def _handle_second_ctrl_c(self) -> None:
		"""Handle a second Ctrl+C press by performing cleanup and exiting."""
		global _exiting

		if not _exiting:
			_exiting = True
			if self.custom_exit_callback:
				try:
					self.custom_exit_callback()
				except Exception as e:
					logger.error(f'Error in exit callback: {e}')

		print('\n\n🛑  Got second Ctrl+C. Exiting immediately...\n', file=stderr)
		print('\033[?25h', end='', flush=True, file=stderr)
		print('\033[?25h', end='', flush=True)
		print('\033[0m', end='', flush=True, file=stderr)
		print('\033[0m', end='', flush=True)
		print('\033[?1l', end='', flush=True, file=stderr)
		print('\033[?1l', end='', flush=True)
		print('\033[?2004l', end='', flush=True, file=stderr)
		print('\033[?2004l', end='', flush=True)
		print('\r', end='', flush=True, file=stderr)
		print('\r', end='', flush=True)
		print('(tip: press [Enter] once to fix escape codes appearing after chrome exit)', file=stderr)
		os._exit(0)

	def sigint_handler(self) -> None:
		"""SIGINT (Ctrl+C) handler."""
		global _exiting

		if _exiting:
			os._exit(0)

		if getattr(self.loop, 'ctrl_c_pressed', False):
			if getattr(self.loop, 'waiting_for_input', False):
				return
			if self.exit_on_second_int:
				self._handle_second_ctrl_c()

		setattr(self.loop, 'ctrl_c_pressed', True)
		self._cancel_interruptible_tasks()

		if self.pause_callback:
			try:
				self.pause_callback()
			except Exception as e:
				logger.error(f'Error in pause callback: {e}')

		print('----------------------------------------------------------------------', file=stderr)

	def sigterm_handler(self) -> None:
		"""SIGTERM handler."""
		global _exiting
		if not _exiting:
			_exiting = True
			print('\n\n🛑 SIGTERM received. Exiting immediately...\n\n', file=stderr)
			if self.custom_exit_callback:
				self.custom_exit_callback()
		os._exit(0)

	def _cancel_interruptible_tasks(self) -> None:
		"""Cancel current tasks that should be interruptible."""
		current_task = asyncio.current_task(self.loop)
		for task in asyncio.all_tasks(self.loop):
			if task != current_task and not task.done():
				task_name = task.get_name() if hasattr(task, 'get_name') else str(task)
				if any(pattern in task_name for pattern in self.interruptible_task_patterns):
					logger.debug(f'Cancelling task: {task_name}')
					task.cancel()
					task.add_done_callback(lambda t: t.exception() if t.cancelled() else None)

		if current_task and not current_task.done():
			task_name = current_task.get_name() if hasattr(current_task, 'get_name') else str(current_task)
			if any(pattern in task_name for pattern in self.interruptible_task_patterns):
				logger.debug(f'Cancelling current task: {task_name}')
				current_task.cancel()

	def wait_for_resume(self) -> None:
		"""Wait for user input to resume or exit."""
		setattr(self.loop, 'waiting_for_input', True)
		original_handler = signal.getsignal(signal.SIGINT)
		try:
			signal.signal(signal.SIGINT, signal.default_int_handler)
		except ValueError:
			pass

		green = '\x1b[32;1m'
		red = '\x1b[31m'
		blink = '\033[33;5m'
		unblink = '\033[0m'
		reset = '\x1b[0m'

		try:
			print(
				f'➡️  Press {green}[Enter]{reset} to resume or {red}[Ctrl+C]{reset} again to exit{blink}...{unblink} ',
				end='',
				flush=True,
				file=stderr,
			)
			input()
			if self.resume_callback:
				self.resume_callback()
		except KeyboardInterrupt:
			self._handle_second_ctrl_c()
		finally:
			try:
				signal.signal(signal.SIGINT, original_handler)
				setattr(self.loop, 'waiting_for_input', False)
			except Exception:
				pass

	def reset(self) -> None:
		"""Reset state after resuming."""
		if hasattr(self.loop, 'ctrl_c_pressed'):
			setattr(self.loop, 'ctrl_c_pressed', False)
		if hasattr(self.loop, 'waiting_for_input'):
			setattr(self.loop, 'waiting_for_input', False)
