# Benchmark Results

Validated against the Contextro MCP codebase (76 files, 892 symbols, 1,620 chunks).

## Tool Output Sizes (16-tool workflow, median of 5 runs)

| Tool | Tokens |
|---|---|
| `search` | 116 |
| `explain` | 43 |
| `find_symbol` | 36 |
| `find_callers` | 6 |
| `status` | 20 |
| Total (16 calls) | 1,043 |

## Retrieval Quality (20 docstring queries, src codebase)

| Metric | Value |
|---|---|
| Hybrid MRR | 1.000 (perfect) |
| Hybrid recall@1 | 1.000 (perfect) |
| Hybrid recall@5 | 1.000 |
| Avg tokens/query | 152 |
| Avg latency | 4.4 ms |

## Indexing Performance (potion-code-16m)

| Codebase | Files | Chunks | Time |
|---|---|---|---|
| src (benchmark) | 76 | 1,620 | 0.45 s |
| Incremental (no changes) | — | — | 22 ms |
| File discovery (3,349 files) | — | — | 15 ms |

## Response Format (v5.0.0)

Search results use compact keys:

| Key | Meaning | Notes |
|---|---|---|
| `n` | symbol name | |
| `f` | file path | relative |
| `l` | start line | |
| `c` | code snippet | top result only |
| `t` | type | omitted when `function` |
| `lc` | line count | |
| `doc` | docstring | first sentence |

Implicit fields (omitted to save tokens):
- `confidence` — omitted when high (the default)
- `sandboxed` — omitted; presence of `sandbox_ref` implies sandboxing
- `lang` — omitted for Python (the default)
- `indexed` — omitted from status when true; presence of `codebase_path` implies indexed

## Search Quality (research-backed improvements)

| Improvement | Source | Gain |
|---|---|---|
| Relevance threshold 0.40 | SIGIR 2024 | -35% noise |
| Same-file diversity penalty | SaraCoder 2025 | -40% redundant results |
| Degenerate vector detection | Internal | MRR 0.975 → 1.0 |
| BM25 docstring-exact-match boost | Internal | MRR 0.975 → 1.0 |
| Contextual chunk enrichment | Anthropic Sep 2024 | -35-49% retrieval failures |
| Sufficiency signal | Google ICLR 2025 | -10% hallucinations |
