import sys
from functools import cache
from typing import Any

from agentyc.utils import logger

from ._profile_types import ViewportSize


@cache
def get_display_size() -> ViewportSize | None:
	try:
		from AppKit import NSScreen  # type: ignore[import]

		screen = NSScreen.mainScreen().frame()
		size = ViewportSize(width=int(screen.size.width), height=int(screen.size.height))
		logger.debug(f'Display size: {size}')
		return size
	except Exception:
		pass

	try:
		from screeninfo import get_monitors

		monitor = get_monitors()[0]
		size = ViewportSize(width=int(monitor.width), height=int(monitor.height))
		logger.debug(f'Display size: {size}')
		return size
	except Exception:
		pass

	logger.debug('No display size found')
	return None


def get_window_adjustments() -> tuple[int, int]:
	"""Returns recommended x, y offsets for window positioning."""
	if sys.platform == 'darwin':
		return -4, 24
	if sys.platform == 'win32':
		return -8, 0
	return 0, 0


def detect_display_configuration(profile: Any) -> None:
	"""Populate display-related defaults on a browser profile."""
	display_size = get_display_size()
	has_screen_available = bool(display_size)
	profile.screen = profile.screen or display_size or ViewportSize(width=1920, height=1080)

	if profile.headless is None:
		profile.headless = not has_screen_available

	user_provided_viewport = profile.viewport is not None

	if profile.headless:
		profile.viewport = profile.viewport or profile.window_size or profile.screen
		profile.window_position = None
		profile.window_size = None
		profile.no_viewport = False
	else:
		profile.window_size = profile.window_size or profile.screen
		if user_provided_viewport:
			profile.no_viewport = False
		else:
			profile.no_viewport = True if profile.no_viewport is None else profile.no_viewport

	if profile.device_scale_factor and profile.no_viewport is None:
		profile.no_viewport = False

	if profile.no_viewport:
		profile.viewport = None
		profile.device_scale_factor = None
		profile.screen = None
		assert profile.viewport is None
		assert profile.no_viewport is True
	else:
		profile.viewport = profile.viewport or profile.screen
		profile.device_scale_factor = profile.device_scale_factor or 1.0
		assert profile.viewport is not None
		assert profile.no_viewport is False

	assert not (profile.headless and profile.no_viewport), (
		'headless=True and no_viewport=True cannot both be set at the same time'
	)
