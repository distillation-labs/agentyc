//! Markdown extraction and structure-aware chunking.
//!
//! Ports `markdown_extractor.py` and `markdown_chunking.py`.

use anyhow::Result;
use regex::Regex;
use serde::{Deserialize, Serialize};

// ---------------------------------------------------------------------------
// Markdown chunk data type
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MarkdownChunk {
    pub content: String,
    pub chunk_index: usize,
    pub total_chunks: usize,
    pub char_offset_start: usize,
    pub char_offset_end: usize,
    pub overlap_prefix: String,
    pub has_more: bool,
}

// ---------------------------------------------------------------------------
// HTML → Markdown extraction
// ---------------------------------------------------------------------------

/// Convert raw HTML to clean markdown using htmd.
pub fn html_to_markdown(html: &str) -> Result<String> {
    let md = htmd::convert(html)?;
    Ok(postprocess_markdown(&md))
}

fn postprocess_markdown(content: &str) -> String {
    // Remove URL-encoded fragments
    let url_enc = Regex::new(r"%[0-9A-Fa-f]{2}").unwrap();
    let content = url_enc.replace_all(content, "");

    // Compress excessive blank lines (4+ → 3)
    let blank_re = Regex::new(r"\n{4,}").unwrap();
    let content = blank_re.replace_all(&content, "\n\n\n");

    // Drop lines that are just long JSON blobs
    let lines: Vec<&str> = content
        .lines()
        .filter(|l| {
            let stripped = l.trim();
            if stripped.is_empty() {
                return true;
            }
            if (stripped.starts_with('{') || stripped.starts_with('[')) && stripped.len() > 100 {
                return false;
            }
            true
        })
        .collect();

    lines.join("\n").trim().to_string()
}

// ---------------------------------------------------------------------------
// Structure-aware chunking (port of markdown_chunking.py)
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum BlockType {
    Header,
    CodeFence,
    Table,
    ListItem,
    Paragraph,
    Blank,
}

#[derive(Debug, Clone)]
struct AtomicBlock {
    block_type: BlockType,
    lines: Vec<String>,
    char_start: usize,
    char_end: usize,
}

fn block_text(b: &AtomicBlock) -> String {
    b.lines.join("\n")
}

fn parse_atomic_blocks(content: &str) -> Vec<AtomicBlock> {
    let lines: Vec<&str> = content.split('\n').collect();
    let mut blocks = Vec::new();
    let mut i = 0usize;
    let mut offset = 0usize;

    let table_row_re = Regex::new(r"^\s*\|.*\|\s*$").unwrap();
    let list_item_re = Regex::new(r"^(\s*)([-*+]|\d+[.)]) ").unwrap();
    let list_cont_re = Regex::new(r"^(\s{2,}|\t)").unwrap();

    while i < lines.len() {
        let line = lines[i];
        let line_len = line.len() + 1; // +1 for the '\n' we split on

        if line.trim().is_empty() {
            blocks.push(AtomicBlock {
                block_type: BlockType::Blank,
                lines: vec![line.to_string()],
                char_start: offset,
                char_end: offset + line_len,
            });
            offset += line_len;
            i += 1;
            continue;
        }

        if line.trim().starts_with("```") {
            let fence_start = offset;
            let mut fence_lines = vec![line.to_string()];
            let mut fence_end = offset + line_len;
            i += 1;
            while i < lines.len() {
                let fl = lines[i];
                let fl_len = fl.len() + 1;
                fence_lines.push(fl.to_string());
                fence_end += fl_len;
                i += 1;
                if fl.trim().starts_with("```") && fence_lines.len() > 1 {
                    break;
                }
            }
            blocks.push(AtomicBlock {
                block_type: BlockType::CodeFence,
                lines: fence_lines,
                char_start: fence_start,
                char_end: fence_end,
            });
            offset = fence_end;
            continue;
        }

        if line.trim_start().starts_with('#') {
            blocks.push(AtomicBlock {
                block_type: BlockType::Header,
                lines: vec![line.to_string()],
                char_start: offset,
                char_end: offset + line_len,
            });
            offset += line_len;
            i += 1;
            continue;
        }

        if table_row_re.is_match(line) {
            let hdr_start = offset;
            let mut hdr_lines = vec![line.to_string()];
            let mut hdr_end = offset + line_len;
            i += 1;
            if i < lines.len() && table_row_re.is_match(lines[i]) && lines[i].contains("---") {
                let sl = lines[i];
                hdr_lines.push(sl.to_string());
                hdr_end += sl.len() + 1;
                i += 1;
            }
            blocks.push(AtomicBlock {
                block_type: BlockType::Table,
                lines: hdr_lines,
                char_start: hdr_start,
                char_end: hdr_end,
            });
            offset = hdr_end;
            while i < lines.len() && table_row_re.is_match(lines[i]) {
                let row = lines[i];
                let row_len = row.len() + 1;
                blocks.push(AtomicBlock {
                    block_type: BlockType::Table,
                    lines: vec![row.to_string()],
                    char_start: offset,
                    char_end: offset + row_len,
                });
                offset += row_len;
                i += 1;
            }
            continue;
        }

        if list_item_re.is_match(line) {
            let ls = offset;
            let mut ll = vec![line.to_string()];
            let mut le = offset + line_len;
            i += 1;
            while i < lines.len() {
                let nl = lines[i];
                let nl_len = nl.len() + 1;
                if list_item_re.is_match(nl) || (nl.trim().starts_with(|c: char| !c.is_whitespace()) && list_cont_re.is_match(nl)) {
                    ll.push(nl.to_string());
                    le += nl_len;
                    i += 1;
                } else {
                    break;
                }
            }
            blocks.push(AtomicBlock {
                block_type: BlockType::ListItem,
                lines: ll,
                char_start: ls,
                char_end: le,
            });
            offset = le;
            continue;
        }

        // Paragraph
        let ps = offset;
        let mut pl = vec![line.to_string()];
        let mut pe = offset + line_len;
        i += 1;
        while i < lines.len() && !lines[i].trim().is_empty() {
            let nl = lines[i];
            if nl.trim_start().starts_with('#')
                || nl.trim().starts_with("```")
                || table_row_re.is_match(nl)
                || list_item_re.is_match(nl)
            {
                break;
            }
            pl.push(nl.to_string());
            pe += nl.len() + 1;
            i += 1;
        }
        blocks.push(AtomicBlock {
            block_type: BlockType::Paragraph,
            lines: pl,
            char_start: ps,
            char_end: pe,
        });
        offset = pe;
    }

    if let Some(last) = blocks.last_mut()
        && !content.is_empty() && !content.ends_with('\n')
    {
        last.char_end = content.len();
    }

    blocks
}

/// Split markdown into structure-aware chunks with optional overlap.
pub fn chunk_markdown(
    content: &str,
    max_chunk_chars: usize,
    overlap_lines: usize,
    start_from_char: usize,
) -> Vec<MarkdownChunk> {
    if content.is_empty() {
        return vec![MarkdownChunk {
            content: String::new(),
            chunk_index: 0,
            total_chunks: 1,
            char_offset_start: 0,
            char_offset_end: 0,
            overlap_prefix: String::new(),
            has_more: false,
        }];
    }
    if start_from_char >= content.len() {
        return vec![];
    }

    let blocks = parse_atomic_blocks(content);
    if blocks.is_empty() {
        return vec![];
    }

    // Greedy assembly
    let mut raw_chunks: Vec<Vec<usize>> = Vec::new(); // indices into blocks
    let mut current: Vec<usize> = Vec::new();
    let mut current_size = 0usize;

    for (idx, block) in blocks.iter().enumerate() {
        let bsize = block.char_end.saturating_sub(block.char_start);
        if current_size + bsize > max_chunk_chars && !current.is_empty() {
            // Try splitting at a header boundary
            let mut best_split = current.len();
            for j in (1..current.len()).rev() {
                if blocks[current[j]].block_type == BlockType::Header {
                    let prefix_size: usize = current[..j]
                        .iter()
                        .map(|&k| blocks[k].char_end.saturating_sub(blocks[k].char_start))
                        .sum();
                    if prefix_size >= max_chunk_chars / 2 {
                        best_split = j;
                        break;
                    }
                }
            }
            raw_chunks.push(current[..best_split].to_vec());
            current = current[best_split..].to_vec();
            current_size = current.iter().map(|&k| blocks[k].char_end.saturating_sub(blocks[k].char_start)).sum();
        }
        current.push(idx);
        current_size += bsize;
    }
    if !current.is_empty() {
        raw_chunks.push(current);
    }

    let total_chunks = raw_chunks.len();
    let mut chunks = Vec::with_capacity(total_chunks);
    let mut prev_table_header: Option<String> = None;

    for (idx, chunk_indices) in raw_chunks.iter().enumerate() {
        let chunk_blocks: Vec<&AtomicBlock> = chunk_indices.iter().map(|&k| &blocks[k]).collect();
        let chunk_text = chunk_blocks.iter().map(|b| block_text(b)).collect::<Vec<_>>().join("\n");
        let char_start = chunk_blocks.first().map(|b| b.char_start).unwrap_or(0);
        let char_end = chunk_blocks.last().map(|b| b.char_end).unwrap_or(0);

        let overlap = if idx > 0 {
            let prev_indices = &raw_chunks[idx - 1];
            let prev_text = prev_indices.iter().map(|&k| block_text(&blocks[k])).collect::<Vec<_>>().join("\n");
            let prev_lines: Vec<&str> = prev_text.lines().collect();
            let first_block = chunk_blocks.first().unwrap();
            if first_block.block_type == BlockType::Table {
                if let Some(ref hdr) = prev_table_header {
                    let trailing: Vec<&str> = if overlap_lines > 0 {
                        prev_lines[prev_lines.len().saturating_sub(overlap_lines)..].to_vec()
                    } else {
                        vec![]
                    };
                    let mut combined: Vec<&str> = hdr.lines().collect();
                    for l in trailing {
                        if !combined.contains(&l) {
                            combined.push(l);
                        }
                    }
                    combined.join("\n")
                } else {
                    prev_lines[prev_lines.len().saturating_sub(overlap_lines)..].join("\n")
                }
            } else if overlap_lines > 0 {
                prev_lines[prev_lines.len().saturating_sub(overlap_lines)..].join("\n")
            } else {
                String::new()
            }
        } else {
            String::new()
        };

        // Track table header for next chunk
        for b in &chunk_blocks {
            if b.block_type == BlockType::Table && b.lines.len() >= 2 && b.lines[1].contains("---") {
                prev_table_header = Some(format!("{}\n{}", b.lines[0], b.lines[1]));
            }
        }

        chunks.push(MarkdownChunk {
            content: chunk_text,
            chunk_index: idx,
            total_chunks,
            char_offset_start: char_start,
            char_offset_end: char_end,
            overlap_prefix: overlap,
            has_more: idx < total_chunks - 1,
        });
    }

    if start_from_char > 0 {
        chunks.retain(|c| c.char_offset_end > start_from_char);
        if chunks.is_empty() {
            return vec![];
        }
    }

    chunks
}
