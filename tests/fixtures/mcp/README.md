# Phase 0 MCP fixtures

`tool_catalog.json` is the local capability input. `offline_workflow.json` is the deterministic no-browser workflow used by the scaffolding.

The fixtures intentionally contain no network URLs, credentials, page bodies from external sites, browser IDs, or CDP endpoints. A live target probe must be selected explicitly with `--mode target` or `--mode headed`; a managed lane must be selected with `--mode managed` and explicit prerequisites.
