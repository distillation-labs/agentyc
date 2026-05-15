"""Default browser action handlers using CDP."""

from agentyc.browser.events import (
    ClickCoordinateEvent,
    ClickElementEvent,
    GetDropdownOptionsEvent,
    ScrollEvent,
    SelectDropdownOptionEvent,
    TypeTextEvent,
    UploadFileEvent,
)
from agentyc.browser.watchdog_base import BaseWatchdog
from agentyc.browser.watchdogs.default_action_clicks import DefaultActionClickMixin
from agentyc.browser.watchdogs.default_action_dropdowns import DefaultActionDropdownMixin
from agentyc.browser.watchdogs.default_action_navigation import DefaultActionNavigationMixin
from agentyc.browser.watchdogs.default_action_text import DefaultActionTextMixin

# Import EnhancedDOMTreeNode and rebuild event models that have forward references to it
# This must be done after all imports are complete
ClickCoordinateEvent.model_rebuild()
ClickElementEvent.model_rebuild()
GetDropdownOptionsEvent.model_rebuild()
SelectDropdownOptionEvent.model_rebuild()
TypeTextEvent.model_rebuild()
ScrollEvent.model_rebuild()
UploadFileEvent.model_rebuild()


class DefaultActionWatchdog(
    DefaultActionClickMixin,
    DefaultActionTextMixin,
    DefaultActionNavigationMixin,
    DefaultActionDropdownMixin,
    BaseWatchdog,
):
    """Handles default browser actions like click, type, and scroll using CDP."""
