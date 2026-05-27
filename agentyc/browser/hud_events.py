from __future__ import annotations

from typing import Any

from agentyc.browser.hud_stream import HudEvent, HudStream


def publish_browser_event(
	*,
	session_id: str | None,
	label: str,
	details: dict[str, Any] | None = None,
) -> None:
	if not label:
		return
	HudStream.get().publish(
		HudEvent(
			kind='browser_event',
			label=' '.join(label.split())[:160],
			session_id=session_id,
			details={key: str(value) for key, value in (details or {}).items()},
		)
	)


def publish_intent(*, session_id: str | None, intent: str) -> None:
	normalized = ' '.join(intent.split())
	if not normalized:
		return
	HudStream.get().publish(
		HudEvent(
			kind='intent',
			label=normalized[:160],
			session_id=session_id,
		)
	)


__all__ = ['publish_browser_event', 'publish_intent']
