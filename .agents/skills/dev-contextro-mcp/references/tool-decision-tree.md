# Tool Decision Tree

Full routing table for Contextro MCP tools. Use this when the main SKILL.md table isn't enough.

## By Task Type

### "Find code that does X"
→ `search("X")` — hybrid semantic+keyword+graph
→ If exact name known: `find_symbol("ExactName")`
→ If structural pattern: `code(operation="pattern_search", pattern="...", language="python")`

### "Understand how X works"
→ `explain("X")` for specific non-utility symbols
→ `search("how does X work")` for broad concepts
→ `find_callees("X")` to see what X calls
→ Only `readFile` if you need the full implementation body after explain told you the file

### "Who uses X / who calls X"
→ `find_callers("X")` — exact callers from call graph
→ `search("X", mode="bm25")` — all text references including comments/docs

### "What breaks if I change X"
→ `impact("X")` — ALWAYS before any rename/delete/signature change
→ `find_callers("X")` — for the full caller list beyond the 20-item cap

### "Project structure / orientation"
→ `overview()` — language breakdown, file count, top dirs, symbol counts
→ `architecture()` — layers, entry points, hub symbols
→ `analyze(path="src/module")` — code quality for a specific area

### "Git history"
→ `commit_search("what changed")` — semantic search over commits
→ `commit_history(limit=10)` — browse recent commits chronologically

### "Store / retrieve context"
→ `remember(content, memory_type="decision")` — persist decisions
→ `recall("topic")` — retrieve by meaning
→ `knowledge(command="add", name="docs", value="/path")` — index external docs
→ `knowledge(command="search", query="topic")` — search indexed docs

### "AST operations"
→ `code(operation="get_document_symbols", file_path="...")` — list all symbols in a file
→ `code(operation="pattern_search", pattern="...", language="...")` — structural search
→ `code(operation="pattern_rewrite", ..., dry_run=True)` — safe structural rewrite
→ `code(operation="lookup_symbols", symbols="A,B,C")` — batch symbol lookup

### "Session recovery"
→ `session_snapshot()` — ALWAYS call first after context compaction
→ `recall("what I was working on")` — retrieve stored context

## Search Mode Selection

| Query type | Mode | Example |
|---|---|---|
| Conceptual ("how does auth work") | `hybrid` (default) | `search("authentication flow")` |
| Exact identifier | `bm25` | `search("TokenBudget", mode="bm25")` |
| Semantic only | `vector` | `search("retry logic", mode="vector")` |

## When NOT to Use Contextro

- Single-file edit where you already know the exact file and line → `readFile` directly
- Reading `package.json`, `pyproject.toml`, config files → `readFile` directly
- Writing new code from scratch → no Contextro needed
- Answering general programming questions → no Contextro needed
