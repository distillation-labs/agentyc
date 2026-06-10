"""Watchdog attachment helpers for BrowserSession."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
	from agentyc.browser.session import BrowserSession


async def attach_all_watchdogs(session: BrowserSession) -> None:
	"""Initialize and attach all watchdogs with explicit handler registration."""
	if session._watchdogs_attached:
		session.logger.debug('Watchdogs already attached, skipping duplicate attachment')
		return

	from agentyc.browser.watchdogs.aboutblank_watchdog import AboutBlankWatchdog
	from agentyc.browser.watchdogs.crash_watchdog import CrashWatchdog
	from agentyc.browser.watchdogs.default_action_watchdog import DefaultActionWatchdog
	from agentyc.browser.watchdogs.dom_watchdog import DOMWatchdog
	from agentyc.browser.watchdogs.downloads_watchdog import DownloadsWatchdog
	from agentyc.browser.watchdogs.local_browser_watchdog import LocalBrowserWatchdog
	from agentyc.browser.watchdogs.permissions_watchdog import PermissionsWatchdog
	from agentyc.browser.watchdogs.popups_watchdog import PopupsWatchdog
	from agentyc.browser.watchdogs.screenshot_watchdog import ScreenshotWatchdog
	from agentyc.browser.watchdogs.security_watchdog import SecurityWatchdog
	from agentyc.browser.watchdogs.storage_state_watchdog import StorageStateWatchdog

	CrashWatchdog.model_rebuild()
	session._crash_watchdog = CrashWatchdog(event_bus=session.event_bus, browser_session=session)
	session._crash_watchdog.attach_to_session()

	DownloadsWatchdog.model_rebuild()
	session._downloads_watchdog = DownloadsWatchdog(event_bus=session.event_bus, browser_session=session)
	session._downloads_watchdog.attach_to_session()
	if session.browser_profile.auto_download_pdfs:
		session.logger.debug('📄 PDF auto-download enabled for this session')

	should_enable_storage_state = (
		session.browser_profile.storage_state is not None or session.browser_profile.user_data_dir is not None
	)
	if should_enable_storage_state:
		StorageStateWatchdog.model_rebuild()
		session._storage_state_watchdog = StorageStateWatchdog(
			event_bus=session.event_bus,
			browser_session=session,
			auto_save_interval=60.0,
			save_on_change=False,
		)
		session._storage_state_watchdog.attach_to_session()
	else:
		session.logger.debug('🍪 StorageStateWatchdog disabled (no storage_state or user_data_dir configured)')

	LocalBrowserWatchdog.model_rebuild()
	session._local_browser_watchdog = LocalBrowserWatchdog(event_bus=session.event_bus, browser_session=session)
	session._local_browser_watchdog.attach_to_session()

	SecurityWatchdog.model_rebuild()
	session._security_watchdog = SecurityWatchdog(event_bus=session.event_bus, browser_session=session)
	session._security_watchdog.attach_to_session()

	AboutBlankWatchdog.model_rebuild()
	session._aboutblank_watchdog = AboutBlankWatchdog(event_bus=session.event_bus, browser_session=session)
	session._aboutblank_watchdog.attach_to_session()

	PopupsWatchdog.model_rebuild()
	session._popups_watchdog = PopupsWatchdog(event_bus=session.event_bus, browser_session=session)
	session._popups_watchdog.attach_to_session()

	PermissionsWatchdog.model_rebuild()
	session._permissions_watchdog = PermissionsWatchdog(event_bus=session.event_bus, browser_session=session)
	session._permissions_watchdog.attach_to_session()

	DefaultActionWatchdog.model_rebuild()
	session._default_action_watchdog = DefaultActionWatchdog(event_bus=session.event_bus, browser_session=session)
	session._default_action_watchdog.attach_to_session()

	ScreenshotWatchdog.model_rebuild()
	session._screenshot_watchdog = ScreenshotWatchdog(event_bus=session.event_bus, browser_session=session)
	session._screenshot_watchdog.attach_to_session()

	DOMWatchdog.model_rebuild()
	session._dom_watchdog = DOMWatchdog(event_bus=session.event_bus, browser_session=session)
	session._dom_watchdog.attach_to_session()

	session._watchdogs_attached = True


__all__ = ['attach_all_watchdogs']
