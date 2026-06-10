from __future__ import annotations


async def _set_intent(self, intent: str) -> str:
	normalized = ' '.join(intent.split())
	if not normalized:
		return 'Error: intent must not be empty'
	return f'Intent updated: {normalized[:160]}'


__all__ = ['_set_intent']
