from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from agentyc.dogfood import build_issue_body, build_issue_title, create_github_issue, detect_regressions


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
