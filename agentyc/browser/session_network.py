"""Network interception and condition helpers for BrowserSession."""

from __future__ import annotations

import base64
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, cast

from cdp_use.cdp.fetch import AuthRequiredEvent, RequestPausedEvent
from cdp_use.cdp.target import SessionID, TargetID

from agentyc.browser.session_leaf_helpers import _sanitize_replay_headers
from agentyc.browser.session_models import CDPSession
from agentyc.utils import create_task_with_error_handling

if TYPE_CHECKING:
	from agentyc.browser.session import BrowserSession


def _normalize_method(method: str | None) -> str | None:
	return method.upper() if method else None


def _normalize_resource_type(resource_type: str | None) -> str | None:
	return resource_type.lower() if resource_type else None


@dataclass(slots=True)
class NetworkMockRule:
	"""Serializable network interception rule bound to a single target."""

	mock_id: str
	target_id: TargetID | None
	url_substring: str | None = None
	url_regex: str | None = None
	method: str | None = None
	resource_type: str | None = None
	action: str = 'fulfill'
	status: int = 200
	headers: dict[str, str] = field(default_factory=dict)
	body: str = ''
	error_reason: str = 'Failed'
	created_at: float = field(default_factory=time.time)
	match_count: int = 0
	_response_body_b64: str = field(init=False, repr=False)
	_compiled_url_regex: re.Pattern[str] | None = field(init=False, repr=False, default=None)

	def __post_init__(self) -> None:
		self.method = _normalize_method(self.method)
		self.resource_type = _normalize_resource_type(self.resource_type)
		self._response_body_b64 = base64.b64encode(self.body.encode('utf-8')).decode('ascii') if self.body else ''
		if self.url_regex:
			self._compiled_url_regex = re.compile(self.url_regex)

	def matches(self, *, target_id: TargetID | None, url: str, method: str | None, resource_type: str | None) -> bool:
		if self.target_id is not None and target_id != self.target_id:
			return False
		if self.url_substring and self.url_substring not in url:
			return False
		if self._compiled_url_regex and self._compiled_url_regex.search(url) is None:
			return False
		if self.method and _normalize_method(method) != self.method:
			return False
		if self.resource_type and _normalize_resource_type(resource_type) != self.resource_type:
			return False
		return True

	def as_public_dict(self) -> dict[str, Any]:
		payload: dict[str, Any] = {
			'mock_id': self.mock_id,
			'action': self.action,
			'match_count': self.match_count,
			'created_at': self.created_at,
		}
		if self.target_id:
			payload['target_tab_id'] = str(self.target_id)[-4:]
		if self.url_substring:
			payload['url_substring'] = self.url_substring
		if self.url_regex:
			payload['url_regex'] = self.url_regex
		if self.method:
			payload['method'] = self.method
		if self.resource_type:
			payload['resource_type'] = self.resource_type
		if self.action == 'fulfill':
			payload['status'] = self.status
			if self.headers:
				payload['headers'] = dict(self.headers)
			if self.body:
				payload['body_preview'] = self.body[:200]
				if len(self.body) > 200:
					payload['body_truncated'] = True
		else:
			payload['error_reason'] = self.error_reason
		return payload


@dataclass(slots=True)
class NetworkConditions:
	"""Network conditions bound to a single target."""

	target_id: TargetID
	offline: bool = False
	latency_ms: float = 0.0
	download_kbps: float | None = None
	upload_kbps: float | None = None
	connection_type: str | None = None
	updated_at: float = field(default_factory=time.time)

	def as_cdp_params(self) -> dict[str, Any]:
		params: dict[str, Any] = {
			'offline': self.offline,
			'latency': max(float(self.latency_ms), 0.0),
			'downloadThroughput': -1,
			'uploadThroughput': -1,
		}
		if self.download_kbps is not None:
			params['downloadThroughput'] = max(int((self.download_kbps * 1024) / 8), 0)
		if self.upload_kbps is not None:
			params['uploadThroughput'] = max(int((self.upload_kbps * 1024) / 8), 0)
		if self.connection_type:
			params['connectionType'] = self.connection_type
		return params

	def as_public_dict(self) -> dict[str, Any]:
		payload: dict[str, Any] = {
			'target_tab_id': str(self.target_id)[-4:],
			'offline': self.offline,
			'latency_ms': self.latency_ms,
			'updated_at': self.updated_at,
		}
		if self.download_kbps is not None:
			payload['download_kbps'] = self.download_kbps
		if self.upload_kbps is not None:
			payload['upload_kbps'] = self.upload_kbps
		if self.connection_type:
			payload['connection_type'] = self.connection_type
		return payload


def has_proxy_auth(session: BrowserSession) -> bool:
	proxy_cfg = session.browser_profile.proxy
	return bool(proxy_cfg and proxy_cfg.username and proxy_cfg.password)


def fetch_interception_enabled(session: BrowserSession) -> bool:
	return has_proxy_auth(session) or bool(session._network_mock_rules)


def active_network_conditions(session: BrowserSession) -> list[NetworkConditions]:
	return list(session._network_conditions_by_target.values())


def list_network_mocks(session: BrowserSession) -> list[dict[str, Any]]:
	return [rule.as_public_dict() for rule in session._network_mock_rules.values()]


def _should_handle_target(session: BrowserSession, target_id: TargetID | None) -> bool:
	if target_id is None:
		return False
	if not session.is_shared_browser_runtime:
		return True
	return session.is_target_owned_by_current_runtime(target_id)


async def _continue_request(session: BrowserSession, *, request_id: str, session_id: SessionID | None) -> None:
	assert session._cdp_client_root is not None
	await session._cdp_client_root.send.Fetch.continueRequest(params={'requestId': request_id}, session_id=session_id)


async def _fail_request(
	session: BrowserSession,
	*,
	request_id: str,
	session_id: SessionID | None,
	error_reason: str,
) -> None:
	assert session._cdp_client_root is not None
	await session._cdp_client_root.send.Fetch.failRequest(
		params=cast(Any, {'requestId': request_id, 'errorReason': error_reason}),
		session_id=session_id,
	)


async def _fulfill_request(
	session: BrowserSession,
	*,
	request_id: str,
	session_id: SessionID | None,
	rule: NetworkMockRule,
) -> None:
	assert session._cdp_client_root is not None
	headers_payload = cast(Any, [{'name': key, 'value': value} for key, value in rule.headers.items()])
	await session._cdp_client_root.send.Fetch.fulfillRequest(
		params=cast(
			Any,
			{
				'requestId': request_id,
				'responseCode': rule.status,
				'responseHeaders': headers_payload,
				'body': rule._response_body_b64,
			},
		),
		session_id=session_id,
	)


async def _handle_auth_required_async(
	session: BrowserSession,
	*,
	event: AuthRequiredEvent,
	session_id: SessionID | None,
) -> None:
	assert session._cdp_client_root is not None
	proxy_cfg = session.browser_profile.proxy
	username = proxy_cfg.username if proxy_cfg else None
	password = proxy_cfg.password if proxy_cfg else None
	request_id = event.get('requestId') or event.get('request_id')
	if not request_id:
		return
	challenge = event.get('authChallenge') or event.get('auth_challenge') or {}
	source = str(challenge.get('source') or '').lower()
	if source == 'proxy' and username and password:
		await session._cdp_client_root.send.Fetch.continueWithAuth(
			params={
				'requestId': request_id,
				'authChallengeResponse': {
					'response': 'ProvideCredentials',
					'username': username,
					'password': password,
				},
			},
			session_id=session_id,
		)
		return
	await session._cdp_client_root.send.Fetch.continueWithAuth(
		params={'requestId': request_id, 'authChallengeResponse': {'response': 'Default'}},
		session_id=session_id,
	)


async def _handle_request_paused_async(
	session: BrowserSession,
	*,
	event: RequestPausedEvent,
	session_id: SessionID | None,
) -> None:
	request_id = event.get('requestId') or event.get('request_id')
	if not request_id:
		return
	target_id = (
		session.session_manager.get_target_id_from_session_id(session_id) if session.session_manager and session_id else None
	)
	if not _should_handle_target(session, target_id):
		await _continue_request(session, request_id=request_id, session_id=session_id)
		return
	request = event.get('request') or {}
	request_url = str(request.get('url') or '')
	request_method = str(request.get('method') or 'GET')
	resource_type = event.get('resourceType') or event.get('resource_type')
	for rule in session._network_mock_rules.values():
		if not rule.matches(
			target_id=target_id,
			url=request_url,
			method=request_method,
			resource_type=resource_type,
		):
			continue
		rule.match_count += 1
		if rule.action == 'abort':
			await _fail_request(
				session,
				request_id=request_id,
				session_id=session_id,
				error_reason=rule.error_reason,
			)
			return
		await _fulfill_request(session, request_id=request_id, session_id=session_id, rule=rule)
		return
	await _continue_request(session, request_id=request_id, session_id=session_id)


async def _ensure_fetch_handlers_registered(session: BrowserSession) -> None:
	if session._fetch_handlers_registered or session._cdp_client_root is None:
		return

	def on_auth_required(event: AuthRequiredEvent, session_id: SessionID | None = None) -> None:
		create_task_with_error_handling(
			_handle_auth_required_async(session, event=event, session_id=session_id),
			name='fetch_auth_required',
			logger_instance=session.logger,
			suppress_exceptions=True,
		)

	def on_request_paused(event: RequestPausedEvent, session_id: SessionID | None = None) -> None:
		create_task_with_error_handling(
			_handle_request_paused_async(session, event=event, session_id=session_id),
			name='fetch_request_paused',
			logger_instance=session.logger,
			suppress_exceptions=True,
		)

	session._cdp_client_root.register.Fetch.authRequired(on_auth_required)
	session._cdp_client_root.register.Fetch.requestPaused(on_request_paused)
	session._fetch_handlers_registered = True


async def _configure_root_fetch(session: BrowserSession, *, enabled: bool) -> None:
	if session._cdp_client_root is None or not has_proxy_auth(session):
		return
	fetch_sender = session._cdp_client_root.send.Fetch
	try:
		if enabled:
			await fetch_sender.enable(params={'handleAuthRequests': True, 'patterns': [{'urlPattern': '*'}]})
		else:
			disable = getattr(fetch_sender, 'disable', None)
			if callable(disable):
				await cast(Any, disable)()
	except Exception as exc:
		session.logger.debug(f'[NetworkControl] Fetch {"enable" if enabled else "disable"} failed for root session: {exc}')


def _iter_fetch_sessions(session: BrowserSession) -> list[CDPSession]:
	if session.session_manager is None:
		return []
	return list(session.session_manager.get_all_sessions().values())


async def _configure_fetch_for_session(session: BrowserSession, cdp_session: CDPSession, *, enabled: bool) -> None:
	try:
		if enabled:
			await cdp_session.cdp_client.send.Fetch.enable(
				params={'handleAuthRequests': has_proxy_auth(session), 'patterns': [{'urlPattern': '*'}]},
				session_id=cdp_session.session_id,
			)
		else:
			await cdp_session.cdp_client.send.Fetch.disable(session_id=cdp_session.session_id)
	except Exception as exc:
		session.logger.debug(
			f'[NetworkControl] Fetch {"enable" if enabled else "disable"} failed for session {cdp_session.session_id[:8]}...: {exc}'
		)


async def configure_fetch_interception(session: BrowserSession) -> None:
	"""Register Fetch handlers once and configure root/session interception."""
	if session._cdp_client_root is None:
		return
	await _ensure_fetch_handlers_registered(session)
	enabled = fetch_interception_enabled(session)
	cdp_sessions = _iter_fetch_sessions(session)
	root_fetch_enabled = enabled and has_proxy_auth(session) and not cdp_sessions
	await _configure_root_fetch(session, enabled=root_fetch_enabled)
	for cdp_session in cdp_sessions:
		await _configure_fetch_for_session(session, cdp_session, enabled=enabled)


async def configure_attached_network_session(session: BrowserSession, cdp_session: CDPSession) -> None:
	"""Apply active interception and network conditions to a newly attached target."""
	if fetch_interception_enabled(session):
		await _ensure_fetch_handlers_registered(session)
		await _configure_root_fetch(session, enabled=False)
		await _configure_fetch_for_session(session, cdp_session, enabled=True)
	conditions = session._network_conditions_by_target.get(str(cdp_session.target_id))
	if conditions is not None:
		await _apply_network_conditions_to_session(session, cdp_session, conditions)


async def add_network_mock(
	session: BrowserSession,
	*,
	url_substring: str | None = None,
	url_regex: str | None = None,
	method: str | None = None,
	resource_type: str | None = None,
	action: str = 'fulfill',
	status: int = 200,
	headers: dict[str, Any] | None = None,
	body: str = '',
	error_reason: str = 'Failed',
) -> dict[str, Any]:
	if not url_substring and not url_regex:
		raise ValueError('Provide url_substring or url_regex for a network mock.')
	if session.agent_focus_target_id is None:
		raise RuntimeError('No active target available for network mock.')
	rule = NetworkMockRule(
		mock_id=f'mock_{uuid.uuid4().hex[:8]}',
		target_id=session.agent_focus_target_id,
		url_substring=url_substring,
		url_regex=url_regex,
		method=method,
		resource_type=resource_type,
		action=action,
		status=status,
		headers={str(key): str(value) for key, value in (headers or {}).items()},
		body=body,
		error_reason=error_reason,
	)
	session._network_mock_rules[rule.mock_id] = rule
	await configure_fetch_interception(session)
	return rule.as_public_dict()


async def remove_network_mock(session: BrowserSession, mock_id: str | None = None) -> dict[str, Any]:
	removed = 0
	if mock_id is None:
		removed = len(session._network_mock_rules)
		session._network_mock_rules.clear()
	else:
		removed = 1 if session._network_mock_rules.pop(mock_id, None) is not None else 0
	await configure_fetch_interception(session)
	return {
		'removed': removed,
		'remaining': len(session._network_mock_rules),
	}


async def _apply_network_conditions_to_session(
	session: BrowserSession,
	cdp_session: CDPSession,
	conditions: NetworkConditions,
) -> None:
	try:
		await cdp_session.cdp_client.send.Network.enable(session_id=cdp_session.session_id)
		await cdp_session.cdp_client.send.Network.emulateNetworkConditions(
			params=cast(Any, conditions.as_cdp_params()),
			session_id=cdp_session.session_id,
		)
	except Exception as exc:
		session.logger.debug(f'[NetworkControl] Failed to apply network conditions to {cdp_session.target_id[:8]}...: {exc}')


async def set_network_conditions(
	session: BrowserSession,
	*,
	offline: bool = False,
	latency_ms: float = 0.0,
	download_kbps: float | None = None,
	upload_kbps: float | None = None,
	connection_type: str | None = None,
	reset: bool = False,
) -> dict[str, Any]:
	if session.agent_focus_target_id is None:
		raise RuntimeError('No active target available for network conditions.')
	target_id = session.agent_focus_target_id
	if reset:
		conditions = NetworkConditions(target_id=target_id)
		session._network_conditions_by_target.pop(str(target_id), None)
	else:
		conditions = NetworkConditions(
			target_id=target_id,
			offline=offline,
			latency_ms=latency_ms,
			download_kbps=download_kbps,
			upload_kbps=upload_kbps,
			connection_type=connection_type,
		)
		session._network_conditions_by_target[str(target_id)] = conditions
	cdp_session = await session.get_or_create_cdp_session(target_id=target_id, focus=False)
	await _apply_network_conditions_to_session(session, cdp_session, conditions)
	result = conditions.as_public_dict()
	result['reset'] = reset
	return result


def get_network_conditions(session: BrowserSession) -> list[dict[str, Any]]:
	return [conditions.as_public_dict() for conditions in session._network_conditions_by_target.values()]


def sanitize_replay_headers(headers: dict[str, Any] | None) -> dict[str, str]:
	return _sanitize_replay_headers(headers)
