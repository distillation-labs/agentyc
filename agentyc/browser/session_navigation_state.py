"""Navigation state refresh helpers for BrowserSession."""

from __future__ import annotations

from typing import TYPE_CHECKING

from agentyc.utils import create_task_with_error_handling

if TYPE_CHECKING:
	from agentyc.browser.session import BrowserSession


def clear_session_state_caches(session: BrowserSession) -> None:
	"""Clear cached DOM and frame state after focus or navigation changes."""
	if session._dom_watchdog:
		session._dom_watchdog.clear_cache()
	session._cached_browser_state_summary = None
	session._cached_selector_map.clear()
	session._cached_frame_snapshot = None
	session._cached_frame_snapshot_target_id = None
	session._cached_frame_snapshot_url = None
	session._cached_frame_snapshot_has_backend_node_ids = False
	session._cached_frame_snapshot_at = 0.0


async def refresh_navigation_target_state(session: BrowserSession, target_id: str) -> None:
	"""Refresh lightweight per-tab state after navigation."""
	clear_session_state_caches(session)
