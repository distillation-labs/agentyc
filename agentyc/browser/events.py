"""Event definitions for browser communication."""

from __future__ import annotations

import inspect

from bubus import BaseEvent

from agentyc.browser.events_actions import (
	BrowserStateRequestEvent,
	ClickCoordinateEvent,
	ClickElementEvent,
	CloseTabEvent,
	ElementSelectedEvent,
	GetDropdownOptionsEvent,
	GoBackEvent,
	GoForwardEvent,
	NavigateToUrlEvent,
	RefreshEvent,
	ScreenshotEvent,
	ScrollEvent,
	ScrollToTextEvent,
	SelectDropdownOptionEvent,
	SendKeysEvent,
	SwitchTabEvent,
	TypeTextEvent,
	UploadFileEvent,
	WaitEvent,
)
from agentyc.browser.events_browser import (
	AgentFocusChangedEvent,
	BrowserConnectedEvent,
	BrowserErrorEvent,
	BrowserKillEvent,
	BrowserLaunchEvent,
	BrowserLaunchResult,
	BrowserReconnectedEvent,
	BrowserReconnectingEvent,
	BrowserStartEvent,
	BrowserStopEvent,
	BrowserStoppedEvent,
	NavigationCompleteEvent,
	NavigationStartedEvent,
	TabClosedEvent,
	TabCreatedEvent,
	TargetCrashedEvent,
)
from agentyc.browser.events_storage import (
	AboutBlankDVDScreensaverShownEvent,
	CaptchaSolverFinishedEvent,
	CaptchaSolverStartedEvent,
	DialogOpenedEvent,
	DownloadProgressEvent,
	DownloadStartedEvent,
	FileDownloadedEvent,
	LoadStorageStateEvent,
	SaveStorageStateEvent,
	StorageStateLoadedEvent,
	StorageStateSavedEvent,
)
from agentyc.browser.events_support import _get_timeout

__all__ = [
	'_get_timeout',
	'AboutBlankDVDScreensaverShownEvent',
	'AgentFocusChangedEvent',
	'BrowserConnectedEvent',
	'BrowserErrorEvent',
	'BrowserKillEvent',
	'BrowserLaunchEvent',
	'BrowserLaunchResult',
	'BrowserReconnectedEvent',
	'BrowserReconnectingEvent',
	'BrowserStartEvent',
	'BrowserStateRequestEvent',
	'BrowserStopEvent',
	'BrowserStoppedEvent',
	'CaptchaSolverFinishedEvent',
	'CaptchaSolverStartedEvent',
	'ClickCoordinateEvent',
	'ClickElementEvent',
	'CloseTabEvent',
	'DialogOpenedEvent',
	'DownloadProgressEvent',
	'DownloadStartedEvent',
	'ElementSelectedEvent',
	'FileDownloadedEvent',
	'GetDropdownOptionsEvent',
	'GoBackEvent',
	'GoForwardEvent',
	'LoadStorageStateEvent',
	'NavigateToUrlEvent',
	'NavigationCompleteEvent',
	'NavigationStartedEvent',
	'RefreshEvent',
	'SaveStorageStateEvent',
	'ScreenshotEvent',
	'ScrollEvent',
	'ScrollToTextEvent',
	'SelectDropdownOptionEvent',
	'SendKeysEvent',
	'StorageStateLoadedEvent',
	'StorageStateSavedEvent',
	'SwitchTabEvent',
	'TabClosedEvent',
	'TabCreatedEvent',
	'TargetCrashedEvent',
	'TypeTextEvent',
	'UploadFileEvent',
	'WaitEvent',
]

# Note: Model rebuilding for forward references is handled in the importing modules
# Events with 'EnhancedDOMTreeNode' forward references (ClickElementEvent, TypeTextEvent,
# ScrollEvent, UploadFileEvent) need model_rebuild() called after imports are complete


def _check_event_names_dont_overlap():
	"""
	check that event names defined in this file are valid and non-overlapping
	(naiively n^2 so it's pretty slow but ok for now, optimize when >20 events)
	"""
	event_names = {
		name.split('[')[0]
		for name in globals().keys()
		if not name.startswith('_')
		and inspect.isclass(globals()[name])
		and issubclass(globals()[name], BaseEvent)
		and name != 'BaseEvent'
	}
	for name_a in event_names:
		assert name_a.endswith('Event'), f'Event with name {name_a} does not end with "Event"'
		for name_b in event_names:
			if name_a != name_b:  # Skip self-comparison
				assert name_a not in name_b, (
					f'Event with name {name_a} is a substring of {name_b}, all events must be completely unique to avoid find-and-replace accidents'
				)


# overlapping event names are a nightmare to trace and rename later, dont do it!
# e.g. prevent ClickEvent and FailedClickEvent are terrible names because one is a substring of the other,
# must be ClickEvent and ClickFailedEvent to preserve the usefulnes of codebase grep/sed/awk as refactoring tools.
# at import time, we do a quick check that all event names defined above are valid and non-overlapping.
# this is hand written in blood by a human! not LLM slop. feel free to optimize but do not remove it without a good reason.
_check_event_names_dont_overlap()
