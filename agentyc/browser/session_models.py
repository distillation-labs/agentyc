"""Shared browser runtime models for session and collaboration state."""

from __future__ import annotations

from typing import Any, Literal

from cdp_use import CDPClient
from cdp_use.cdp.target import SessionID, TargetID
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr


def _short_runtime_id(runtime_id: str) -> str:
	return runtime_id[-4:] if len(runtime_id) >= 4 else runtime_id


class BrowserWindowBounds(BaseModel):
	"""Serializable browser window bounds."""

	model_config = ConfigDict(extra='forbid', revalidate_instances='never')

	left: int | None = None
	top: int | None = None
	width: int | None = None
	height: int | None = None
	window_state: str | None = Field(default=None, alias='windowState')


class BrowserWindowContext(BaseModel):
	"""Window metadata for a target when exposed by CDP Browser domain."""

	model_config = ConfigDict(extra='forbid', revalidate_instances='never')

	window_id: int
	bounds: BrowserWindowBounds | None = None


class RuntimeOwnershipMetadata(BaseModel):
	"""Deterministic metadata describing the runtime that owns a target."""

	model_config = ConfigDict(extra='forbid', revalidate_instances='never')

	runtime_id: str
	session_id: str
	runtime_label: str
	runtime_role: str = 'primary'
	parent_runtime_id: str | None = None
	title_prefix: str

	@classmethod
	def create(
		cls,
		*,
		session_id: str,
		runtime_id: str | None = None,
		runtime_label: str | None = None,
		runtime_role: str = 'primary',
		parent_runtime_id: str | None = None,
	) -> RuntimeOwnershipMetadata:
		resolved_runtime_id = runtime_id or session_id
		short_id = _short_runtime_id(resolved_runtime_id)
		resolved_label = runtime_label or short_id
		return cls(
			runtime_id=resolved_runtime_id,
			session_id=session_id,
			runtime_label=resolved_label,
			runtime_role=runtime_role,
			parent_runtime_id=parent_runtime_id,
			title_prefix=f'[{resolved_label}] ',
		)


class TargetOwnershipMetadata(BaseModel):
	"""Ownership metadata stored alongside a tracked browser target."""

	model_config = ConfigDict(arbitrary_types_allowed=True, extra='forbid', revalidate_instances='never')

	target_id: TargetID
	owner_kind: Literal['agent', 'human', 'runtime']
	source: Literal['current_runtime', 'detected_runtime', 'explicit_runtime', 'shared_browser_human', 'manual_human']
	display_label: str
	runtime: RuntimeOwnershipMetadata | None = None
	title_prefix_applied: bool = False
	marker_version: int = 1

	@classmethod
	def for_runtime(
		cls,
		*,
		target_id: TargetID,
		runtime: RuntimeOwnershipMetadata,
		current_runtime_id: str | None = None,
		source: Literal['current_runtime', 'detected_runtime', 'explicit_runtime'] | None = None,
		title_prefix_applied: bool = False,
	) -> TargetOwnershipMetadata:
		is_current_runtime = current_runtime_id is not None and runtime.runtime_id == current_runtime_id
		resolved_source = source or ('current_runtime' if is_current_runtime else 'detected_runtime')
		return cls(
			target_id=target_id,
			owner_kind='agent' if is_current_runtime else 'runtime',
			source=resolved_source,
			display_label=runtime.runtime_label,
			runtime=runtime,
			title_prefix_applied=title_prefix_applied,
		)

	@classmethod
	def human(
		cls,
		*,
		target_id: TargetID,
		display_label: str = 'Human',
		source: Literal['shared_browser_human', 'manual_human'] = 'shared_browser_human',
		title_prefix_applied: bool = False,
	) -> TargetOwnershipMetadata:
		return cls(
			target_id=target_id,
			owner_kind='human',
			source=source,
			display_label=display_label,
			runtime=None,
			title_prefix_applied=title_prefix_applied,
		)


class Target(BaseModel):
	"""Browser target (page, iframe, worker) - the actual entity being controlled."""

	model_config = ConfigDict(arbitrary_types_allowed=True, revalidate_instances='never')

	target_id: TargetID
	target_type: str
	url: str = 'about:blank'
	title: str = 'Unknown title'
	display_title: str | None = None
	ownership: TargetOwnershipMetadata | None = None
	window_id: int | None = None
	window_bounds: BrowserWindowBounds | None = None


class CDPSession(BaseModel):
	"""CDP communication channel to a target."""

	model_config = ConfigDict(arbitrary_types_allowed=True, revalidate_instances='never')

	cdp_client: CDPClient
	target_id: TargetID
	session_id: SessionID

	_lifecycle_events: Any = PrivateAttr(default=None)
	_lifecycle_lock: Any = PrivateAttr(default=None)
