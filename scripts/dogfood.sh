#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"
cd "$SCRIPT_DIR/.." || exit 1

ARGS=(--preset dogfood)

if [[ "${DOGFOOD_RELEASE_GATE:-0}" == "1" ]]; then
	ARGS+=(--release-gate)
fi

if [[ "${DOGFOOD_FAIL_ON_REGRESSION:-0}" == "1" ]]; then
	ARGS+=(--fail-on-regression)
fi

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

if [[ -n "${DOGFOOD_MAX_IMPORT_MS:-}" ]]; then
	ARGS+=(--max-import-ms "$DOGFOOD_MAX_IMPORT_MS")
fi

if [[ -n "${DOGFOOD_MAX_SESSION_INIT_MS:-}" ]]; then
	ARGS+=(--max-session-init-ms "$DOGFOOD_MAX_SESSION_INIT_MS")
fi

if [[ -n "${DOGFOOD_MIN_AVG_AUTO_PAYLOAD_REDUCTION_PCT:-}" ]]; then
	ARGS+=(--min-avg-auto-payload-reduction-pct "$DOGFOOD_MIN_AVG_AUTO_PAYLOAD_REDUCTION_PCT")
fi

if [[ -n "${DOGFOOD_MIN_AVG_AUTO_RECALL:-}" ]]; then
	ARGS+=(--min-avg-auto-recall "$DOGFOOD_MIN_AVG_AUTO_RECALL")
fi

if [[ -n "${DOGFOOD_MIN_AVG_MIN_RECALL:-}" ]]; then
	ARGS+=(--min-avg-min-recall "$DOGFOOD_MIN_AVG_MIN_RECALL")
fi

if [[ -n "${DOGFOOD_MIN_AVG_DETERMINISTIC_RECALL:-}" ]]; then
	ARGS+=(--min-avg-deterministic-recall "$DOGFOOD_MIN_AVG_DETERMINISTIC_RECALL")
fi

if [[ -n "${DOGFOOD_MIN_AVG_STRUCTURED_RECALL:-}" ]]; then
	ARGS+=(--min-avg-structured-recall "$DOGFOOD_MIN_AVG_STRUCTURED_RECALL")
fi

if [[ -n "${DOGFOOD_MIN_AVG_ACTION_SUCCESS:-}" ]]; then
	ARGS+=(--min-avg-action-success "$DOGFOOD_MIN_AVG_ACTION_SUCCESS")
fi

if [[ -n "${DOGFOOD_MIN_COLLABORATION_REQUIRED_CHECK_PASS_RATE:-}" ]]; then
	ARGS+=(--min-collaboration-required-check-pass-rate "$DOGFOOD_MIN_COLLABORATION_REQUIRED_CHECK_PASS_RATE")
fi

exec uv run python scripts/benchmark_mcp_runtime.py "${ARGS[@]}" "$@"
