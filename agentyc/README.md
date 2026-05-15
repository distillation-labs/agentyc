# agentyc Package Notes

This package implements the repository's MCP-first browser automation runtime.

## Contributor Direction

- Prefer the repo-level contributor guidance in `AGENTS.md`.
- Prefer the release and gating guidance in `docs/release-gate.md` for publish-time expectations.
- Treat `agentyc/mcp`, `agentyc/browser`, `agentyc/dom`, `agentyc/tools`, `agentyc/filesystem`, and `agentyc/llm` as the primary maintained surfaces.
- Reuse existing browser/session primitives instead of introducing parallel automation abstractions.
- Keep MCP tool contracts deterministic and inspectable through explicit state, HTML, screenshots, and clear errors.

## Package Layout

- `agentyc/mcp`: server and protocol-facing runtime entrypoints.
- `agentyc/browser`: browser sessions, CDP integration, tabs, and watchdogs.
- `agentyc/dom`: DOM capture, serialization, and extraction helpers.
- `agentyc/tools`: deterministic tool orchestration on top of browser primitives.
- `agentyc/filesystem`: local file access and document helpers.
- `agentyc/llm`: provider integrations and structured extraction support.

For end-to-end setup and public-facing product docs, use the repository root documentation rather than this package-local note.
