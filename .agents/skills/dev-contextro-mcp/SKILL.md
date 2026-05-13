---
name: dev-contextro-mcp
description: >
  Use Contextro for codebase discovery, semantic/code-graph search, safe refactors,
  AST search and rewrite, git history, memory recovery, and cross-repo search.
  Trigger when the user asks to index or search a codebase, find a symbol or usage,
  explain how code works, trace callers/callees, assess what breaks before a change,
  inspect recent commits, recover after compaction, search across repos, or retrieve
  sandboxed results. Do not use for general programming questions, writing new code
  from scratch, or reading a single known file when direct file inspection is cheaper.
when_to_use: >
  Prefer Contextro before file-by-file reads for unfamiliar codebases and multi-file
  questions. Especially useful for: who calls, what calls, what breaks, find usages,
  explain this class, git history, pattern search, add repo, remember or recall,
  compact or archive, retrieve sandbox results.
metadata:
  version: "0.1.0"
  mcp-server: contextro
  category: mcp-enhancement
  tags: [contextro, mcp, code-search, code-graph, ast, git, memory, cross-repo]
license: Proprietary
---

# Contextro MCP

Use Contextro as the default discovery layer for unfamiliar or multi-file code work.
It is best for semantic search, symbol lookup, call graphs, impact analysis, commit
history, memory recovery, cross-repo search, and AST-aware search or rewrite.

Pure Rust binary. No Python. No interpreter. Cold start <25ms.

## Use It For

- Finding code by concept, symbol, caller, callee, or exact identifier.
- Understanding a symbol or project before editing.
- Checking refactor blast radius before rename, delete, or signature changes.
- Investigating regressions with code search plus commit history.
- Recovering after compaction or searching archived session context.
- Searching across multiple repos or retrieving sandboxed large results.

## Do Not Use It For

- General programming questions.
- Writing new code from scratch when no repository discovery is needed.
- Reading one known config file or one known small file directly.
- Any task where a direct single-file read is clearly cheaper than indexing or search.

## First Use In A Repo

```text
1. index("/absolute/path/to/project")
2. status()
3. Wait for indexed: true before search/find_symbol/explain/impact
```

Index persists. Do not re-index before every call. Use `status()` to check readiness.

## Routing

| Task | Use | Notes |
|---|---|---|
| Find an exact symbol definition | `find_symbol(name="ExactName")` | `name` param; add `exact=false` for fuzzy |
| Find code by concept | `search(query="authentication middleware")` | Default discovery tool |
| Find exact identifier/string references | `search(query="CTX_STORAGE_DIR", mode="bm25")` | Prefer BM25 for exact names |
| Find callers | `find_callers(symbol_name="Symbol")` | Returns `{callers: [...]}` |
| Find callees | `find_callees(symbol_name="Symbol")` | Returns `{callees: [...]}` |
| Understand one symbol | `explain(symbol_name="Symbol")` | Start here before file reads |
| Orient in a new codebase | `overview()` then `architecture()` | High-signal orientation path |
| Check refactor impact | `impact(symbol_name="Symbol")` | Mandatory before rename/delete/signature changes |
| Batch lookup several symbols | `code(operation="lookup_symbols", symbols="A,B,C")` | Avoid serial `find_symbol` calls |
| List symbols in a file | `code(operation="get_document_symbols", file_path="...")` | Better than reading for structure |
| Structural search | `code(operation="pattern_search", pattern="fn $F($$$)", language="rust")` | Use for AST-shaped queries |
| Structural rewrite | `code(operation="pattern_rewrite", ..., dry_run=true)` | Preview first, then apply |
| Plan an edit | `code(operation="edit_plan", goal="...", symbol_name="...")` | Returns ordered edit steps |
| Search commit history | `commit_search(query="...")` or `commit_history(limit=N)` | Prefer over shell `git log` |
| Add/search another repo | `repo_add(path="...", name="...")`, `repo_status()`, `search(...)` | Use for cross-repo flows |
| Store a durable decision | `remember(content="...", memory_type="decision")` | Persistent memory |
| Archive pre-compaction context | `compact(content="...")` | Not `remember()` |
| Recover after compaction | `session_snapshot()` then `recall(query="...", memory_type="archive")` | Search archive |
| Expand sandboxed large output | `retrieve(ref_id="sx_...")` | Use when `sandbox_ref` is present |

## Parameter Reference

Key parameter names that differ from intuition:

| Tool | Param | Type |
|---|---|---|
| `find_symbol` | `name` | string (required) |
| `find_callers` | `symbol_name` | string (required) |
| `find_callees` | `symbol_name` | string (required) |
| `explain` | `symbol_name` | string (required) |
| `impact` | `symbol_name` | string (required); optional `max_depth` int |
| `retrieve` | `ref_id` | string (required, e.g. `"sx_abc123"`) |
| `forget` | `memory_id` or `tags` or `memory_type` | at least one required |

## Response Format

Search results use compact keys to minimise token usage:

| Key | Meaning |
|---|---|
| `n` | symbol name |
| `f` | file path (relative) |
| `l` | start line |
| `c` | code snippet (top result only) |
| `t` | type (omitted when `function`) |
| `lc` | line count |
| `doc` | docstring (first sentence) |

`confidence` is omitted when high (the default). `sandboxed` is omitted — presence
of `sandbox_ref` implies sandboxing.

## Mandatory Workflows

### Safe Refactor

```text
1. impact(symbol_name="Symbol")
2. explain(symbol_name="Symbol")
3. find_callers(symbol_name="Symbol") if impact is broad
4. Make the code change
5. search(query="OldName", mode="bm25") to verify cleanup
```

Never recommend rename, delete, or signature changes without `impact()` first.

### New Codebase Orientation

```text
1. overview()
2. architecture()
3. explain(symbol_name="hub-symbol")
```

This is the default orientation path. Do not start with broad file reads.

### Bug Investigation

```text
1. search(query="error message or symptom")
2. explain(symbol_name="relevant symbol") or find_callers/find_callees
3. commit_search(query="recent changes related to symptom")
```

### AST Rewrite

```text
1. code(operation="pattern_search", pattern="...", language="...")
2. code(operation="pattern_rewrite", ..., dry_run=true)
3. Review preview
4. code(operation="pattern_rewrite", ..., dry_run=false)
```

### Recovery After Compaction

```text
1. session_snapshot()
2. recall(query="topic", memory_type="archive")
3. recall(query="topic")
```

## Output Rules

- Read `content[0].text` first. Treat it as the primary output.
- Use `structuredContent` only as a supplement.
- If `confidence: low` is present, narrow the query or switch to `find_symbol`.
- If the response includes `sandbox_ref`, call `retrieve(ref_id="sx_...")` before claiming you have the full result set.
- Keep default search limits unless the user explicitly wants exhaustive output.
- If the user mentions a tight context budget, pass `context_budget` to `search()`.

## Anti-Patterns

- Do not call `search()` immediately after `index()`; wait for `status()` to show `indexed: true`.
- Do not re-index the same repo repeatedly.
- Do not use three separate `find_symbol` calls when `lookup_symbols` fits.
- Do not ignore `sandbox_ref` in responses.
- Do not use `remember()` for pre-compaction archival; use `compact()`.
- Do not use `overview()` to find one symbol; use `search()` or `find_symbol()`.
- Do not replace exact-history questions with shell `git log` when `commit_search()` or `commit_history()` is available.
- Do not pass `name=` to `find_callers/find_callees/explain/impact`; use `symbol_name=`.

## Escalation Rule

Prefer Contextro first for discovery. Once it has narrowed the scope to a specific file
or symbol, direct file reads are acceptable if the full implementation body is needed.

## Benchmarks

Token efficiency (measured on 8,498-file production codebase, v0.1.0):

| Tool | Approx tokens |
|---|---|
| `search` (5 results) | ~300 |
| `explain` | ~60 |
| `find_symbol` | ~200 |
| `find_callers` | ~17 |
| `status` | ~30 |

Performance:

| Metric | Value |
|---|---|
| Search latency | ~50µs avg |
| Throughput | 21,000+ ops/sec |
| Graph exact lookup | 29ns |
| Graph callers/callees | 30–42ns |
| Cold start | <25ms |
| Memory idle | 1.4MB |
| Binary size | 11MB (arm64) |

Indexing (8,498 files / 11,534 symbols):

| Metric | Value |
|---|---|
| Full index | ~310ms |
| Re-index (no changes) | <1s |

## References

- Full routing guide: `references/tool-decision-tree.md`
- Token and benchmark data: `references/benchmark-results.md`
- Eval rubric: `references/eval-rubric.md`
