# Changelog

## Unreleased

### Added

- Added explicit release-gate evaluation to `scripts/benchmark_mcp_runtime.py`, including threshold checks and non-zero exit behavior without requiring GitHub issue creation.
- Added `scripts/check_release_guards.py` and a publish-time file-size guard for oversized Python modules, plus a watchlist artifact for modules above the preferred modularity target.
- Added `docs/release-gate.md` documenting benchmark thresholds, dogfood environment variables, and CI publish gating.

### Changed

- Documented the current shared-browser runtime contract across README and public docs, including runtime labels, ownership metadata, window mode, window bounds, and focus policy behavior.
- Extended `scripts/dogfood.sh` so release-gate thresholds can be driven from environment variables in CI or local dogfooding.
- Updated `.github/workflows/workflow.yml` so publishing is blocked on benchmark and modularity/file-size release gates, including collaboration pass-rate enforcement, an evidence-backed auto-payload regression floor, and a stricter file-size ceiling.
- Replaced the stale internal `agentyc/README.md` with contributor-facing guidance that points to the repository’s MCP-first workflow and release-gate docs.
