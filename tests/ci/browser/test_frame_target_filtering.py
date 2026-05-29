from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from agentyc.browser.session_targets import get_all_frames


async def test_get_all_frames_skips_targets_without_active_sessions():
	fake_cdp_session = SimpleNamespace(
		session_id='session-1',
		cdp_client=SimpleNamespace(
			send=SimpleNamespace(
				Page=SimpleNamespace(getFrameTree=AsyncMock(return_value={'frameTree': {'frame': {'id': 'frame-1'}}}))
			)
		),
	)
	session = SimpleNamespace(
		browser_profile=SimpleNamespace(cross_origin_iframes=True),
		agent_focus_target_id='live-target',
		logger=SimpleNamespace(debug=Mock()),
		session_manager=SimpleNamespace(is_target_valid=AsyncMock(side_effect=lambda target_id: target_id == 'live-target')),
	)
	targets = [
		{'targetId': 'live-target', 'type': 'page', 'url': 'http://example.test/frame-storage'},
		{'targetId': 'stale-target', 'type': 'page', 'url': 'chrome://newtab/'},
	]

	with (
		patch('agentyc.browser.session_targets._get_cached_frame_snapshot', return_value=None),
		patch('agentyc.browser.session_targets._cdp_get_all_pages', new=AsyncMock(return_value=targets)),
		patch(
			'agentyc.browser.session_targets.get_or_create_cdp_session', new=AsyncMock(return_value=fake_cdp_session)
		) as lookup,
		patch('agentyc.browser.session_targets._cache_frame_snapshot'),
	):
		all_frames, target_sessions = await get_all_frames(session, include_backend_node_ids=False)

	assert all_frames['frame-1']['frameTargetId'] == 'live-target'
	assert target_sessions == {'live-target': 'session-1'}
	lookup.assert_awaited_once_with(session, 'live-target', focus=False)
