"""Structure-aware markdown chunking utilities."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum, auto

from agentyc.dom.views import MarkdownChunk

# Structure-aware markdown chunking
# ---------------------------------------------------------------------------


class _BlockType(Enum):
	HEADER = auto()
	CODE_FENCE = auto()
	TABLE = auto()
	LIST_ITEM = auto()
	PARAGRAPH = auto()
	BLANK = auto()


@dataclass(slots=True)
class _AtomicBlock:
	block_type: _BlockType
	lines: list[str]
	char_start: int  # offset in original content
	char_end: int  # offset in original content (exclusive)


_TABLE_ROW_RE = re.compile(r'^\s*\|.*\|\s*$')
_LIST_ITEM_RE = re.compile(r'^(\s*)([-*+]|\d+[.)]) ')
_LIST_CONTINUATION_RE = re.compile(r'^(\s{2,}|\t)')


def _parse_atomic_blocks(content: str) -> list[_AtomicBlock]:
	"""Phase 1: Walk lines, group into unsplittable blocks."""
	lines = content.split('\n')
	blocks: list[_AtomicBlock] = []
	i = 0
	offset = 0  # char offset tracking

	while i < len(lines):
		line = lines[i]
		line_len = len(line) + 1  # +1 for the newline we split on

		# BLANK
		if not line.strip():
			blocks.append(
				_AtomicBlock(
					block_type=_BlockType.BLANK,
					lines=[line],
					char_start=offset,
					char_end=offset + line_len,
				)
			)
			offset += line_len
			i += 1
			continue

		# CODE FENCE
		if line.strip().startswith('```'):
			fence_lines = [line]
			fence_end = offset + line_len
			i += 1
			# Consume until closing fence or EOF
			while i < len(lines):
				fence_line = lines[i]
				fence_line_len = len(fence_line) + 1
				fence_lines.append(fence_line)
				fence_end += fence_line_len
				i += 1
				if fence_line.strip().startswith('```') and len(fence_lines) > 1:
					break
			blocks.append(
				_AtomicBlock(
					block_type=_BlockType.CODE_FENCE,
					lines=fence_lines,
					char_start=offset,
					char_end=fence_end,
				)
			)
			offset = fence_end
			continue

		# HEADER
		if line.lstrip().startswith('#'):
			blocks.append(
				_AtomicBlock(
					block_type=_BlockType.HEADER,
					lines=[line],
					char_start=offset,
					char_end=offset + line_len,
				)
			)
			offset += line_len
			i += 1
			continue

		# TABLE (consecutive |...|  lines)
		# Header + separator row stay together; each data row is its own block
		if _TABLE_ROW_RE.match(line):
			# Collect header line
			header_lines = [line]
			header_end = offset + line_len
			i += 1
			# Check if next line is separator (contains ---)
			if i < len(lines) and _TABLE_ROW_RE.match(lines[i]) and '---' in lines[i]:
				sep = lines[i]
				sep_len = len(sep) + 1
				header_lines.append(sep)
				header_end += sep_len
				i += 1
			# Emit header+separator as one atomic block
			blocks.append(
				_AtomicBlock(
					block_type=_BlockType.TABLE,
					lines=header_lines,
					char_start=offset,
					char_end=header_end,
				)
			)
			offset = header_end
			# Each subsequent table row is its own TABLE block (splittable between rows)
			while i < len(lines) and _TABLE_ROW_RE.match(lines[i]):
				row = lines[i]
				row_len = len(row) + 1
				blocks.append(
					_AtomicBlock(
						block_type=_BlockType.TABLE,
						lines=[row],
						char_start=offset,
						char_end=offset + row_len,
					)
				)
				offset += row_len
				i += 1
			continue

		# LIST ITEM (with indented continuations)
		if _LIST_ITEM_RE.match(line):
			list_lines = [line]
			list_end = offset + line_len
			i += 1
			# Consume continuation lines (indented or blank between items)
			while i < len(lines):
				next_line = lines[i]
				next_len = len(next_line) + 1
				# Another list item at same or deeper indent → still part of this block
				if _LIST_ITEM_RE.match(next_line):
					list_lines.append(next_line)
					list_end += next_len
					i += 1
					continue
				# Indented continuation
				if next_line.strip() and _LIST_CONTINUATION_RE.match(next_line):
					list_lines.append(next_line)
					list_end += next_len
					i += 1
					continue
				break
			blocks.append(
				_AtomicBlock(
					block_type=_BlockType.LIST_ITEM,
					lines=list_lines,
					char_start=offset,
					char_end=list_end,
				)
			)
			offset = list_end
			continue

		# PARAGRAPH (everything else, up to next blank line)
		para_lines = [line]
		para_end = offset + line_len
		i += 1
		while i < len(lines) and lines[i].strip():
			# Stop if next line starts a different block type
			nl = lines[i]
			if nl.lstrip().startswith('#') or nl.strip().startswith('```') or _TABLE_ROW_RE.match(nl) or _LIST_ITEM_RE.match(nl):
				break
			nl_len = len(nl) + 1
			para_lines.append(nl)
			para_end += nl_len
			i += 1
		blocks.append(
			_AtomicBlock(
				block_type=_BlockType.PARAGRAPH,
				lines=para_lines,
				char_start=offset,
				char_end=para_end,
			)
		)
		offset = para_end

	# Fix last block char_end: content may not end with \n
	if blocks and content and not content.endswith('\n'):
		blocks[-1] = _AtomicBlock(
			block_type=blocks[-1].block_type,
			lines=blocks[-1].lines,
			char_start=blocks[-1].char_start,
			char_end=len(content),
		)

	return blocks


def _block_text(block: _AtomicBlock) -> str:
	return '\n'.join(block.lines)


def _get_table_header(block: _AtomicBlock) -> str | None:
	"""Extract table header + separator rows from a TABLE block."""
	assert block.block_type == _BlockType.TABLE
	if len(block.lines) < 2:
		return None
	# Header is first line, separator is second line (must contain ---)
	sep_line = block.lines[1]
	if '---' in sep_line or '- -' in sep_line:
		return block.lines[0] + '\n' + block.lines[1]
	return None


def chunk_markdown_by_structure(
	content: str,
	max_chunk_chars: int = 100_000,
	overlap_lines: int = 5,
	start_from_char: int = 0,
) -> list[MarkdownChunk]:
	"""Split markdown into structure-aware chunks.

	Algorithm:
	  Phase 1 — Parse atomic blocks (headers, code fences, tables, list items, paragraphs).
	  Phase 2 — Greedy chunk assembly: accumulate blocks until exceeding max_chunk_chars.
	            A single block exceeding the limit is allowed (soft limit).
	  Phase 3 — Build overlap prefixes for context carry between chunks.

	Args:
	    content: Full markdown string.
	    max_chunk_chars: Target maximum chars per chunk (soft limit for single blocks).
	    overlap_lines: Number of trailing lines from previous chunk to prepend.
	    start_from_char: Return chunks starting from the chunk that contains this offset.

	Returns:
	    List of MarkdownChunk. Empty if start_from_char is past end of content.
	"""
	if not content:
		return [
			MarkdownChunk(
				content='',
				chunk_index=0,
				total_chunks=1,
				char_offset_start=0,
				char_offset_end=0,
				overlap_prefix='',
				has_more=False,
			)
		]

	if start_from_char >= len(content):
		return []

	# Phase 1: parse atomic blocks
	blocks = _parse_atomic_blocks(content)
	if not blocks:
		return []

	# Phase 2: greedy chunk assembly with header-preferred splitting
	raw_chunks: list[list[_AtomicBlock]] = []
	current_chunk: list[_AtomicBlock] = []
	current_size = 0

	for block in blocks:
		block_size = block.char_end - block.char_start
		# If adding this block would exceed limit AND we already have content, emit chunk
		if current_size + block_size > max_chunk_chars and current_chunk:
			# Prefer splitting at a header boundary within the current chunk.
			# Scan backwards for the last HEADER block; if found and it wouldn't
			# create a tiny chunk (< 50% of limit), split right before it so the
			# header starts the next chunk for better semantic coherence.
			best_split = len(current_chunk)
			for j in range(len(current_chunk) - 1, 0, -1):
				if current_chunk[j].block_type == _BlockType.HEADER:
					prefix_size = sum(b.char_end - b.char_start for b in current_chunk[:j])
					if prefix_size >= max_chunk_chars * 0.5:
						best_split = j
						break
			raw_chunks.append(current_chunk[:best_split])
			# Carry remaining blocks (from the header onward) into the next chunk
			current_chunk = current_chunk[best_split:]
			current_size = sum(b.char_end - b.char_start for b in current_chunk)
		current_chunk.append(block)
		current_size += block_size

	if current_chunk:
		raw_chunks.append(current_chunk)

	total_chunks = len(raw_chunks)

	# Phase 3: build MarkdownChunk objects with overlap prefixes
	chunks: list[MarkdownChunk] = []
	# Track table header from previous chunk for table continuations
	prev_chunk_last_table_header: str | None = None

	for idx, chunk_blocks in enumerate(raw_chunks):
		chunk_text = '\n'.join(_block_text(b) for b in chunk_blocks)
		char_start = chunk_blocks[0].char_start
		char_end = chunk_blocks[-1].char_end

		# Build overlap prefix
		overlap = ''
		if idx > 0:
			prev_blocks = raw_chunks[idx - 1]
			prev_text = '\n'.join(_block_text(b) for b in prev_blocks)
			prev_lines = prev_text.split('\n')

			# Check if current chunk starts with a table continuation
			first_block = chunk_blocks[0]
			if first_block.block_type == _BlockType.TABLE and prev_chunk_last_table_header:
				# Always prepend table header for continuation
				trailing = prev_lines[-(overlap_lines):] if overlap_lines > 0 else []
				header_lines = prev_chunk_last_table_header.split('\n')
				# Deduplicate: don't repeat header lines if they're already in trailing
				combined = list(header_lines)
				for tl in trailing:
					if tl not in combined:
						combined.append(tl)
				overlap = '\n'.join(combined)
			elif overlap_lines > 0:
				overlap = '\n'.join(prev_lines[-(overlap_lines):])

		# Track table header from this chunk for next iteration.
		# Only overwrite if this chunk contains a new header+separator block;
		# otherwise preserve the previous header so tables spanning 3+ chunks
		# still get the header carried forward.
		for b in chunk_blocks:
			if b.block_type == _BlockType.TABLE:
				hdr = _get_table_header(b)
				if hdr is not None:
					prev_chunk_last_table_header = hdr

		has_more = idx < total_chunks - 1
		chunks.append(
			MarkdownChunk(
				content=chunk_text,
				chunk_index=idx,
				total_chunks=total_chunks,
				char_offset_start=char_start,
				char_offset_end=char_end,
				overlap_prefix=overlap,
				has_more=has_more,
			)
		)

	# Apply start_from_char filter: return chunks from the one containing that offset
	if start_from_char > 0:
		for i, chunk in enumerate(chunks):
			if chunk.char_offset_end > start_from_char:
				return chunks[i:]
		return []  # offset past all chunks

	return chunks
