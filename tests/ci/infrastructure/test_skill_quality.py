from pathlib import Path

from agentyc.skill_quality import collect_skill_dirs, evaluate_skill_dir, evaluate_skill_tree, validate_skill_dir

SKILLS_ROOT = Path('.agents/skills')


def test_current_skills_validate_cleanly():
	results = [validate_skill_dir(skill_dir) for skill_dir in collect_skill_dirs(SKILLS_ROOT)]
	assert results
	assert all(result.passed for result in results), [result.errors for result in results if result.errors]


def test_current_skills_evaluate_cleanly():
	results = evaluate_skill_tree(SKILLS_ROOT)
	assert results
	assert all(result.passed for result in results), [result.coverage_errors for result in results if result.coverage_errors]


def test_validate_skill_dir_flags_missing_examples_and_evals(tmp_path: Path):
	skill_dir = tmp_path / 'broken-skill'
	(skill_dir / 'references').mkdir(parents=True)
	(skill_dir / 'evals').mkdir(parents=True)
	(skill_dir / 'SKILL.md').write_text(
		'---\n'
		'name: broken-skill\n'
		'description: Broken example skill. Use when validating failures.\n'
		'---\n\n'
		'# Broken Skill\n\n'
		'## References\n\n'
		'- `references/eval-rubric.md`\n'
	)
	(skill_dir / 'references' / 'eval-rubric.md').write_text('# Pass\n\n## Fail\n')
	(skill_dir / 'evals' / 'cases.yaml').write_text('version: 1\nskill: broken-skill\n')

	result = validate_skill_dir(skill_dir)

	assert not result.passed
	assert any('Examples' in error for error in result.errors)
	assert any('Troubleshooting' in error for error in result.errors)
	assert any('positive trigger cases' in error for error in result.errors)


def test_evaluate_skill_dir_flags_insufficient_trigger_coverage(tmp_path: Path):
	skill_dir = tmp_path / 'thin-skill'
	(skill_dir / 'references').mkdir(parents=True)
	(skill_dir / 'evals').mkdir(parents=True)
	(skill_dir / 'SKILL.md').write_text(
		'---\n'
		'name: thin-skill\n'
		'description: Thin skill example. Use when checking eval coverage.\n'
		'---\n\n'
		'# Thin Skill\n\n'
		'## Examples\n\n'
		'Example 1: minimal.\n\n'
		'## Troubleshooting\n\n'
		'- fix the coverage.\n\n'
		'## References\n\n'
		'- `references/eval-rubric.md`\n'
	)
	(skill_dir / 'references' / 'eval-rubric.md').write_text(
		'## Pass when the skill:\n- works\n\n## Fail when the skill:\n- breaks\n'
	)
	(skill_dir / 'evals' / 'cases.yaml').write_text(
		'version: 1\n'
		'skill: thin-skill\n'
		'suites:\n'
		'  - name: triggering\n'
		'    pass_thresholds:\n'
		'      relevant_trigger_rate: ">=90%"\n'
		'      unrelated_non_trigger_rate: "100%"\n'
		'    cases:\n'
		'      - id: trig-01\n'
		'        prompt: One prompt\n'
		'        expect_trigger: true\n'
		'  - name: functional\n'
		'    cases:\n'
		'      - id: func-01\n'
		'        prompt: One case\n'
		'        success_criteria:\n'
		'          - first\n'
		'          - second\n'
		'      - id: func-02\n'
		'        prompt: Two case\n'
		'        success_criteria:\n'
		'          - first\n'
		'          - second\n'
		'  - name: performance-and-robustness\n'
		'    baseline_without_skill:\n'
		'      symptoms:\n'
		'        - drift\n'
		'    expectations_with_skill:\n'
		'      - better\n'
	)

	result = evaluate_skill_dir(skill_dir)

	assert not result.passed
	assert not result.validation.passed or any(
		'triggering suite should contain at least 4 cases' == error for error in result.coverage_errors
	)
	assert result.trigger_case_count <= 1
