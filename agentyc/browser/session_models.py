"""Browser session runtime models."""

from __future__ import annotations

from typing import Any

from cdp_use import CDPClient
from cdp_use.cdp.target import SessionID, TargetID
from pydantic import BaseModel, ConfigDict, PrivateAttr


class Target(BaseModel):
	"""Browser target (page, iframe, worker) - the actual entity being controlled."""

	model_config = ConfigDict(arbitrary_types_allowed=True, revalidate_instances='never')

	target_id: TargetID
	target_type: str
	url: str = 'about:blank'
	title: str = 'Unknown title'
	window_id: int | None = None


class CDPSession(BaseModel):
	"""CDP communication channel to a target."""

	model_config = ConfigDict(arbitrary_types_allowed=True, revalidate_instances='never')

	cdp_client: CDPClient
	target_id: TargetID
	session_id: SessionID

	_lifecycle_events: Any = PrivateAttr(default=None)
	_lifecycle_lock: Any = PrivateAttr(default=None)
