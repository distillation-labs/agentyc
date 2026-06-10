"""Helpers for browser window bounds."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class BrowserWindowBounds(BaseModel):
	model_config = ConfigDict(extra='forbid', revalidate_instances='never')
	left: int | None = None
	top: int | None = None
	width: int | None = None
	height: int | None = None
	window_state: str | None = Field(default=None, alias='windowState')


def normalize_window_bounds(bounds: dict[str, Any] | BrowserWindowBounds | None) -> BrowserWindowBounds | None:
	if bounds is None:
		return None
	if isinstance(bounds, BrowserWindowBounds):
		return bounds
	return BrowserWindowBounds.model_validate(bounds)
