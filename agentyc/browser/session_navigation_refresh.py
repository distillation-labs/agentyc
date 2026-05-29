"""Lightweight post-navigation refresh helpers for BrowserSession."""

from __future__ import annotations

from typing import TYPE_CHECKING

from agentyc.browser.session_targets import get_all_frames
from agentyc.utils import create_task_with_error_handling

if TYPE_CHECKING:
	from agentyc.browser.session import BrowserSession


async def _refresh_navigation_target_state(session: BrowserSession, target_id: str) -> None:
	"""Refresh lightweight per-tab state after navigation without re-running full focus handlers."""
	if session._dom_watchdog:
		session._dom_watchdog.clear_cache()
	session._cached_browser_state_summary = None
	session._cached_selector_map.clear()
	session._cached_frame_snapshot = None
	session._cached_frame_snapshot_target_id = None
	session._cached_frame_snapshot_url = None
	session._cached_frame_snapshot_has_backend_node_ids = False
	session._cached_frame_snapshot_at = 0.0
	create_task_with_error_handling(
		session._apply_runtime_markers_to_target(target_id),
		name='refresh_navigation_runtime_markers',
		logger_instance=session.logger,
		suppress_exceptions=True,
	)
	if session.agent_focus_target_id == target_id:
		create_task_with_error_handling(
			get_all_frames(session, include_backend_node_ids=False),
			name='refresh_navigation_frame_snapshot',
			logger_instance=session.logger,
			suppress_exceptions=True,
		)
