"""Demo mode helper for injecting and updating the in-browser log panel."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any

from agentyc.browser.demo_mode_script import DEMO_PANEL_SCRIPT
from agentyc.browser.feedback import build_feedback_config
from agentyc.browser.hud_stream import HudEvent, HudStream
from agentyc.browser.session import BrowserSession


class DemoMode:
	"""Encapsulates browser overlay injection and log broadcasting for demo mode."""

	VALID_LEVELS = {'info', 'action', 'thought', 'error', 'success', 'warning'}

	def __init__(self, session: BrowserSession):
		self.session = session
		self.logger = logging.getLogger(f'{__name__}.DemoMode')
		self._script_identifier: str | None = None
		self._script_source: str | None = None
		self._panel_ready = False
		self._lock = asyncio.Lock()
		self._stream = HudStream.get()
		self._subscribed = False
		self._stream.subscribe(self._on_hud_event)
		self._subscribed = True

	def reset(self) -> None:
		self._script_identifier = None
		self._panel_ready = False

	def cleanup(self) -> None:
		if not self._subscribed:
			return
		self._stream.unsubscribe(self._on_hud_event)
		self._subscribed = False

	def _load_script(self) -> str:
		if self._script_source is None:
			self._script_source = DEMO_PANEL_SCRIPT

		config = build_feedback_config(self.session.id)
		script = self._script_source.replace('__AGENTYC_HUD_CONFIG_PLACEHOLDER__', json.dumps(config, ensure_ascii=False))
		self.logger.debug(f'Injecting HUD config for session {self.session.id}')
		return script

	async def ensure_ready(self) -> None:
		"""Add init script and inject overlay into currently open pages."""
		if not self.session.browser_profile.demo_mode:
			return
		if self.session._cdp_client_root is None:
			raise RuntimeError('Root CDP client not initialized')

		async with self._lock:
			script = self._load_script()
			if self._script_identifier is None:
				self._script_identifier = await self.session._cdp_add_init_script(script)
				self.logger.debug('Added auto-injection script for demo overlay')

			await self._inject_into_open_pages(script)
			self._panel_ready = True
			self.logger.debug('Demo overlay injected successfully')

	async def send_log(self, message: str, level: str = 'info', metadata: dict[str, Any] | None = None) -> None:
		"""Send a log entry to the in-browser panel."""
		if not message or not self.session.browser_profile.demo_mode:
			return

		try:
			await self.ensure_ready()
		except Exception as exc:
			self.logger.warning(f'Failed to ensure demo mode is ready: {exc}')
			return

		if self.session.agent_focus_target_id is None:
			self.logger.debug('Cannot send demo log: no active target')
			return

		level_value = level.lower()
		if level_value not in self.VALID_LEVELS:
			level_value = 'info'

		payload = {
			'message': message,
			'level': level_value,
			'metadata': {'session_id': self.session.id, **(metadata or {})},
			'timestamp': datetime.now(timezone.utc).isoformat(),
		}

		script = self._build_event_expression(json.dumps(payload, ensure_ascii=False))

		try:
			session = await self.session.get_or_create_cdp_session(target_id=None, focus=False)
		except Exception as exc:
			self.logger.debug(f'Cannot acquire CDP session for demo log: {exc}')
			return

		try:
			await session.cdp_client.send.Runtime.evaluate(
				params={'expression': script, 'awaitPromise': False}, session_id=session.session_id
			)
		except Exception as exc:
			self.logger.debug(f'Failed to send demo log: {exc}')

	def _on_hud_event(self, event: HudEvent) -> None:
		if event.session_id != self.session.id:
			return
		try:
			loop = asyncio.get_running_loop()
		except RuntimeError:
			return
		loop.create_task(self._send_hud_event(event))

	async def _send_hud_event(self, event: HudEvent) -> None:
		metadata: dict[str, Any] = {
			'kind': event.kind,
			'session_id': event.session_id,
		}
		if event.tool_name:
			metadata['tool_name'] = event.tool_name
		if event.duration_ms is not None:
			metadata['duration_ms'] = event.duration_ms
		if event.error:
			metadata['error'] = event.error
		if event.details:
			metadata['details'] = event.details
		await self.send_log(event.label, level=self._hud_level(event), metadata=metadata)

	def _hud_level(self, event: HudEvent) -> str:
		if event.kind == 'tool_error':
			return 'error'
		if event.kind == 'tool_done':
			return 'success'
		if event.kind == 'intent':
			return 'thought'
		return 'action'

	def _build_event_expression(self, payload: str) -> str:
		return f"""
(() => {{
	const detail = {payload};
	const event = new CustomEvent('agentyc-log', {{ detail }});
	window.dispatchEvent(event);
}})();
""".strip()

	async def _inject_into_open_pages(self, script: str) -> None:
		targets = await self.session._cdp_get_all_pages(
			include_http=True,
			include_about=True,
			include_pages=True,
			include_iframes=False,
			include_workers=False,
			include_chrome=False,
			include_chrome_extensions=False,
			include_chrome_error=False,
		)

		target_ids = [t['targetId'] for t in targets]
		if not target_ids and self.session.agent_focus_target_id:
			target_ids = [self.session.agent_focus_target_id]

		for target_id in target_ids:
			try:
				await self._inject_into_target(target_id, script)
			except Exception as exc:
				self.logger.debug(f'Failed to inject demo overlay into {target_id}: {exc}')

	async def _inject_into_target(self, target_id: str, script: str) -> None:
		session = await self.session.get_or_create_cdp_session(target_id=target_id, focus=False)
		await session.cdp_client.send.Runtime.evaluate(
			params={'expression': script, 'awaitPromise': False},
			session_id=session.session_id,
		)
