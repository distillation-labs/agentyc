from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class DogfoodRegression:
	slug: str
	reasons: list[str]


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
