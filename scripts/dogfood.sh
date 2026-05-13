#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"
cd "$SCRIPT_DIR/.." || exit 1

ARGS=(--preset dogfood)

if [[ "${DOGFOOD_OPEN_ISSUES:-0}" == "1" ]]; then
	ARGS+=(--open-issue)
fi

if [[ -n "${DOGFOOD_ARTIFACT_DIR:-}" ]]; then
	ARGS+=(--artifact-dir "$DOGFOOD_ARTIFACT_DIR")
fi

if [[ -n "${DOGFOOD_ISSUE_REPO:-}" ]]; then
	ARGS+=(--issue-repo "$DOGFOOD_ISSUE_REPO")
fi

if [[ -n "${DOGFOOD_ISSUE_TITLE_PREFIX:-}" ]]; then
	ARGS+=(--issue-title-prefix "$DOGFOOD_ISSUE_TITLE_PREFIX")
fi

if [[ -n "${DOGFOOD_ISSUE_LABELS:-}" ]]; then
	IFS=',' read -ra labels <<< "$DOGFOOD_ISSUE_LABELS"
	for label in "${labels[@]}"; do
		if [[ -n "$label" ]]; then
			ARGS+=(--issue-label "$label")
		fi
	done
fi

exec uv run python scripts/benchmark_mcp_runtime.py "${ARGS[@]}" "$@"
