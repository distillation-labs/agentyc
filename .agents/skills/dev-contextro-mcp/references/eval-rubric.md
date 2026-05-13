# Dev Contextro MCP Eval Rubric

Use this rubric to judge whether the skill matches Anthropic's skill guidance and the
local `skills-guide.pdf`.

## Source Principles

- Frontmatter must say what the skill does and when to use it.
- `SKILL.md` should stay concise; detailed guidance belongs in `references/`.
- Test three things separately: triggering, functional execution, and performance
  comparison against the no-skill baseline.
- Prefer problem-first routing. Users ask for outcomes; the skill should pick the right
  Contextro tool sequence.

## Pass Criteria

### Triggering

- Triggers on obvious Contextro requests.
- Triggers on paraphrased versions of those requests.
- Does not trigger on unrelated coding or knowledge tasks.
- Does not over-trigger on direct single-file reads or from-scratch coding requests.

### Functional Routing

- Uses `find_symbol` for exact symbols.
- Uses `search` for concepts and `bm25` for exact identifiers.
- Uses `impact` before rename, delete, or signature-change guidance.
- Uses `session_snapshot` first after compaction.
- Uses `retrieve` when `sandbox_ref` is present (regardless of whether `sandboxed` field appears).
- Uses AST operations with `dry_run=True` before applying structural rewrites.

### Response Format Interpretation

- Reads compact keys: `n`=name, `f`=file, `l`=line, `c`=code, `t`=type, `lc`=line_count, `doc`=docstring.
- Treats absent `confidence` as high (the default).
- Treats absent `sandboxed` field as sandboxed when `sandbox_ref` is present.
- Treats absent `indexed` field as true when `codebase_path` is present in status.
- Reads flat single-result `find_symbol` response (no `symbols` wrapper).
- Reads `{callers: [...]}` and `{callees: [...]}` directly from find_callers/find_callees.

### Anti-Patterns

- No repeated re-indexing.
- No immediate search after `index()` before `status()` confirms readiness.
- No serial `find_symbol` calls when `lookup_symbols` is better.
- No `remember()` in place of `compact()` for pre-compaction archival.
- No shell `git log` when Contextro history tools answer the question.

### Performance Comparison

- Fewer tool calls than the no-skill baseline for orientation, safe refactor checks, and
  bug investigation.
- Fewer file reads than the no-skill baseline.
- Lower token usage by preferring compact Contextro outputs.

## Recommended Thresholds

- Relevant-query trigger rate: at least 90 percent.
- Unrelated-query non-trigger rate: 100 percent.
- `impact()` before refactor guidance: 100 percent.
- `status()` after `index()`: at least 95 percent.
- `retrieve()` when `sandbox_ref` appears: at least 95 percent.
- Performance improvement vs baseline: clear reduction in reads, shell search, and token-heavy workflows.
