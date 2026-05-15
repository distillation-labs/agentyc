from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from agentyc.browser.collaboration import apply_title_prefix, extract_title_prefix, strip_title_prefix
from agentyc.browser.profile import BrowserProfile
from agentyc.browser.session import BrowserSession
from agentyc.browser.session_manager import SessionManager
from agentyc.browser.session_models import BrowserWindowBounds, RuntimeOwnershipMetadata, Target, TargetOwnershipMetadata


async def test_session_manager_strips_prefixed_titles_but_preserves_display_title():
	session = BrowserSession(headless=True, user_data_dir=None)
	manager = SessionManager(session)
	target = Target(target_id='target-1', target_type='page')
	prefixed = apply_title_prefix('Dashboard', session.runtime_metadata)

	manager._targets['target-1'] = target
	manager._apply_target_info(target, {'title': prefixed, 'url': 'https://example.test'})

	assert target.title == 'Dashboard'
	assert target.display_title == prefixed
	assert target.ownership is not None
	assert target.ownership.title_prefix_applied is True


async def test_target_scoped_init_script_tracks_by_target():
	session = BrowserSession(headless=True, user_data_dir=None)
	fake_page = SimpleNamespace(addScriptToEvaluateOnNewDocument=AsyncMock(return_value={'identifier': 'init-1'}))
	fake_send = SimpleNamespace(Page=fake_page)
	fake_cdp_session = SimpleNamespace(session_id='cdp-session', cdp_client=SimpleNamespace(send=fake_send))
	session._cdp_client_root = SimpleNamespace()
	get_or_create = AsyncMock(return_value=fake_cdp_session)

	with patch.object(BrowserSession, 'get_or_create_cdp_session', get_or_create):
		identifier = await session._cdp_add_init_script('window.test = true;', target_id='target-1')

	assert identifier == 'init-1'
	assert session._target_init_scripts == {'target-1': {'init-1'}}
	get_or_create.assert_awaited_once_with(target_id='target-1', focus=False)


async def test_get_tabs_exposes_display_title_and_ownership_metadata():
	session = BrowserSession(headless=True, user_data_dir=None)
	owned_target = Target(
		target_id='target-1',
		target_type='page',
		url='https://example.test',
		title='Project board',
		display_title=apply_title_prefix('Project board', session.runtime_metadata),
		window_bounds=BrowserWindowBounds(left=10, top=20, width=1200, height=800),
	)
	manager = SessionManager(session)
	manager._targets['target-1'] = owned_target
	manager.set_target_ownership('target-1', session.runtime_metadata)
	session.session_manager = manager

	tabs = await session.get_tabs()

	assert len(tabs) == 1
	assert tabs[0].title == 'Project board'
	assert tabs[0].display_title == apply_title_prefix('Project board', session.runtime_metadata)
	assert tabs[0].ownership is not None
	assert tabs[0].ownership.owner_kind == 'agent'
	assert tabs[0].ownership.runtime is not None
	assert tabs[0].ownership.runtime.runtime_id == session.runtime_metadata.runtime_id
	assert tabs[0].window_bounds is not None
	assert tabs[0].window_bounds.width == 1200


async def test_shared_attach_marks_unclaimed_tabs_as_human_owned():
	session = BrowserSession(
		browser_profile=BrowserProfile(
			headless=True,
			user_data_dir=None,
			cdp_url='ws://example.test/devtools/browser/123',
			shared_browser_mode='tab',
		)
	)
	manager = SessionManager(session)
	human_target = Target(target_id='target-human', target_type='page', title='Inbox', display_title='Inbox')
	other_target = Target(
		target_id='target-other',
		target_type='page',
		title='Builds',
		display_title='[Runtime abcd] Builds',
		ownership=TargetOwnershipMetadata.for_runtime(
			target_id='target-other',
			runtime=RuntimeOwnershipMetadata.create(
				session_id='runtime-abcd',
				runtime_id='runtime-abcd',
				runtime_label='Runtime abcd',
				runtime_role='detected',
			),
			current_runtime_id=session.runtime_metadata.runtime_id,
			source='detected_runtime',
			title_prefix_applied=True,
		),
	)
	manager._targets = {'target-human': human_target, 'target-other': other_target}
	session.session_manager = manager

	session._assign_shared_browser_ownership([human_target, other_target])

	assert manager.get_target('target-human').ownership is not None
	assert manager.get_target('target-human').ownership.owner_kind == 'human'
	assert manager.get_target('target-human').ownership.runtime is None
	assert manager.get_target('target-other').ownership.runtime is not None
	assert manager.get_target('target-other').ownership.runtime.runtime_id == 'runtime-abcd'


async def test_set_window_bounds_uses_browser_domain_and_updates_target_cache():
	session = BrowserSession(headless=True, user_data_dir=None)
	manager = SessionManager(session)
	manager._targets['target-1'] = Target(target_id='target-1', target_type='page', title='Page')
	session.session_manager = manager
	fake_browser = SimpleNamespace(
		getWindowForTarget=AsyncMock(return_value={'windowId': 7, 'bounds': {'left': 1, 'top': 2, 'width': 800, 'height': 600}}),
		setWindowBounds=AsyncMock(return_value={}),
	)
	session._cdp_client_root = SimpleNamespace(send=SimpleNamespace(Browser=fake_browser))

	result = await session.set_window_bounds({'left': 40, 'top': 50, 'width': 1000, 'height': 700}, target_id='target-1')

	fake_browser.setWindowBounds.assert_awaited_once()
	assert result is not None
	assert manager.get_target('target-1').window_id == 7


def test_title_prefix_helpers_are_stable():
	runtime_session = BrowserSession(headless=True, user_data_dir=None)
	prefixed = apply_title_prefix('Inbox', runtime_session.runtime_metadata)

	assert extract_title_prefix(prefixed) == runtime_session.runtime_metadata.title_prefix
	assert strip_title_prefix(prefixed) == 'Inbox'
	assert strip_title_prefix('Inbox') == 'Inbox'
