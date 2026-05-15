"""Shared action models used by deterministic runtimes (MCP/tools)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, model_validator

from agentyc.tools.registry.views import ActionModel


class ActionResult(BaseModel):
	"""Result of executing a single action."""

	# Completion flags
	is_done: bool | None = False
	success: bool | None = None

	# Error/result payloads
	error: str | None = None
	extracted_content: str | None = None
	include_extracted_content_only_once: bool = False
	long_term_memory: str | None = None

	# Attachments and metadata
	attachments: list[str] | None = None
	images: list[dict[str, Any]] | None = None
	metadata: dict[str, Any] | None = None

	# Backward compatibility field
	include_in_memory: bool = False

	# Optional judgement payload retained for compatibility
	judgement: dict[str, Any] | None = None

	@model_validator(mode='after')
	def validate_success_requires_done(self):
		"""Ensure success=True is only used for done actions."""
		if self.success is True and self.is_done is not True:
			raise ValueError(
				'success=True can only be set when is_done=True. '
				'For regular actions that succeed, leave success as None. '
				'Use success=False only for actions that fail.'
			)
		return self


__all__ = ['ActionModel', 'ActionResult']
