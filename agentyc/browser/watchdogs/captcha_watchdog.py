"""Captcha solver watchdog — monitors captcha events from the browser proxy.

Listens for Agentyc.captchaSolverStarted/Finished CDP events and exposes a
wait_if_captcha_solving() method that the agent step loop uses to block until
a captcha is resolved (with a configurable timeout).

NOTE: Only a single captcha solve is tracked at a time.  If multiple captchas
overlap (e.g. rapid successive navigations), only the latest one is tracked and
earlier in-flight waits may return prematurely.
"""

import asyncio
from dataclasses import dataclass
from typing import Any, ClassVar, Literal, Protocol, cast

from agentyc.browser.events import (
	BrowserConnectedEvent,
	BrowserStoppedEvent,
	CaptchaSolverFinishedEvent,
	CaptchaSolverStartedEvent,
	_get_timeout,
)
from agentyc.browser.watchdog_base import BaseWatchdog
from bubus import BaseEvent
from pydantic import PrivateAttr

CaptchaResultType = Literal['success', 'failed', 'timeout', 'unknown']


class _CaptchaEventPayload(Protocol):
	def get(self, key: str, default: Any = None) -> Any: ...


@dataclass
class CaptchaWaitResult:
	"""Result returned by wait_if_captcha_solving() when the agent had to wait."""

	waited: bool
	vendor: str
	url: str
	duration_ms: int
	result: CaptchaResultType


class CaptchaWatchdog(BaseWatchdog):
	"""Monitors captcha solver events from the browser proxy.

	When the proxy detects a CAPTCHA and starts solving it, a CDP event
	``Agentyc.captchaSolverStarted`` is sent over the WebSocket.  This
	watchdog catches that event and blocks the agent's step loop (via
	``wait_if_captcha_solving``) until ``Agentyc.captchaSolverFinished``
	arrives or the configurable timeout expires.
	"""

	# Event contracts
	LISTENS_TO: ClassVar[list[type[BaseEvent]]] = [
		BrowserConnectedEvent,
		BrowserStoppedEvent,
	]
	EMITS: ClassVar[list[type[BaseEvent]]] = [
		CaptchaSolverStartedEvent,
		CaptchaSolverFinishedEvent,
	]

	# --- private state ---
	_captcha_solving: bool = PrivateAttr(default=False)
	_captcha_solved_event: asyncio.Event = PrivateAttr(default_factory=asyncio.Event)
	_captcha_info: dict[str, Any] = PrivateAttr(default_factory=dict)
	_captcha_result: CaptchaResultType = PrivateAttr(default='unknown')
	_captcha_duration_ms: int = PrivateAttr(default=0)
	_cdp_handlers_registered: bool = PrivateAttr(default=False)

	def model_post_init(self, __context: Any) -> None:
		# Start in "not blocked" state so callers never wait when there is no captcha.
		self._captcha_solved_event.set()

	# ------------------------------------------------------------------
	# Event handlers
	# ------------------------------------------------------------------

	async def on_BrowserConnectedEvent(self, event: BrowserConnectedEvent) -> None:
		"""Register CDP event handlers for Agentyc captcha solver events."""
		if self._cdp_handlers_registered:
			self.logger.debug('CaptchaWatchdog: CDP handlers already registered, skipping')
			return

		cdp_client = self.browser_session.cdp_client
		agentyc_register = getattr(cdp_client.register, 'Agentyc', None)
		if agentyc_register is None:
			self.logger.info('CaptchaWatchdog: Agentyc CDP domain unavailable in cdp_use; captcha event monitoring disabled')
			return

		def _on_captcha_started(event_data: _CaptchaEventPayload, session_id: str | None) -> None:
			try:
				self._captcha_solving = True
				self._captcha_result = 'unknown'
				self._captcha_duration_ms = 0
				self._captcha_info = {
					'vendor': event_data.get('vendor', 'unknown'),
					'url': event_data.get('url', ''),
					'targetId': event_data.get('targetId', ''),
					'startedAt': event_data.get('startedAt', 0),
				}
				# Block any waiter
				self._captcha_solved_event.clear()

				vendor = self._captcha_info['vendor']
				url = self._captcha_info['url']
				self.logger.info(f'🔒 Captcha solving started: {vendor} on {url}')

				self.event_bus.dispatch(
					CaptchaSolverStartedEvent(
						target_id=event_data.get('targetId', ''),
						vendor=vendor,
						url=url,
						started_at=event_data.get('startedAt', 0),
					)
				)
			except Exception:
				self.logger.exception('Error handling captchaSolverStarted CDP event')
				# Ensure consistent state: unblock any waiter
				self._captcha_solving = False
				self._captcha_solved_event.set()

		def _on_captcha_finished(event_data: _CaptchaEventPayload, session_id: str | None) -> None:
			try:
				success = event_data.get('success', False)
				self._captcha_solving = False
				self._captcha_duration_ms = event_data.get('durationMs', 0)
				self._captcha_result = 'success' if success else 'failed'

				vendor = event_data.get('vendor', self._captcha_info.get('vendor', 'unknown'))
				url = event_data.get('url', self._captcha_info.get('url', ''))
				duration_s = self._captcha_duration_ms / 1000

				self.logger.info(f'🔓 Captcha solving finished: {self._captcha_result} — {vendor} on {url} ({duration_s:.1f}s)')

				# Unblock any waiter
				self._captcha_solved_event.set()

				self.event_bus.dispatch(
					CaptchaSolverFinishedEvent(
						target_id=event_data.get('targetId', ''),
						vendor=vendor,
						url=url,
						duration_ms=self._captcha_duration_ms,
						finished_at=event_data.get('finishedAt', 0),
						success=success,
					)
				)
			except Exception:
				self.logger.exception('Error handling captchaSolverFinished CDP event')
				# Ensure consistent state: unblock any waiter
				self._captcha_solving = False
				self._captcha_solved_event.set()

		captcha_solver_started = getattr(agentyc_register, 'captchaSolverStarted', None)
		captcha_solver_finished = getattr(agentyc_register, 'captchaSolverFinished', None)
		if not callable(captcha_solver_started) or not callable(captcha_solver_finished):
			self.logger.info(
				'CaptchaWatchdog: Agentyc captcha CDP events unavailable in cdp_use; captcha event monitoring disabled'
			)
			return

		cast(Any, captcha_solver_started)(_on_captcha_started)
		cast(Any, captcha_solver_finished)(_on_captcha_finished)
		self._cdp_handlers_registered = True
		self.logger.debug('🔒 CaptchaWatchdog: registered CDP event handlers for Agentyc captcha events')

	async def on_BrowserStoppedEvent(self, event: BrowserStoppedEvent) -> None:
		"""Clear captcha state when the browser disconnects so nothing hangs."""
		self._captcha_solving = False
		self._captcha_result = 'unknown'
		self._captcha_duration_ms = 0
		self._captcha_info = {}
		self._captcha_solved_event.set()
		self._cdp_handlers_registered = False

	# ------------------------------------------------------------------
	# Public API
	# ------------------------------------------------------------------

	async def wait_if_captcha_solving(self, timeout: float | None = None) -> CaptchaWaitResult | None:
		"""Wait if a captcha is currently being solved.

		Returns:
			``None`` if no captcha was in progress.
			A ``CaptchaWaitResult`` with the outcome otherwise.
		"""
		if not self._captcha_solving:
			return None

		if timeout is None:
			timeout = _get_timeout('TIMEOUT_CaptchaSolverWait', 120.0)
		assert timeout is not None
		vendor = self._captcha_info.get('vendor', 'unknown')
		url = self._captcha_info.get('url', '')
		self.logger.info(f'⏳ Waiting for {vendor} captcha to be solved on {url} (timeout={timeout}s)...')

		try:
			await asyncio.wait_for(self._captcha_solved_event.wait(), timeout=timeout)
			return CaptchaWaitResult(
				waited=True,
				vendor=vendor,
				url=url,
				duration_ms=self._captcha_duration_ms,
				result=self._captcha_result,
			)
		except TimeoutError:
			# Timed out — unblock and report
			self._captcha_solving = False
			self._captcha_solved_event.set()
			self.logger.warning(f'⏰ Captcha wait timed out after {timeout}s for {vendor} on {url}')
			return CaptchaWaitResult(
				waited=True,
				vendor=vendor,
				url=url,
				duration_ms=int(timeout * 1000),
				result='timeout',
			)
