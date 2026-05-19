from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from agentyc.browser import session_connection, session_navigation, session_shared_browser
from agentyc.browser.collaboration import apply_title_prefix, extract_title_prefix, strip_title_prefix
from agentyc.browser.events import CloseTabEvent, NavigateToUrlEvent, SwitchTabEvent
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


async def test_session_manager_prefers_most_recent_session_for_target():
	session = BrowserSession(headless=True, user_data_dir=None)
	manager = SessionManager(session)
	manager._targets['target-1'] = Target(target_id='target-1', target_type='page', title='Page')
	manager._sessions = {
		'session-old': SimpleNamespace(session_id='session-old', target_id='target-1'),
		'session-new': SimpleNamespace(session_id='session-new', target_id='target-1'),
	}
	manager._target_sessions['target-1'] = ['session-old', 'session-new']
	manager._sessions['session-new']._lifecycle_events = []

	assert manager._get_session_for_target('target-1').session_id == 'session-new'


async def test_session_manager_waits_for_page_monitoring_before_exposing_session():
	session = BrowserSession(headless=True, user_data_dir=None)
	manager = SessionManager(session)
	manager._targets['target-1'] = Target(target_id='target-1', target_type='page', title='Page')
	manager._sessions = {
		'session-unready': SimpleNamespace(session_id='session-unready', target_id='target-1'),
	}
	manager._target_sessions['target-1'] = ['session-unready']

	assert manager._get_session_for_target('target-1') is None


async def test_session_manager_does_not_fall_back_to_older_ready_page_session():
	session = BrowserSession(headless=True, user_data_dir=None)
	manager = SessionManager(session)
	manager._targets['target-1'] = Target(target_id='target-1', target_type='page', title='Page')
	manager._sessions = {
		'session-old': SimpleNamespace(session_id='session-old', target_id='target-1', _lifecycle_events=[]),
		'session-new': SimpleNamespace(session_id='session-new', target_id='target-1'),
	}
	manager._target_sessions['target-1'] = ['session-old', 'session-new']

	assert manager._get_session_for_target('target-1') is None


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
		display_title='[abcd] Builds',
		ownership=TargetOwnershipMetadata.for_runtime(
			target_id='target-other',
			runtime=RuntimeOwnershipMetadata.create(
				session_id='runtime-abcd',
				runtime_id='runtime-abcd',
				runtime_label='abcd',
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


async def test_get_tabs_reconciles_missing_peer_targets_from_root_discovery():
	session = BrowserSession(
		browser_profile=BrowserProfile(
			headless=True,
			user_data_dir=None,
			cdp_url='ws://example.test/devtools/browser/123',
			shared_browser_mode='tab',
		)
	)
	manager = SessionManager(session)
	owned_target = Target(
		target_id='target-owned',
		target_type='page',
		url='https://example.test/current',
		title='Current',
		display_title=apply_title_prefix('Current', session.runtime_metadata),
	)
	manager._targets = {'target-owned': owned_target}
	manager.set_target_ownership('target-owned', session.runtime_metadata, source='current_runtime')
	session.session_manager = manager
	session._cdp_client_root = SimpleNamespace(
		send=SimpleNamespace(
			Target=SimpleNamespace(
				getTargets=AsyncMock(
					return_value={
						'targetInfos': [
							{
								'targetId': 'target-owned',
								'type': 'page',
								'url': 'https://example.test/current',
								'title': apply_title_prefix('Current', session.runtime_metadata),
							},
						{
							'targetId': 'target-peer',
							'type': 'page',
							'url': 'https://example.test/peer',
							'title': '[abcd] Peer page',
						},
					],
				}
			)
			)
		)
	)

	tabs = await session.get_tabs()

	assert len(tabs) == 2
	peer = next(tab for tab in tabs if tab.target_id == 'target-peer')
	assert peer.ownership is not None
	assert peer.ownership.runtime is not None
	assert peer.ownership.runtime.runtime_label == 'abcd'


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


async def test_shared_navigation_new_tab_does_not_reuse_peer_about_blank():
	session = BrowserSession(
		browser_profile=BrowserProfile(
			headless=True,
			user_data_dir=None,
			cdp_url='ws://example.test/devtools/browser/123',
			shared_browser_mode='tab',
			shared_browser_focus_policy='preserve',
		)
	)
	session._browser_context_id = 'context-123'
	manager = SessionManager(session)
	owned_target = Target(target_id='target-owned', target_type='page', url='https://example.test/current', title='Current')
	peer_blank = Target(
		target_id='target-peer-blank',
		target_type='page',
		url='about:blank',
		title='Blank',
		ownership=TargetOwnershipMetadata.for_runtime(
			target_id='target-peer-blank',
			runtime=RuntimeOwnershipMetadata.create(
				session_id='runtime-peer',
				runtime_id='runtime-peer',
				runtime_label='Peer runtime',
			),
			current_runtime_id=session.runtime_metadata.runtime_id,
			source='explicit_runtime',
		),
	)
	manager._targets = {
		'target-owned': owned_target,
		'target-peer-blank': peer_blank,
	}
	manager.set_target_ownership('target-owned', session.runtime_metadata, source='current_runtime')
	session.session_manager = manager
	session.agent_focus_target_id = 'target-owned'

	async def dispatch(event):
		if isinstance(event, SwitchTabEvent):
			session.agent_focus_target_id = event.target_id
		return None

	object.__setattr__(session, 'event_bus', SimpleNamespace(dispatch=dispatch))

	with (
		patch.object(BrowserSession, '_cdp_create_new_page', new=AsyncMock(return_value='target-new-owned')) as create_page,
		patch.object(session_navigation, '_navigate_and_wait', new=AsyncMock()) as navigate_and_wait,
		patch.object(BrowserSession, '_close_extension_options_pages', new=AsyncMock()),
	):
		await session.on_NavigateToUrlEvent(NavigateToUrlEvent(url='https://example.test/next', new_tab=True))

	create_page.assert_awaited_once_with(
		'about:blank',
		background=True,
	)
	navigate_and_wait.assert_awaited_once()
	assert session.agent_focus_target_id == 'target-new-owned'


async def test_shared_bootstrap_without_browser_context_skips_provisional_page_creation():
	session = BrowserSession(
		browser_profile=BrowserProfile(
			headless=True,
			user_data_dir=None,
			cdp_url='ws://example.test/devtools/browser/123',
			shared_browser_mode='tab',
			shared_browser_focus_policy='preserve',
		)
	)
	manager = SessionManager(session)
	peer_target = Target(target_id='target-peer', target_type='page', url='https://example.test/peer', title='Peer')
	manager._targets = {'target-peer': peer_target}
	session.session_manager = manager
	session._cdp_client_root = SimpleNamespace()
	dispatched_events: list[str] = []

	def dispatch(event):
		dispatched_events.append(event.__class__.__name__)
		return None

	object.__setattr__(session, 'event_bus', SimpleNamespace(dispatch=dispatch))

	with (
		patch.object(BrowserSession, '_cdp_create_new_page', new=AsyncMock()) as create_page,
		patch.object(BrowserSession, '_apply_runtime_markers_to_target', new=AsyncMock()) as apply_runtime_markers,
		patch.object(BrowserSession, '_cdp_get_window_context', new=AsyncMock()) as get_window_context,
	):
		page_targets = await session_connection._bootstrap_page_targets(session)

	assert [target.target_id for target in page_targets] == ['target-peer']
	assert session.agent_focus_target_id is None
	assert dispatched_events == ['TabCreatedEvent']
	create_page.assert_not_awaited()
	apply_runtime_markers.assert_awaited_once_with('target-peer')
	get_window_context.assert_awaited_once_with('target-peer')


async def test_initialize_session_manager_enables_auto_attach_before_monitoring():
	session = BrowserSession(headless=True, user_data_dir=None)
	fake_root = SimpleNamespace(send=SimpleNamespace(Target=SimpleNamespace(setAutoAttach=AsyncMock(return_value={}))))
	session._cdp_client_root = fake_root
	order: list[str] = []

	async def fake_start_monitoring(self):
		order.append('start_monitoring')

	with patch.object(SessionManager, 'start_monitoring', new=fake_start_monitoring):
		await session_connection._initialize_session_manager(session)

	assert session.session_manager is not None
	assert order == ['start_monitoring']
	fake_root.send.Target.setAutoAttach.assert_awaited_once_with(
		params={'autoAttach': True, 'waitForDebuggerOnStart': False, 'flatten': True}
	)


async def test_initialize_existing_targets_does_not_manually_attach_when_auto_attach_is_enabled():
	session = BrowserSession(headless=True, user_data_dir=None)
	manager = SessionManager(session)
	manager.browser_session._cdp_client_root = SimpleNamespace(
		send=SimpleNamespace(
			Target=SimpleNamespace(
				getTargets=AsyncMock(
					return_value={
						'targetInfos': [
							{'targetId': 'page-1', 'type': 'page'},
							{'targetId': 'worker-1', 'type': 'service_worker'},
						]
					}
				),
				attachToTarget=AsyncMock(return_value={}),
			)
		)
	)

	with patch.object(
		SessionManager,
		'_get_session_for_target',
		side_effect=lambda target_id: SimpleNamespace(_lifecycle_events=[]) if target_id == 'page-1' else object(),
	):
		await manager._initialize_existing_targets()

	manager.browser_session._cdp_client_root.send.Target.getTargets.assert_awaited_once()
	manager.browser_session._cdp_client_root.send.Target.attachToTarget.assert_not_awaited()


async def test_navigation_keeps_event_timeout_out_of_inner_page_navigate_budget():
	session = BrowserSession(headless=True, user_data_dir=None)
	manager = SessionManager(session)
	manager._targets = {'target-owned': Target(target_id='target-owned', target_type='page', url='about:blank', title='Blank')}
	session.session_manager = manager
	session.agent_focus_target_id = 'target-owned'

	async def dispatch(_event):
		return None

	object.__setattr__(session, 'event_bus', SimpleNamespace(dispatch=dispatch))

	with (
		patch.object(session_navigation, '_navigate_and_wait', new=AsyncMock()) as navigate_and_wait,
		patch.object(BrowserSession, '_close_extension_options_pages', new=AsyncMock()),
	):
		await session.on_NavigateToUrlEvent(
			NavigateToUrlEvent(url='https://example.test/slow', new_tab=False, event_timeout=30.0)
		)

	navigate_and_wait.assert_awaited_once_with(
		session,
		'https://example.test/slow',
		'target-owned',
		timeout=None,
		wait_until='load',
	)


async def test_navigation_refreshes_current_target_without_re_emitting_focus_change():
	session = BrowserSession(headless=True, user_data_dir=None)
	manager = SessionManager(session)
	manager._targets = {'target-owned': Target(target_id='target-owned', target_type='page', url='about:blank', title='Blank')}
	session.session_manager = manager
	session.agent_focus_target_id = 'target-owned'
	session._cached_browser_state_summary = object()
	session._cached_selector_map['stale'] = object()
	clear_cache = lambda: None
	session._dom_watchdog = SimpleNamespace(clear_cache=clear_cache)
	dispatched_events: list[str] = []

	async def dispatch(event):
		dispatched_events.append(event.__class__.__name__)
		return None

	object.__setattr__(session, 'event_bus', SimpleNamespace(dispatch=dispatch))

	with (
		patch.object(session_navigation, '_navigate_and_wait', new=AsyncMock()),
		patch.object(BrowserSession, '_close_extension_options_pages', new=AsyncMock()),
		patch.object(BrowserSession, '_apply_runtime_markers_to_target', new=AsyncMock()) as apply_runtime_markers,
	):
		await session.on_NavigateToUrlEvent(NavigateToUrlEvent(url='https://example.test/next', new_tab=False))

	assert dispatched_events == ['NavigationStartedEvent', 'NavigationCompleteEvent']
	assert session._cached_browser_state_summary is None
	assert session._cached_selector_map == {}
	apply_runtime_markers.assert_not_awaited()


async def test_shared_switch_tab_none_prefers_most_recent_owned_tab():
	session = BrowserSession(
		browser_profile=BrowserProfile(
			headless=True,
			user_data_dir=None,
			cdp_url='ws://example.test/devtools/browser/123',
			shared_browser_mode='tab',
			shared_browser_focus_policy='preserve',
		)
	)
	manager = SessionManager(session)
	owned_first = Target(target_id='target-owned-1', target_type='page', url='https://example.test/one', title='One')
	owned_second = Target(target_id='target-owned-2', target_type='page', url='https://example.test/two', title='Two')
	peer_target = Target(
		target_id='target-peer',
		target_type='page',
		url='https://example.test/peer',
		title='Peer',
		ownership=TargetOwnershipMetadata.for_runtime(
			target_id='target-peer',
			runtime=RuntimeOwnershipMetadata.create(
				session_id='runtime-peer',
				runtime_id='runtime-peer',
				runtime_label='Peer runtime',
			),
			current_runtime_id=session.runtime_metadata.runtime_id,
			source='explicit_runtime',
		),
	)
	manager._targets = {
		'target-owned-1': owned_first,
		'target-owned-2': owned_second,
		'target-peer': peer_target,
	}
	manager.set_target_ownership('target-owned-1', session.runtime_metadata, source='current_runtime')
	manager.set_target_ownership('target-owned-2', session.runtime_metadata, source='current_runtime')
	session.session_manager = manager
	session.agent_focus_target_id = 'target-owned-1'
	fake_cdp_session = SimpleNamespace(session_id='session-1', cdp_client=SimpleNamespace(send=SimpleNamespace()))

	async def get_or_create(target_id, focus=True):
		if focus:
			session.agent_focus_target_id = target_id
		return fake_cdp_session

	with patch.object(BrowserSession, 'get_or_create_cdp_session', new=AsyncMock(side_effect=get_or_create)):
		result = await session.on_SwitchTabEvent(SwitchTabEvent(target_id=None, activate_target=False))

	assert result == 'target-owned-2'
	assert session.agent_focus_target_id == 'target-owned-2'
	await session.event_bus.stop(clear=True)


async def test_shared_switch_and_close_are_guarded_to_owned_tabs():
	session = BrowserSession(
		browser_profile=BrowserProfile(
			headless=True,
			user_data_dir=None,
			cdp_url='ws://example.test/devtools/browser/123',
			shared_browser_mode='tab',
		)
	)
	manager = SessionManager(session)
	owned_target = Target(target_id='target-owned', target_type='page', url='https://example.test/owned', title='Owned')
	peer_target = Target(
		target_id='target-peer',
		target_type='page',
		url='https://example.test/peer',
		title='Peer',
		ownership=TargetOwnershipMetadata.for_runtime(
			target_id='target-peer',
			runtime=RuntimeOwnershipMetadata.create(
				session_id='runtime-peer',
				runtime_id='runtime-peer',
				runtime_label='Peer runtime',
			),
			current_runtime_id=session.runtime_metadata.runtime_id,
			source='explicit_runtime',
		),
	)
	manager._targets = {'target-owned': owned_target, 'target-peer': peer_target}
	manager.set_target_ownership('target-owned', session.runtime_metadata, source='current_runtime')
	session.session_manager = manager
	session.agent_focus_target_id = 'target-owned'

	with pytest.raises(RuntimeError, match='Cannot switch to tab .*owned by Peer runtime'):
		await session.on_SwitchTabEvent(SwitchTabEvent(target_id='target-peer'))

	with pytest.raises(RuntimeError, match='Cannot close tab .*owned by Peer runtime'):
		await session.on_CloseTabEvent(CloseTabEvent(target_id='target-peer'))


async def test_shared_switch_and_close_are_guarded_from_human_tabs():
	session = BrowserSession(
		browser_profile=BrowserProfile(
			headless=True,
			user_data_dir=None,
			cdp_url='ws://example.test/devtools/browser/123',
			shared_browser_mode='tab',
		)
	)
	manager = SessionManager(session)
	owned_target = Target(target_id='target-owned', target_type='page', url='https://example.test/owned', title='Owned')
	human_target = Target(target_id='target-human', target_type='page', url='https://example.test/human', title='Inbox')
	manager._targets = {'target-owned': owned_target, 'target-human': human_target}
	manager.set_target_ownership('target-owned', session.runtime_metadata, source='current_runtime')
	manager.set_target_human_ownership('target-human')
	session.session_manager = manager
	session.agent_focus_target_id = 'target-owned'

	with pytest.raises(RuntimeError, match='Cannot switch to tab .*owned by Human'):
		await session.on_SwitchTabEvent(SwitchTabEvent(target_id='target-human'))

	with pytest.raises(RuntimeError, match='Cannot close tab .*owned by Human'):
		await session.on_CloseTabEvent(CloseTabEvent(target_id='target-human'))


async def test_shared_new_page_uses_runtime_browser_context_by_default():
	session = BrowserSession(
		browser_profile=BrowserProfile(
			headless=True,
			user_data_dir=None,
			cdp_url='ws://example.test/devtools/browser/123',
			shared_browser_mode='tab',
			shared_browser_focus_policy='preserve',
		)
	)
	session._browser_context_id = 'context-123'
	session.session_manager = SessionManager(session)
	session._cdp_client_root = SimpleNamespace(
		send=SimpleNamespace(Target=SimpleNamespace(createTarget=AsyncMock(return_value={'targetId': 'target-new'})))
	)
	fake_cdp_session = SimpleNamespace(session_id='session-new', target_id='target-new')

	with (
		patch.object(BrowserSession, 'get_or_create_cdp_session', new=AsyncMock(return_value=fake_cdp_session)),
		patch.object(session_shared_browser, '_apply_runtime_markers_to_target', new=AsyncMock()) as apply_markers,
		patch.object(session_shared_browser, '_cdp_get_window_context', new=AsyncMock()) as get_window_context,
	):
		page = await session.new_page('https://example.test/new')

	assert page._target_id == 'target-new'
	create_target = session._cdp_client_root.send.Target.createTarget
	create_target.assert_awaited_once()
	params = create_target.await_args.kwargs['params']
	assert params['url'] == 'https://example.test/new'
	assert params['browserContextId'] == 'context-123'
	assert params['background'] is True
	apply_markers.assert_awaited_once_with(session, 'target-new')
	get_window_context.assert_awaited_once_with(session, 'target-new')


async def test_shared_close_page_rejects_peer_owned_target():
	session = BrowserSession(
		browser_profile=BrowserProfile(
			headless=True,
			user_data_dir=None,
			cdp_url='ws://example.test/devtools/browser/123',
			shared_browser_mode='tab',
		)
	)
	manager = SessionManager(session)
	peer_target = Target(
		target_id='target-peer',
		target_type='page',
		url='https://example.test/peer',
		title='Peer',
		ownership=TargetOwnershipMetadata.for_runtime(
			target_id='target-peer',
			runtime=RuntimeOwnershipMetadata.create(
				session_id='runtime-peer',
				runtime_id='runtime-peer',
				runtime_label='Peer runtime',
			),
			current_runtime_id=session.runtime_metadata.runtime_id,
			source='explicit_runtime',
		),
	)
	manager._targets = {'target-peer': peer_target}
	session.session_manager = manager
	session._cdp_client_root = SimpleNamespace(send=SimpleNamespace(Target=SimpleNamespace(closeTarget=AsyncMock())))

	with pytest.raises(RuntimeError, match='Cannot close tab .*owned by Peer runtime'):
		await session.close_page('target-peer')

	session._cdp_client_root.send.Target.closeTarget.assert_not_awaited()
