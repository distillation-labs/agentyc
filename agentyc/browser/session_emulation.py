"""Permission and environment emulation helpers for BrowserSession CDP sessions."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from cdp_use.cdp import browser, emulation

from agentyc.browser.session_lookup import get_or_create_cdp_session

if TYPE_CHECKING:
	from agentyc.browser.session import BrowserSession


async def _cdp_grant_permissions(session: BrowserSession, permissions: list[str], origin: str | None = None) -> None:
	params = browser.GrantPermissionsParameters(
		permissions=[cast(browser.PermissionType, permission) for permission in permissions]
	)
	if origin:
		params['origin'] = origin
	await session.cdp_client.send.Browser.grantPermissions(params=params)


async def _cdp_set_geolocation(session: BrowserSession, latitude: float, longitude: float, accuracy: float = 100) -> None:
	cdp_session = await get_or_create_cdp_session(session, target_id=None, focus=False)
	await cdp_session.cdp_client.send.Emulation.setGeolocationOverride(
		params={'latitude': latitude, 'longitude': longitude, 'accuracy': accuracy},
		session_id=cdp_session.session_id,
	)


async def _cdp_clear_geolocation(session: BrowserSession) -> None:
	cdp_session = await get_or_create_cdp_session(session, target_id=None, focus=False)
	await cdp_session.cdp_client.send.Emulation.clearGeolocationOverride(session_id=cdp_session.session_id)


async def _cdp_set_locale(session: BrowserSession, locale: str | None = None) -> None:
	cdp_session = await get_or_create_cdp_session(session, target_id=None, focus=False)
	params = emulation.SetLocaleOverrideParameters()
	if locale:
		params['locale'] = locale
	await cdp_session.cdp_client.send.Emulation.setLocaleOverride(params=params, session_id=cdp_session.session_id)


async def _cdp_set_timezone(session: BrowserSession, timezone_id: str) -> None:
	cdp_session = await get_or_create_cdp_session(session, target_id=None, focus=False)
	params = emulation.SetTimezoneOverrideParameters(timezoneId=timezone_id)
	await cdp_session.cdp_client.send.Emulation.setTimezoneOverride(params=params, session_id=cdp_session.session_id)


async def _cdp_set_user_agent(
	session: BrowserSession,
	user_agent: str,
	accept_language: str | None = None,
	platform: str | None = None,
) -> None:
	cdp_session = await get_or_create_cdp_session(session, target_id=None, focus=False)
	params = emulation.SetUserAgentOverrideParameters(userAgent=user_agent)
	if accept_language:
		params['acceptLanguage'] = accept_language
	if platform:
		params['platform'] = platform
	await cdp_session.cdp_client.send.Emulation.setUserAgentOverride(params=params, session_id=cdp_session.session_id)


async def _cdp_set_emulated_media(
	session: BrowserSession,
	media: str | None = None,
	features: list[dict[str, str]] | None = None,
) -> None:
	cdp_session = await get_or_create_cdp_session(session, target_id=None, focus=False)
	params = emulation.SetEmulatedMediaParameters()
	params['media'] = media if media is not None else ''
	params['features'] = [emulation.MediaFeature(name=feature['name'], value=feature['value']) for feature in features or []]
	await cdp_session.cdp_client.send.Emulation.setEmulatedMedia(params=params, session_id=cdp_session.session_id)


__all__ = [
	'_cdp_clear_geolocation',
	'_cdp_grant_permissions',
	'_cdp_set_emulated_media',
	'_cdp_set_geolocation',
	'_cdp_set_locale',
	'_cdp_set_timezone',
	'_cdp_set_user_agent',
]
