from __future__ import annotations

from agentyc.browser.hud_events import publish_intent


async def _set_intent(self, intent: str) -> str:
	normalized = ' '.join(intent.split())
	if not normalized:
		return 'Error: intent must not be empty'

	session_id = getattr(getattr(self, 'browser_session', None), 'id', None)
	publish_intent(session_id=session_id, intent=normalized)
	return f'Intent updated: {normalized[:160]}'


__all__ = ['_set_intent']
