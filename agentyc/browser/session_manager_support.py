"""Shared helper functions for SessionManager."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, cast

from cdp_use.cdp.target import SessionID

from agentyc.browser.collaboration import extract_title_prefix, strip_title_prefix
from agentyc.browser.session_models import CDPSession, RuntimeOwnershipMetadata, Target, TargetOwnershipMetadata

if TYPE_CHECKING:
	from agentyc.browser.session_manager import SessionManager

INITIAL_TARGET_ATTACH_WAIT_TIMEOUT_S = 0.25
INITIAL_TARGET_ATTACH_POLL_INTERVAL_S = 0.05


def runtime_metadata_from_title(title: str) -> RuntimeOwnershipMetadata | None:
	prefix = extract_title_prefix(title)
	if not prefix:
		return None
	runtime_token = prefix.strip()[1:-1].strip()  # extract content between [ and ]
	return RuntimeOwnershipMetadata.create(
		session_id=runtime_token,
		runtime_id=runtime_token,
		runtime_label=runtime_token,
		runtime_role='detected',
	)


def apply_target_info(target: Target, target_info: Mapping[str, Any], *, current_runtime_id: str | None) -> None:
	raw_title = str(target_info.get('title', target.display_title or target.title or 'Unknown title'))
	target.display_title = raw_title
	target.title = strip_title_prefix(raw_title) or target.title
	target.url = str(target_info.get('url', target.url))
	ownership_metadata = runtime_metadata_from_title(raw_title)
	if ownership_metadata:
		target.ownership = TargetOwnershipMetadata.for_runtime(
			target_id=target.target_id,
			runtime=ownership_metadata,
			current_runtime_id=current_runtime_id,
			source='detected_runtime',
			title_prefix_applied=True,
		)


def set_target_ownership(
	target: Target | None,
	runtime: RuntimeOwnershipMetadata | TargetOwnershipMetadata,
	*,
	current_runtime_id: str | None,
	source: str | None = None,
) -> None:
	if target is None:
		return
	title_prefix_applied = bool(target.display_title and extract_title_prefix(target.display_title))
	if isinstance(runtime, TargetOwnershipMetadata):
		target.ownership = runtime.model_copy(
			update={
				'target_id': target.target_id,
				'title_prefix_applied': title_prefix_applied,
			}
		)
		return
	target.ownership = TargetOwnershipMetadata.for_runtime(
		target_id=target.target_id,
		runtime=runtime,
		current_runtime_id=current_runtime_id,
		source=cast(Any, source),
		title_prefix_applied=title_prefix_applied,
	)


def set_target_human_ownership(target: Target | None, *, display_label: str = 'Human') -> None:
	if target is None:
		return
	target.ownership = TargetOwnershipMetadata.human(
		target_id=target.target_id,
		display_label=display_label,
		title_prefix_applied=bool(target.display_title and extract_title_prefix(target.display_title)),
	)


def set_target_window_context(target: Target | None, *, window_id: int | None = None, window_bounds: Any = None) -> None:
	if target is None:
		return
	if window_id is not None:
		target.window_id = window_id
	if window_bounds is not None:
		target.window_bounds = window_bounds


async def initialize_existing_targets(manager: SessionManager) -> None:
	"""Discover and initialize all existing targets at startup."""
	cdp_client = manager.browser_session._cdp_client_root
	assert cdp_client is not None

	targets_result = await cdp_client.send.Target.getTargets()
	existing_targets = targets_result.get('targetInfos', [])

	manager.logger.debug(f'[SessionManager] Discovered {len(existing_targets)} existing targets')

	target_ids_to_wait_for = []
	for target in existing_targets:
		target_id = target['targetId']
		target_type = target.get('type', 'unknown')
		if target_type in {'page', 'tab', 'iframe', 'background_page', 'service_worker', 'worker'}:
			target_ids_to_wait_for.append(target_id)

	if not target_ids_to_wait_for:
		return

	def _ready_count() -> int:
		ready_count = 0
		for target_id in target_ids_to_wait_for:
			session = manager._get_session_for_target(target_id)
			if session:
				target = manager._targets.get(target_id)
				target_type = target.target_type if target else 'unknown'
				if target_type in ('page', 'tab'):
					if hasattr(session, '_lifecycle_events') and session._lifecycle_events is not None:
						ready_count += 1
				else:
					ready_count += 1
		return ready_count

	loop = asyncio.get_running_loop()
	deadline = loop.time() + INITIAL_TARGET_ATTACH_WAIT_TIMEOUT_S
	ready_count = 0
	while True:
		ready_count = _ready_count()
		if ready_count == len(target_ids_to_wait_for):
			return
		if loop.time() >= deadline:
			manager.logger.debug(
				f'[SessionManager] Initial target warmup ended with {ready_count}/{len(target_ids_to_wait_for)} sessions ready'
			)
			return
		await asyncio.sleep(INITIAL_TARGET_ATTACH_POLL_INTERVAL_S)


async def enable_page_monitoring(manager: SessionManager, cdp_session: CDPSession) -> None:
	"""Enable lifecycle events and network monitoring for a page target."""
	try:
		await cdp_session.cdp_client.send.Page.enable(session_id=cdp_session.session_id)
		await cdp_session.cdp_client.send.Page.setLifecycleEventsEnabled(
			params={'enabled': True}, session_id=cdp_session.session_id
		)
		await cdp_session.cdp_client.send.Network.enable(session_id=cdp_session.session_id)

		from collections import deque

		cdp_session._lifecycle_events = deque(maxlen=50)
		cdp_session._lifecycle_lock = asyncio.Lock()

		def on_lifecycle_event(event, session_id: SessionID | None = None) -> None:
			event_name = event.get('name', 'unknown')
			event_loader_id = event.get('loaderId', 'none')

			target_id_from_event = None
			if session_id:
				target_id_from_event = manager.get_target_id_from_session_id(session_id)

			if target_id_from_event == cdp_session.target_id:
				event_data = {
					'name': event_name,
					'loaderId': event_loader_id,
					'timestamp': asyncio.get_event_loop().time(),
				}
				try:
					cdp_session._lifecycle_events.append(event_data)
				except Exception as error:
					manager.logger.error(f'[SessionManager] Failed to store lifecycle event: {error}')

		cdp_session.cdp_client.register.Page.lifecycleEvent(on_lifecycle_event)

	except Exception as error:
		error_str = str(error)
		if '-32001' in error_str or 'Session with given id not found' in error_str:
			manager.logger.debug(
				f'[SessionManager] Target {cdp_session.target_id[:8]}... detached before monitoring could be enabled (normal for short-lived targets)'
			)
		else:
			manager.logger.warning(
				f'[SessionManager] Failed to enable monitoring for target {cdp_session.target_id[:8]}...: {error}'
			)
