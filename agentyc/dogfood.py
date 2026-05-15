from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from operator import ge, le
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class DogfoodRegression:
	slug: str
	reasons: list[str]


@dataclass(slots=True)
class FileSizeViolation:
	path: str
	line_count: int


def default_artifact_root() -> Path:
	timestamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
	return Path.home() / '.agentyc' / 'dogfood' / timestamp


def _metric_recall(metric: Any) -> float | None:
	if isinstance(metric, dict):
		recall = metric.get('recall')
		if isinstance(recall, (int, float)):
			return float(recall)
	return None


def collect_fixture_regression_reasons(result: dict[str, Any]) -> list[str]:
	reasons: list[str] = []

	action = result.get('action_reliability')
	if isinstance(action, dict) and action.get('passed') is False:
		scenario = action.get('scenario')
		reasons.append(f'action reliability failed{f" ({scenario})" if scenario else ""}')

	state = result.get('state')
	if isinstance(state, dict):
		for mode in ('auto', 'min'):
			mode_metrics = state.get(mode)
			if not isinstance(mode_metrics, dict):
				continue
			recall = _metric_recall(mode_metrics.get('recall'))
			if recall is not None and recall < 1.0:
				reasons.append(f'{mode} recall {recall:.3f}')

	extract_content = result.get('extract_content')
	if isinstance(extract_content, dict):
		deterministic = extract_content.get('deterministic')
		if isinstance(deterministic, dict):
			recall = _metric_recall(deterministic.get('match'))
			if recall is not None and recall < 1.0:
				if deterministic.get('structured'):
					reasons.append(f'structured extraction recall {recall:.3f}')
				else:
					reasons.append(f'deterministic extraction recall {recall:.3f}')

	return reasons


def collect_collaboration_regression_reasons(output: dict[str, Any]) -> list[str]:
	reasons: list[str] = []
	collaboration = output.get('collaboration')
	if not isinstance(collaboration, dict):
		return reasons

	tab_runtime_pair = collaboration.get('tab_runtime_pair')
	if isinstance(tab_runtime_pair, dict) and tab_runtime_pair.get('passed') is False:
		failed_checks: list[str] = []
		for check in tab_runtime_pair.get('checks', []):
			if not isinstance(check, dict) or check.get('passed') is not False:
				continue
			key = check.get('key')
			if isinstance(key, str):
				failed_checks.append(key)
		if failed_checks:
			reasons.append('collaboration required checks failed: ' + ', '.join(failed_checks))
		else:
			reasons.append('collaboration required checks failed')

	window_mode_probe = collaboration.get('window_mode_probe')
	if isinstance(window_mode_probe, dict) and window_mode_probe.get('required'):
		if window_mode_probe.get('passed') is False:
			reasons.append('collaboration window-mode probe failed')

	return reasons


def detect_regressions(results: dict[str, dict[str, Any]]) -> list[DogfoodRegression]:
	regressions: list[DogfoodRegression] = []
	for slug, result in results.items():
		reasons = collect_fixture_regression_reasons(result)
		if reasons:
			regressions.append(DogfoodRegression(slug=slug, reasons=reasons))
	return regressions


def build_issue_title(
	regressions: list[DogfoodRegression],
	*,
	preset: str,
	title_prefix: str = '[dogfood]',
) -> str:
	if not regressions:
		raise ValueError('Cannot build a dogfood issue title without regressions')
	if len(regressions) == 1:
		regression = regressions[0]
		reasons = ', '.join(regression.reasons[:2])
		return f'{title_prefix} {regression.slug}: {reasons}'
	return f'{title_prefix} {len(regressions)} regressions in {preset} preset'


def build_issue_body(
	*,
	preset: str,
	command: str,
	artifact_dir: Path,
	output: dict[str, Any],
	regressions: list[DogfoodRegression],
) -> str:
	summary = output.get('summary', {})
	fixtures = output.get('fixtures', {})

	lines: list[str] = [
		'## Automated dogfood regression',
		'',
		f'- Preset: `{preset}`',
		f'- Reproduction: `{command}`',
		f'- Artifact directory: `{artifact_dir}`',
		f'- Fixture count: `{summary.get("fixture_count", 0)}`',
		f'- Failing fixtures: {", ".join(regression.slug for regression in regressions)}',
		'',
		'## Summary',
		'```json',
		json.dumps(summary, indent=2, sort_keys=False),
		'```',
		'',
		'## Failing fixtures',
	]

	for regression in regressions:
		fixture = fixtures.get(regression.slug, {})
		state = fixture.get('state', {}) if isinstance(fixture, dict) else {}
		auto_state = state.get('auto', {}) if isinstance(state, dict) else {}
		min_state = state.get('min', {}) if isinstance(state, dict) else {}
		lines.extend(
			[
				f'### `{regression.slug}`',
				f'- Title: {fixture.get("title", "Unknown fixture")}',
				f'- Reasons: {", ".join(regression.reasons)}',
				f'- Auto recall: {auto_state.get("recall", {}).get("recall", "n/a")}',
				f'- Min recall: {min_state.get("recall", {}).get("recall", "n/a")}',
				f'- Artifact bundle: `{artifact_dir / regression.slug}`',
				'',
			]
		)

	return '\n'.join(lines).rstrip() + '\n'


def _serialize_regressions(regressions: list[DogfoodRegression]) -> list[dict[str, Any]]:
	return [{'slug': regression.slug, 'reasons': regression.reasons} for regression in regressions]


def _metric_check(
	checks: list[dict[str, Any]],
	failures: list[str],
	*,
	key: str,
	actual: float | None,
	threshold: float | None,
	operator: str,
) -> None:
	if threshold is None:
		return
	if actual is None:
		checks.append({'key': key, 'actual': None, 'threshold': threshold, 'operator': operator, 'passed': False})
		failures.append(f'{key} is missing from benchmark output')
		return

	compare = ge if operator == '>=' else le
	passed = compare(actual, threshold)
	checks.append({'key': key, 'actual': actual, 'threshold': threshold, 'operator': operator, 'passed': passed})
	if not passed:
		failures.append(f'{key} {actual:.3f} must be {operator} {threshold:.3f}')


def evaluate_release_gate(
	output: dict[str, Any],
	*,
	preset: str,
	max_import_ms: float | None = None,
	max_session_init_ms: float | None = None,
	min_avg_auto_payload_reduction_pct: float | None = None,
	min_avg_auto_recall: float | None = None,
	min_avg_min_recall: float | None = None,
	min_avg_deterministic_recall: float | None = None,
	min_avg_structured_recall: float | None = None,
	min_avg_action_success: float | None = None,
	min_collaboration_required_check_pass_rate: float | None = None,
) -> dict[str, Any]:
	fixtures = output.get('fixtures', {})
	regressions = detect_regressions(fixtures) if isinstance(fixtures, dict) else []
	collaboration_failures = collect_collaboration_regression_reasons(output)
	summary = output.get('summary', {}) if isinstance(output.get('summary'), dict) else {}
	checks: list[dict[str, Any]] = []
	failures: list[str] = []

	regression_count = len(regressions)
	regression_check = {
		'key': 'fixture_regressions',
		'actual': regression_count,
		'threshold': 0,
		'operator': '==',
		'passed': regression_count == 0,
	}
	checks.append(regression_check)
	if regression_count:
		failures.append(
			'fixture regressions detected: '
			+ ', '.join(f'{regression.slug} ({", ".join(regression.reasons)})' for regression in regressions)
		)
	for failure in collaboration_failures:
		failures.append(failure)

	_metric_check(
		checks,
		failures,
		key='import_ms',
		actual=float(output['import_ms']) if isinstance(output.get('import_ms'), (int, float)) else None,
		threshold=max_import_ms,
		operator='<=',
	)
	_metric_check(
		checks,
		failures,
		key='session_init_ms',
		actual=float(output['session_init_ms']) if isinstance(output.get('session_init_ms'), (int, float)) else None,
		threshold=max_session_init_ms,
		operator='<=',
	)

	for key, threshold in (
		('avg_auto_payload_reduction_pct', min_avg_auto_payload_reduction_pct),
		('avg_auto_recall', min_avg_auto_recall),
		('avg_min_recall', min_avg_min_recall),
		('avg_deterministic_recall', min_avg_deterministic_recall),
		('avg_structured_recall', min_avg_structured_recall),
		('avg_action_success', min_avg_action_success),
		('collaboration_required_check_pass_rate', min_collaboration_required_check_pass_rate),
	):
		actual = summary.get(key)
		_metric_check(
			checks,
			failures,
			key=key,
			actual=float(actual) if isinstance(actual, (int, float)) else None,
			threshold=threshold,
			operator='>=',
		)

	return {
		'name': 'release',
		'preset': preset,
		'passed': not failures,
		'checks': checks,
		'failures': failures,
		'regression_count': regression_count,
		'regressions': _serialize_regressions(regressions),
	}


def evaluate_file_size_guard(package_root: Path, *, max_lines: int, watch_lines: int | None = None) -> dict[str, Any]:
	violations: list[FileSizeViolation] = []
	watchlist: list[FileSizeViolation] = []
	python_files = sorted(path for path in package_root.rglob('*.py') if path.is_file())
	for path in python_files:
		line_count = path.read_text(encoding='utf-8').count('\n') + 1
		if line_count > max_lines:
			violations.append(FileSizeViolation(path=path.relative_to(package_root.parent).as_posix(), line_count=line_count))
		elif watch_lines is not None and line_count > watch_lines:
			watchlist.append(FileSizeViolation(path=path.relative_to(package_root.parent).as_posix(), line_count=line_count))

	return {
		'name': 'file_size_guard',
		'package_root': str(package_root),
		'watch_lines': watch_lines,
		'max_lines': max_lines,
		'scanned_file_count': len(python_files),
		'passed': not violations,
		'watchlist_count': len(watchlist),
		'watchlist': [{'path': entry.path, 'line_count': entry.line_count} for entry in watchlist],
		'violations': [{'path': violation.path, 'line_count': violation.line_count} for violation in violations],
	}


def write_text(path: Path, content: str) -> None:
	path.parent.mkdir(parents=True, exist_ok=True)
	path.write_text(content, encoding='utf-8')


def write_json(path: Path, payload: Any) -> None:
	write_text(path, json.dumps(payload, indent=2, sort_keys=False))


def create_github_issue(
	*,
	title: str,
	body_file: Path,
	labels: list[str],
	repo: str | None = None,
) -> str:
	if shutil.which('gh') is None:
		raise RuntimeError('GitHub CLI (gh) is required for automatic issue creation')

	command = ['gh', 'issue', 'create', '--title', title, '--body-file', str(body_file)]
	if repo:
		command.extend(['--repo', repo])
	for label in labels:
		command.extend(['--label', label])

	result = subprocess.run(command, check=False, capture_output=True, text=True)
	if result.returncode != 0:
		raise RuntimeError(result.stderr.strip() or 'Failed to create GitHub issue')
	return result.stdout.strip()
