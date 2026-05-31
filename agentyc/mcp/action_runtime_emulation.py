"""Browser context and environment emulation MCP helpers."""

from __future__ import annotations

from typing import get_args

from cdp_use.cdp import browser as cdp_browser

_SUPPORTED_BROWSER_PERMISSIONS = frozenset(get_args(cdp_browser.PermissionType))
_SUPPORTED_MEDIA = frozenset({'', 'print', 'screen', 'speech'})
_SUPPORTED_COLOR_SCHEMES = frozenset({'dark', 'light', 'no-preference'})
_SUPPORTED_REDUCED_MOTION = frozenset({'no-preference', 'reduce'})
_SUPPORTED_FORCED_COLORS = frozenset({'active', 'none'})


def _invalid_choice_error(name: str, value: str, allowed: frozenset[str]) -> str:
	return f'Error [invalid_argument]: Unsupported {name} "{value}". Allowed: {", ".join(sorted(allowed))}'


async def _grant_permissions(self, permissions: list[str], origin: str | None = None) -> str:
	"""Grant browser permissions via CDP Browser.grantPermissions."""
	if not self.browser_session:
		return 'Error: No browser session active'
	if not permissions:
		return 'Error [invalid_argument]: Provide at least one permission'
	invalid_permissions = sorted(
		{str(permission) for permission in permissions if str(permission) not in _SUPPORTED_BROWSER_PERMISSIONS}
	)
	if invalid_permissions:
		return f'Error [invalid_argument]: Unsupported permissions: {", ".join(invalid_permissions)}'

	self._update_session_activity(self.browser_session.id)
	await self.browser_session._cdp_grant_permissions([str(permission) for permission in permissions], origin=origin)
	if origin:
		return f'Granted permissions {", ".join(permissions)} for {origin}'
	return f'Granted permissions {", ".join(permissions)}'


async def _set_geolocation(self, latitude: float, longitude: float, accuracy: float = 100.0) -> str:
	"""Override geolocation for the current browser session."""
	if not self.browser_session:
		return 'Error: No browser session active'
	if not self.browser_session.agent_focus_target_id:
		return 'Error: No browser page active'

	self._update_session_activity(self.browser_session.id)
	await self.browser_session._cdp_set_geolocation(float(latitude), float(longitude), float(accuracy))
	return f'Set geolocation to ({latitude:.5f}, {longitude:.5f}) with accuracy {accuracy:.1f}m'


async def _set_extra_headers(self, headers: dict[str, str]) -> str:
	"""Set or clear extra HTTP headers for the focused page target."""
	if not self.browser_session:
		return 'Error: No browser session active'
	if not self.browser_session.agent_focus_target_id:
		return 'Error: No browser page active'

	normalized_headers = {str(name): str(value) for name, value in headers.items()}
	self._update_session_activity(self.browser_session.id)
	await self.browser_session.set_extra_headers(normalized_headers)
	if normalized_headers:
		return f'Set {len(normalized_headers)} extra HTTP header(s)'
	return 'Cleared extra HTTP headers'


async def _set_timezone(self, timezone_id: str) -> str:
	"""Override the timezone for the focused page."""
	if not self.browser_session:
		return 'Error: No browser session active'
	if not self.browser_session.agent_focus_target_id:
		return 'Error: No browser page active'

	self._update_session_activity(self.browser_session.id)
	await self.browser_session._cdp_set_timezone(str(timezone_id))
	if timezone_id:
		return f'Set timezone override to {timezone_id}'
	return 'Cleared timezone override'


async def _set_locale(self, locale: str | None = None) -> str:
	"""Override the locale for the focused page."""
	if not self.browser_session:
		return 'Error: No browser session active'
	if not self.browser_session.agent_focus_target_id:
		return 'Error: No browser page active'

	self._update_session_activity(self.browser_session.id)
	normalized_locale = str(locale) if locale else None
	await self.browser_session._cdp_set_locale(normalized_locale)
	if normalized_locale:
		return f'Set locale override to {normalized_locale}'
	return 'Cleared locale override'


async def _set_user_agent(self, user_agent: str, accept_language: str | None = None, platform: str | None = None) -> str:
	"""Override the user agent for the focused page."""
	if not self.browser_session:
		return 'Error: No browser session active'
	if not self.browser_session.agent_focus_target_id:
		return 'Error: No browser page active'
	if not str(user_agent).strip():
		return 'Error [invalid_argument]: Provide a non-empty user_agent'

	self._update_session_activity(self.browser_session.id)
	await self.browser_session._cdp_set_user_agent(
		str(user_agent),
		accept_language=str(accept_language) if accept_language else None,
		platform=str(platform) if platform else None,
	)
	return f'Set user agent override to {user_agent}'


async def _emulate_media(
	self,
	media: str | None = None,
	color_scheme: str | None = None,
	reduced_motion: str | None = None,
	forced_colors: str | None = None,
) -> str:
	"""Emulate CSS media type and key user-preference media features."""
	if not self.browser_session:
		return 'Error: No browser session active'
	if not self.browser_session.agent_focus_target_id:
		return 'Error: No browser page active'

	normalized_media = str(media) if media is not None else None
	if normalized_media is not None and normalized_media not in _SUPPORTED_MEDIA:
		return _invalid_choice_error('media', normalized_media, _SUPPORTED_MEDIA)
	if color_scheme is not None and color_scheme not in _SUPPORTED_COLOR_SCHEMES:
		return _invalid_choice_error('color_scheme', color_scheme, _SUPPORTED_COLOR_SCHEMES)
	if reduced_motion is not None and reduced_motion not in _SUPPORTED_REDUCED_MOTION:
		return _invalid_choice_error('reduced_motion', reduced_motion, _SUPPORTED_REDUCED_MOTION)
	if forced_colors is not None and forced_colors not in _SUPPORTED_FORCED_COLORS:
		return _invalid_choice_error('forced_colors', forced_colors, _SUPPORTED_FORCED_COLORS)

	features: list[dict[str, str]] = []
	if color_scheme is not None:
		features.append({'name': 'prefers-color-scheme', 'value': color_scheme})
	if reduced_motion is not None:
		features.append({'name': 'prefers-reduced-motion', 'value': reduced_motion})
	if forced_colors is not None:
		features.append({'name': 'forced-colors', 'value': forced_colors})

	self._update_session_activity(self.browser_session.id)
	await self.browser_session._cdp_set_emulated_media(normalized_media, features)

	if normalized_media is None and not features:
		return 'Cleared emulated media'

	parts: list[str] = []
	if normalized_media is not None:
		parts.append(f'media={normalized_media or "default"}')
	if color_scheme is not None:
		parts.append(f'color_scheme={color_scheme}')
	if reduced_motion is not None:
		parts.append(f'reduced_motion={reduced_motion}')
	if forced_colors is not None:
		parts.append(f'forced_colors={forced_colors}')
	return f'Emulated media with {", ".join(parts)}'


__all__ = [
	'_emulate_media',
	'_grant_permissions',
	'_set_extra_headers',
	'_set_geolocation',
	'_set_locale',
	'_set_timezone',
	'_set_user_agent',
]
