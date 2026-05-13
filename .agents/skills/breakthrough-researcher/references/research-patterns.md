# Breakthrough Research Patterns

This reference captures the mechanisms that recur across leading AI research and coding-agent teams.

## Shared Patterns

### OpenAI

- Keep top-level instructions short and map-like.
- Treat the repository as the system of record.
- Make architecture and quality legible through docs, linters, and structured artifacts.
- Favor harnesses and feedback loops over manual heroics.

### Anthropic

- Use the smallest high-signal context that still solves the task.
- Use progressive disclosure rather than dumping full state by default.
- Separate initialization and execution for long-running work.
- Preserve continuity with explicit artifacts, not hidden chat memory.

### Google DeepMind

- Define a clean baseline before changing the system.
- Use ablations to identify what actually matters.
- Separate signal from confounders.

### Cursor

- Start with the local codebase and keep retrieval tightly coupled to the edit surface.
- Favor fast explore-compare-apply loops over large up-front plans when the problem is still being localized.
- Keep research grounded in concrete files, symbols, and repo constraints so recommendations stay actionable.

### Windsurf

- Treat long tasks as paired reasoning and execution flows that need visible state.
- Preserve enough working memory to resume without rebuilding the full trajectory every turn.
- Keep IDE context, repo state, and in-flight hypotheses aligned so execution does not drift from the research goal.

### Mistral

- Efficient models and clean decomposition often beat larger undifferentiated reasoning passes.
- Route subtasks by difficulty and cost instead of assuming one heavyweight pass should do everything.
- Prefer modular context slices that can be recombined for research, evaluation, and implementation.

### Devin And Cognition

- Evaluate agents in realistic environments.
- Prefer autonomous feedback and evaluator loops when deterministic checks are insufficient.
- External notes and environment state improve long-horizon continuity.
- Critiquing a candidate solution is usually easier than generating it.

### NVIDIA

- Retrieval systems must be benchmarked end-to-end.
- Chunking, retrieval, reranking, output shaping, and latency trade off together.
- Use the real corpus and real query shapes when choosing defaults.

### DeepSeek

- Long-horizon systems need explicit checkpointing and resumability.
- Stable prefixes and repeated structure improve cache reuse.
- Keep a trajectory that can be replayed or inspected after failure.
- Preserve state selectively in tool-calling scenarios instead of replaying everything.

## What This Means For Contextro

- Research should start from the current benchmark and architecture, not from hypothetical rewrites.
- Good proposals include a transfer mechanism and a non-transfer rationale.
- High-ROI directions usually improve retrieval quality, token efficiency, compaction recovery, or workflow control rather than adding arbitrary complexity.
- Best-in-class comparison should include coding-agent product patterns from Cursor and Windsurf, plus efficiency-oriented model-system patterns from Mistral, when they transfer to Contextro.
