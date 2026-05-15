"""Downloads watchdog for monitoring and handling file downloads."""

import asyncio
from typing import Any, ClassVar

from bubus import BaseEvent
from pydantic import PrivateAttr

from agentyc.browser.events import (
    BrowserLaunchEvent,
    BrowserStateRequestEvent,
    BrowserStoppedEvent,
    DownloadProgressEvent,
    DownloadStartedEvent,
    FileDownloadedEvent,
    NavigationCompleteEvent,
    TabClosedEvent,
    TabCreatedEvent,
)
from agentyc.browser.watchdog_base import BaseWatchdog
from agentyc.browser.watchdogs.downloads_core import DownloadsCoreMixin
from agentyc.browser.watchdogs.downloads_network import DownloadsNetworkMixin


class DownloadsWatchdog(DownloadsCoreMixin, DownloadsNetworkMixin, BaseWatchdog):
    """Monitors downloads and handles file download events."""

    LISTENS_TO: ClassVar[list[type[BaseEvent[Any]]]] = [
        BrowserLaunchEvent,
        BrowserStateRequestEvent,
        BrowserStoppedEvent,
        TabCreatedEvent,
        TabClosedEvent,
        NavigationCompleteEvent,
    ]

    EMITS: ClassVar[list[type[BaseEvent[Any]]]] = [
        DownloadProgressEvent,
        DownloadStartedEvent,
        FileDownloadedEvent,
    ]

    _sessions_with_listeners: set[str] = PrivateAttr(default_factory=set)
    _active_downloads: dict[str, Any] = PrivateAttr(default_factory=dict)
    _pdf_viewer_cache: dict[str, bool] = PrivateAttr(default_factory=dict)
    _download_cdp_session_setup: bool = PrivateAttr(default=False)
    _download_cdp_session: Any = PrivateAttr(default=None)
    _cdp_event_tasks: set[asyncio.Task] = PrivateAttr(default_factory=set)
    _cdp_downloads_info: dict[str, dict[str, Any]] = PrivateAttr(default_factory=dict)
    _session_pdf_urls: dict[str, str] = PrivateAttr(default_factory=dict)
    _initial_downloads_snapshot: set[str] = PrivateAttr(default_factory=set)
    _network_monitored_targets: set[str] = PrivateAttr(default_factory=set)
    _detected_downloads: set[str] = PrivateAttr(default_factory=set)
    _network_callback_registered: bool = PrivateAttr(default=False)

    _download_start_callbacks: list[Any] = PrivateAttr(default_factory=list)
    _download_progress_callbacks: list[Any] = PrivateAttr(default_factory=list)
    _download_complete_callbacks: list[Any] = PrivateAttr(default_factory=list)


# Fix Pydantic circular dependency - this will be called from session.py after BrowserSession is defined
