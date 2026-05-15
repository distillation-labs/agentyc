from typing import Any

from pydantic import BaseModel, Field


class FileSystemState(BaseModel):
	"""Serializable state of the file system."""

	files: dict[str, dict[str, Any]] = Field(default_factory=dict)
	base_dir: str
	extracted_content_count: int = 0
