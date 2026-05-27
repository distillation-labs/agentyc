from __future__ import annotations

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import RLock
from typing import Literal

HudEventKind = Literal['tool_start', 'tool_done', 'tool_error', 'intent', 'browser_event']
HudEventDetails = dict[str, str | int | float | bool | None]
HudSubscriber = Callable[['HudEvent'], None]


@dataclass(slots=True)
class HudEvent:
	kind: HudEventKind
	label: str
	session_id: str | None = None
	tool_name: str | None = None
	duration_ms: float | None = None
	error: str | None = None
	details: HudEventDetails = field(default_factory=dict)
	timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class HudStream:
	"""Process-local HUD event broadcaster for browser-facing activity surfaces."""

	_instance: HudStream | None = None

	def __init__(self, max_events: int = 200) -> None:
		self._events: deque[HudEvent] = deque(maxlen=max_events)
		self._subscribers: list[HudSubscriber] = []
		self._lock = RLock()

	@classmethod
	def get(cls) -> HudStream:
		if cls._instance is None:
			cls._instance = cls()
		return cls._instance

	def subscribe(self, subscriber: HudSubscriber) -> None:
		with self._lock:
			if subscriber not in self._subscribers:
				self._subscribers.append(subscriber)

	def unsubscribe(self, subscriber: HudSubscriber) -> None:
		with self._lock:
			self._subscribers = [registered for registered in self._subscribers if registered != subscriber]

	def publish(self, event: HudEvent) -> None:
		with self._lock:
			self._events.append(event)
			subscribers = list(self._subscribers)
		for subscriber in subscribers:
			try:
				subscriber(event)
			except Exception:
				pass

	def recent_events(self, *, session_id: str | None = None, limit: int | None = None) -> list[HudEvent]:
		with self._lock:
			events = list(self._events)
		if session_id is not None:
			events = [event for event in events if event.session_id == session_id]
		if limit is not None:
			events = events[-limit:]
		return events


__all__ = ['HudEvent', 'HudEventDetails', 'HudEventKind', 'HudStream']
