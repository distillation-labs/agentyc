"""Shared browser runtime models for session and collaboration state."""

from __future__ import annotations

import hashlib
from typing import Any

from cdp_use import CDPClient
from cdp_use.cdp.target import SessionID, TargetID
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr


def _short_runtime_id(runtime_id: str) -> str:
	return runtime_id[-4:] if len(runtime_id) >= 4 else runtime_id


def _runtime_color(runtime_id: str) -> str:
	"""Derive a deterministic accent color from runtime_id."""
	digest = hashlib.sha1(runtime_id.encode('utf-8')).hexdigest()
	hue = int(digest[:6], 16) % 360
	return f'hsl({hue} 72% 58%)'


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
	overlay_label: str
	accent_color: str

	@classmethod
	def create(
		cls,
		*,
		session_id: str,
		runtime_id: str | None = None,
		runtime_label: str | None = None,
		runtime_role: str = 'primary',
		parent_runtime_id: str | None = None,
	) -> 'RuntimeOwnershipMetadata':
		resolved_runtime_id = runtime_id or session_id
		short_id = _short_runtime_id(resolved_runtime_id)
		resolved_label = runtime_label or f'Runtime {short_id}'
		return cls(
			runtime_id=resolved_runtime_id,
			session_id=session_id,
			runtime_label=resolved_label,
			runtime_role=runtime_role,
			parent_runtime_id=parent_runtime_id,
			title_prefix=f'[agtyc:{short_id}] ',
			overlay_label=resolved_label,
			accent_color=_runtime_color(resolved_runtime_id),
		)


class TargetOwnershipMetadata(BaseModel):
	"""Ownership metadata stored alongside a tracked browser target."""

	model_config = ConfigDict(arbitrary_types_allowed=True, extra='forbid', revalidate_instances='never')

	target_id: TargetID
	runtime: RuntimeOwnershipMetadata
	title_prefix_applied: bool = False
	overlay_enabled: bool = False
	overlay_visible: bool = False
	marker_version: int = 1


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
