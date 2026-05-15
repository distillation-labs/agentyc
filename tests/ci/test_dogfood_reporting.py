from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from agentyc.dogfood import (
	build_issue_body,
	build_issue_title,
	collect_collaboration_regression_reasons,
	create_github_issue,
	detect_regressions,
	evaluate_file_size_guard,
	evaluate_release_gate,
)


def test_dogfood_issue_body_mentions_regressions(tmp_path: Path) -> None:
	output = {
		'summary': {
			'fixture_count': 2,
			'avg_action_success': 0.5,
		},
		'fixtures': {
			'confirm-dialog': {
				'title': 'Confirm dialog decision workflow',
				'state': {
					'auto': {'recall': {'recall': 1.0}},
					'min': {'recall': {'recall': 1.0}},
				},
				'action_reliability': {'passed': False, 'scenario': 'confirm-dialog'},
			},
			'dense-catalog': {
				'title': 'Dense catalog grid',
				'state': {
					'auto': {'recall': {'recall': 0.9}},
					'min': {'recall': {'recall': 1.0}},
				},
			},
		},
	}

	regressions = detect_regressions(output['fixtures'])
	assert [regression.slug for regression in regressions] == ['confirm-dialog', 'dense-catalog']

	title = build_issue_title(regressions, preset='dogfood')
	assert title.startswith('[dogfood] 2 regressions in dogfood preset')

	body = build_issue_body(
		preset='dogfood',
		command='./scripts/dogfood.sh',
		artifact_dir=tmp_path,
		output=output,
		regressions=regressions,
	)

	assert 'Automated dogfood regression' in body
	assert './scripts/dogfood.sh' in body
	assert str(tmp_path) in body
	assert '`confirm-dialog`' in body
	assert 'action reliability failed (confirm-dialog)' in body
	assert 'auto recall' in body


def test_create_github_issue_builds_expected_command(monkeypatch, tmp_path: Path) -> None:
	called = {}

	def fake_run(command, check, capture_output, text):
		called['command'] = command
		called['check'] = check
		called['capture_output'] = capture_output
		called['text'] = text
		return SimpleNamespace(returncode=0, stdout='https://github.com/org/repo/issues/1', stderr='')

	monkeypatch.setattr('agentyc.dogfood.subprocess.run', fake_run)

	issue_url = create_github_issue(
		title='[dogfood] dense-catalog regression',
		body_file=tmp_path / 'issue.md',
		labels=['dogfood', 'automation'],
		repo='org/repo',
	)

	assert issue_url == 'https://github.com/org/repo/issues/1'
	assert called['command'][:3] == ['gh', 'issue', 'create']
	assert '--repo' in called['command']
	assert 'org/repo' in called['command']
	assert called['command'].count('--label') == 2
	assert called['check'] is False
	assert called['capture_output'] is True
	assert called['text'] is True


def test_evaluate_release_gate_reports_threshold_and_regression_failures() -> None:
	output = {
		'import_ms': 1800.0,
		'session_init_ms': 9000.0,
		'summary': {
			'fixture_count': 2,
			'avg_auto_payload_reduction_pct': 28.0,
			'avg_auto_recall': 0.95,
			'avg_min_recall': 1.0,
			'avg_deterministic_recall': 1.0,
			'avg_structured_recall': 1.0,
			'avg_action_success': 0.5,
			'collaboration_required_check_pass_rate': 0.75,
		},
		'collaboration': {
			'passed': False,
			'tab_runtime_pair': {
				'passed': False,
				'checks': [
					{'key': 'distinct_current_tabs', 'passed': False},
					{'key': 'runtime_a_current_tab_owner_matches', 'passed': True},
				],
			},
			'window_mode_probe': {'required': False, 'passed': False},
		},
		'fixtures': {
			'confirm-dialog': {
				'title': 'Confirm dialog decision workflow',
				'state': {
					'auto': {'recall': {'recall': 1.0}},
					'min': {'recall': {'recall': 1.0}},
				},
				'action_reliability': {'passed': False, 'scenario': 'confirm-dialog'},
			},
			'dense-catalog': {
				'title': 'Dense catalog grid',
				'state': {
					'auto': {'recall': {'recall': 0.9}},
					'min': {'recall': {'recall': 1.0}},
				},
			},
		},
	}

	gate = evaluate_release_gate(
		output,
		preset='dogfood',
		min_avg_auto_recall=0.99,
		min_avg_action_success=1.0,
		min_collaboration_required_check_pass_rate=1.0,
		max_import_ms=2000.0,
	)

	assert gate['passed'] is False
	assert gate['regression_count'] == 2
	assert any(check['key'] == 'avg_auto_recall' and check['passed'] is False for check in gate['checks'])
	assert any(check['key'] == 'avg_action_success' and check['passed'] is False for check in gate['checks'])
	assert any(
		check['key'] == 'collaboration_required_check_pass_rate' and check['passed'] is False for check in gate['checks']
	)
	assert any('fixture regressions detected' in failure for failure in gate['failures'])
	assert any('collaboration required checks failed' in failure for failure in gate['failures'])


def test_evaluate_release_gate_passes_when_output_meets_expectations() -> None:
	output = {
		'import_ms': 1200.0,
		'session_init_ms': 6000.0,
		'summary': {
			'fixture_count': 1,
			'avg_auto_payload_reduction_pct': 31.0,
			'avg_auto_recall': 1.0,
			'avg_min_recall': 1.0,
			'avg_deterministic_recall': 1.0,
			'avg_structured_recall': 1.0,
			'avg_action_success': 1.0,
			'collaboration_required_check_pass_rate': 1.0,
		},
		'collaboration': {
			'passed': True,
			'tab_runtime_pair': {
				'passed': True,
				'checks': [
					{'key': 'distinct_current_tabs', 'passed': True},
					{'key': 'runtime_a_current_tab_owner_matches', 'passed': True},
				],
			},
			'window_mode_probe': {'required': False, 'passed': False, 'exercised': False},
		},
		'fixtures': {
			'small-form': {
				'title': 'Small accessible form',
				'state': {
					'auto': {'recall': {'recall': 1.0}},
					'min': {'recall': {'recall': 1.0}},
				},
				'extract_content': {
					'deterministic': {
						'match': {'recall': 1.0},
						'structured': False,
					}
				},
			},
		},
	}

	gate = evaluate_release_gate(
		output,
		preset='dogfood',
		max_import_ms=2000.0,
		max_session_init_ms=8000.0,
		min_avg_auto_payload_reduction_pct=20.0,
		min_avg_auto_recall=0.99,
		min_avg_min_recall=0.99,
		min_avg_deterministic_recall=0.99,
		min_avg_structured_recall=0.99,
		min_avg_action_success=1.0,
		min_collaboration_required_check_pass_rate=1.0,
	)

	assert gate['passed'] is True
	assert gate['failures'] == []
	assert all(check['passed'] is True for check in gate['checks'])


def test_collect_collaboration_regression_reasons_reports_failed_checks() -> None:
	output = {
		'collaboration': {
			'tab_runtime_pair': {
				'passed': False,
				'checks': [
					{'key': 'distinct_runtime_ids', 'passed': True},
					{'key': 'distinct_current_tabs', 'passed': False},
				],
			},
			'window_mode_probe': {'required': False, 'passed': False},
		}
	}

	reasons = collect_collaboration_regression_reasons(output)

	assert reasons == ['collaboration required checks failed: distinct_current_tabs']


def test_evaluate_file_size_guard_reports_oversized_modules(tmp_path: Path) -> None:
	package_root = tmp_path / 'agentyc'
	package_root.mkdir()
	(package_root / 'small.py').write_text('print(1)\n', encoding='utf-8')
	(package_root / 'too_big.py').write_text('x = 1\n' * 5, encoding='utf-8')

	guard = evaluate_file_size_guard(package_root, max_lines=3)

	assert guard['passed'] is False
	assert guard['violations'] == [{'path': 'agentyc/too_big.py', 'line_count': 6}]


def test_evaluate_file_size_guard_tracks_watchlist_without_failing(tmp_path: Path) -> None:
	package_root = tmp_path / 'agentyc'
	package_root.mkdir()
	(package_root / 'small.py').write_text('print(1)\n', encoding='utf-8')
	(package_root / 'watch.py').write_text('x = 1\n' * 5, encoding='utf-8')

	guard = evaluate_file_size_guard(package_root, max_lines=10, watch_lines=3)

	assert guard['passed'] is True
	assert guard['watch_lines'] == 3
	assert guard['watchlist_count'] == 1
	assert guard['watchlist'] == [{'path': 'agentyc/watch.py', 'line_count': 6}]
	assert guard['violations'] == []
