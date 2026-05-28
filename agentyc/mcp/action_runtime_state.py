"""Browser-state caching helpers for action runtime methods."""

from __future__ import annotations

import asyncio
import json
import time
from typing import TYPE_CHECKING, Any, cast

from agentyc.browser.views import BrowserStateSummary

if TYPE_CHECKING:
	from agentyc.mcp.state import StateMode

_MAX_CLEAN_STATE_REUSE_S = 0.25


def _cache_state_payload(self, payload: dict[str, Any]) -> None:
	payload_url = payload.get('url')
	if isinstance(payload_url, str) and payload_url != self._last_state_cache_url:
		self._last_state_elements_by_ref = {}
		self._last_state_cache_url = payload_url
	elements = payload.get('interactive_elements')
	if not isinstance(elements, list) or not elements:
		return
	self._last_state_elements_by_ref.update(
		{str(element['ref']): element for element in elements if isinstance(element, dict) and element.get('ref')}
	)
	self._browser_state_cache_timestamp = time.monotonic()


def _mark_browser_state_cache_clean(self) -> None:
	self._browser_state_cache_clean = True
	self._browser_state_cache_timestamp = time.monotonic()


def _mark_browser_state_cache_dirty(self) -> None:
	self._browser_state_cache_clean = False


async def _refresh_selector_map(self) -> None:
	if self.browser_session is None:
		return
	await self.browser_session.get_browser_state_summary(include_screenshot=False)


async def _resolve_live_element(
	self,
	*,
	index: int | None = None,
	ref: str | None = None,
) -> tuple[Any | None, int, bool]:
	if self.browser_session is None:
		raise RuntimeError('No browser session active')

	from agentyc.mcp.state import make_element_ref, summarize_interactive_element

	resolved_index = self._resolve_element_index(index=index, ref=ref)
	if ref is not None and self._last_state_elements_by_ref:
		await self._refresh_selector_map()
	element = await self.browser_session.get_dom_element_by_index(resolved_index)
	if element is not None:
		return element, resolved_index, False

	await self._refresh_selector_map()
	element = await self.browser_session.get_dom_element_by_index(resolved_index)
	if element is not None:
		return element, resolved_index, False

	reference_summary = self._last_state_elements_by_ref.get(make_element_ref(resolved_index))
	if reference_summary is None:
		return None, resolved_index, False

	selector_map = await self.browser_session.get_selector_map()
	best_candidate = None
	best_score = 0
	for candidate in selector_map.values():
		candidate_summary = summarize_interactive_element(candidate)
		score = 0
		strong_match = False
		if reference_summary.get('tag') == candidate_summary.get('tag'):
			score += 1
		for field_name, weight in (('text', 6), ('placeholder', 4), ('href', 5), ('context', 3), ('type', 2)):
			reference_value = str(reference_summary.get(field_name, '')).strip().lower()
			candidate_value = str(candidate_summary.get(field_name, '')).strip().lower()
			if not reference_value or not candidate_value:
				continue
			if reference_value == candidate_value:
				score += weight
				if field_name in {'text', 'placeholder', 'href'}:
					strong_match = True
			elif field_name in {'text', 'placeholder'} and len(min(reference_value, candidate_value, key=len)) >= 4:
				# Partial match for transitional text like "Submit" → "Submitting..."
				if reference_value in candidate_value or candidate_value in reference_value:
					score += weight // 2
					strong_match = True
		if reference_summary.get('disabled') == candidate_summary.get('disabled'):
			score += 1
		if strong_match and score > best_score:
			best_candidate = candidate
			best_score = score

	if best_candidate is None or best_score < 6:
		return None, resolved_index, False
	return best_candidate, best_candidate.backend_node_id, True


async def _get_browser_state(
	self,
	include_screenshot: bool = False,
	mode: StateMode = 'auto',
	focus_ref: str | None = None,
	since_hash: str | None = None,
	include_recent_events: bool = False,
) -> tuple[str, str | None]:
	"""Get current browser state. Returns (state_json, screenshot_b64 | None)."""
	if not self.browser_session:
		return 'Error: No browser session active', None

	from agentyc.mcp.state import build_browser_state_payload, compute_browser_state_hash, make_element_ref

	cached_state = cast(BrowserStateSummary | None, getattr(self.browser_session, '_cached_browser_state_summary', None))
	can_use_cached_state = (
		cached_state is not None
		and cached_state.dom_state
		and (not include_recent_events or getattr(cached_state, 'recent_events', None) is not None)
	)
	cache_age_s = max(0.0, time.monotonic() - getattr(self, '_browser_state_cache_timestamp', 0.0))
	cache_is_fresh = cache_age_s <= _MAX_CLEAN_STATE_REUSE_S
	cache_is_clean = bool(getattr(self, '_browser_state_cache_clean', False)) and cache_is_fresh
	if since_hash is not None and cached_state is not None and can_use_cached_state and cache_is_clean:
		cached_hash = getattr(cached_state, 'state_hash', None)
		if cached_hash is None:
			cached_hash = compute_browser_state_hash(cached_state)
			cached_state.state_hash = cached_hash
		if since_hash == cached_hash:
			result = build_browser_state_payload(
				cached_state,
				mode=mode,
				focus_ref=focus_ref,
				since_hash=since_hash,
				include_recent_events=include_recent_events,
			)
			result_json = json.dumps(result, separators=(',', ':'))
			self._cache_state_payload(result)
			return result_json, None

	if (
		cached_state is not None
		and can_use_cached_state
		and cache_is_clean
		and not include_screenshot
		and focus_ref is None
		and since_hash is None
	):
		result = build_browser_state_payload(
			cached_state,
			mode=mode,
			focus_ref=focus_ref,
			since_hash=since_hash,
			include_recent_events=include_recent_events,
		)
		result_json = json.dumps(result, separators=(',', ':'))
		self._cache_state_payload(result)
		return result_json, None

	async def _fetch_state_payload(resolved_focus_ref: str | None) -> tuple[Any, dict[str, Any]]:
		state = await self.browser_session.get_browser_state_summary(
			include_screenshot=include_screenshot,
			include_recent_events=include_recent_events,
		)
		try:
			result = build_browser_state_payload(
				state,
				mode=mode,
				focus_ref=resolved_focus_ref,
				since_hash=since_hash,
				include_recent_events=include_recent_events,
			)
		except ValueError:
			if mode != 'focus' or resolved_focus_ref is None:
				raise
			element, resolved_index, _ = await self._resolve_live_element(ref=resolved_focus_ref)
			if element is None:
				raise
			recovered_focus_ref = make_element_ref(resolved_index)
			state = await self.browser_session.get_browser_state_summary(
				include_screenshot=include_screenshot,
				include_recent_events=include_recent_events,
			)
			result = build_browser_state_payload(
				state,
				mode=mode,
				focus_ref=recovered_focus_ref,
				since_hash=since_hash,
				include_recent_events=include_recent_events,
			)
		return state, result

	state, result = await _fetch_state_payload(focus_ref)

	current_tab = result.get('current_tab') if isinstance(result, dict) else None
	current_tab_has_ownership = isinstance(current_tab, dict) and isinstance(current_tab.get('ownership'), dict)
	has_interactive_elements = bool(result.get('interactive_elements')) if isinstance(result, dict) else False
	is_live_http_page = str(getattr(state, 'url', '') or '').startswith(('http://', 'https://'))
	if (not current_tab_has_ownership and len(getattr(state, 'tabs', []) or []) > 1) or (
		is_live_http_page and not has_interactive_elements
	):
		await asyncio.sleep(0.1)
		state, result = await _fetch_state_payload(focus_ref)

	screenshot_b64 = None
	if include_screenshot and state.screenshot:
		screenshot_b64 = state.screenshot
		if state.page_info:
			result['screenshot_dimensions'] = {
				'width': state.page_info.viewport_width,
				'height': state.page_info.viewport_height,
			}

	self._cache_state_payload(result)
	self._mark_browser_state_cache_clean()
	return json.dumps(result, separators=(',', ':')), screenshot_b64


__all__ = [
	'_cache_state_payload',
	'_get_browser_state',
	'_mark_browser_state_cache_clean',
	'_mark_browser_state_cache_dirty',
	'_refresh_selector_map',
	'_resolve_live_element',
]
