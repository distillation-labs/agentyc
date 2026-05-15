from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from cdp_use.cdp.accessibility.commands import GetFullAXTreeReturns
from cdp_use.cdp.accessibility.types import AXPropertyName
from cdp_use.cdp.dom.commands import GetDocumentReturns
from cdp_use.cdp.domsnapshot.commands import CaptureSnapshotReturns
from cdp_use.cdp.target.types import TargetInfo


class MatchLevel(Enum):
	"""Element matching strictness levels for history replay."""

	EXACT = 1
	STABLE = 2
	XPATH = 3
	AX_NAME = 4
	ATTRIBUTE = 5


@dataclass
class CurrentPageTargets:
	page_session: TargetInfo
	iframe_sessions: list[TargetInfo]
	"""
	Iframe sessions are ALL the iframe sessions of all the pages (not just the current page).
	"""


@dataclass
class TargetAllTrees:
	snapshot: CaptureSnapshotReturns
	dom_tree: GetDocumentReturns
	ax_tree: GetFullAXTreeReturns
	device_pixel_ratio: float
	cdp_timing: dict[str, float]
	js_click_listener_backend_ids: set[int] | None = None


class NodeType(int, Enum):
	"""DOM node types based on the DOM specification."""

	ELEMENT_NODE = 1
	ATTRIBUTE_NODE = 2
	TEXT_NODE = 3
	CDATA_SECTION_NODE = 4
	ENTITY_REFERENCE_NODE = 5
	ENTITY_NODE = 6
	PROCESSING_INSTRUCTION_NODE = 7
	COMMENT_NODE = 8
	DOCUMENT_NODE = 9
	DOCUMENT_TYPE_NODE = 10
	DOCUMENT_FRAGMENT_NODE = 11
	NOTATION_NODE = 12


@dataclass(slots=True)
class DOMRect:
	x: float
	y: float
	width: float
	height: float

	def to_dict(self) -> dict[str, Any]:
		return {
			'x': self.x,
			'y': self.y,
			'width': self.width,
			'height': self.height,
		}

	def __json__(self) -> dict[str, Any]:
		return self.to_dict()


@dataclass(slots=True)
class PropagatingBounds:
	"""Track bounds that propagate from parent elements to filter children."""

	tag: str
	bounds: DOMRect
	node_id: int
	depth: int


@dataclass(slots=True)
class EnhancedAXProperty:
	"""Reduced accessibility property view used by DOM serialization."""

	name: AXPropertyName
	value: str | bool | None


@dataclass(slots=True)
class EnhancedAXNode:
	ax_node_id: str
	ignored: bool
	role: str | None
	name: str | None
	description: str | None
	properties: list[EnhancedAXProperty] | None
	child_ids: list[str] | None
	value: str | None = None


@dataclass(slots=True)
class EnhancedSnapshotNode:
	"""Snapshot data extracted from DOMSnapshot for enhanced functionality."""

	is_clickable: bool | None
	cursor_style: str | None
	bounds: DOMRect | None
	clientRects: DOMRect | None
	scrollRects: DOMRect | None
	computed_styles: dict[str, str] | None
	paint_order: int | None
	stacking_contexts: int | None


@dataclass(slots=True)
class MarkdownChunk:
	"""A structure-aware chunk of markdown content."""

	content: str
	chunk_index: int
	total_chunks: int
	char_offset_start: int
	char_offset_end: int
	overlap_prefix: str
	has_more: bool
