# Dev Contextro MCP Eval Rubric

Use this rubric to judge whether the skill matches the local `skills-guide.md` guidance and the
repo's skill-evaluation conventions.

## Source Principles

- Frontmatter must say what the skill does and when to use it.
- `SKILL.md` should stay concise; detailed guidance belongs in `references/`.
- Test three things separately: triggering, functional execution, and performance
  comparison against the no-skill baseline.
- Prefer problem-first routing. Users ask for outcomes; the skill should pick the right
  Contextro tool sequence.

## Pass when the skill:

- triggers on obvious Contextro requests and paraphrased variants
- does not over-trigger on direct single-file reads or from-scratch coding requests
- uses `find_symbol` for exact symbols and `search` for concepts
- uses `impact` before rename, delete, or signature-change guidance
- uses `session_snapshot` first after compaction
- uses `retrieve` when `sandbox_ref` is present
- uses AST operations with `dry_run=True` before applying structural rewrites
- correctly interprets compact result keys and response shapes
- reduces file reads and shell-history work versus the no-skill baseline

## Fail when the skill:

- re-indexes the same repo repeatedly without reason
- searches immediately after `index()` without waiting for readiness
- uses serial `find_symbol` calls when batch lookup is better
- ignores `sandbox_ref`
- reaches for shell `git log` when Contextro history tools answer the question
- inflates token usage with unnecessary file reads before narrowing scope

## Recommended Thresholds

- Relevant-query trigger rate: at least 90 percent.
- Unrelated-query non-trigger rate: 100 percent.
- `impact()` before refactor guidance: 100 percent.
- `status()` after `index()`: at least 95 percent.
- `retrieve()` when `sandbox_ref` appears: at least 95 percent.
- Performance improvement vs baseline: clear reduction in reads, shell search, and token-heavy workflows.
