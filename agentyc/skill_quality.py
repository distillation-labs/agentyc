from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import yaml

SKILLS_ROOT = Path('.agents/skills')
KEBAB_CASE_RE = re.compile(r'^[a-z0-9]+(?:-[a-z0-9]+)*$')
REQUIRED_BODY_SECTIONS = (
	'## Examples',
	'## Troubleshooting',
	'## References',
)
REQUIRED_EVAL_SUITES = (
	'triggering',
	'functional',
	'performance-and-robustness',
)


@dataclass
class SkillValidationResult:
	skill_name: str
	passed: bool
	errors: list[str]
	word_count: int
	description_length: int


@dataclass
class SkillEvaluationResult:
	skill_name: str
	passed: bool
	validation: SkillValidationResult
	coverage_errors: list[str]
	trigger_case_count: int
	trigger_positive_count: int
	trigger_negative_count: int
	functional_case_count: int
	performance_expectation_count: int

	def to_dict(self) -> dict[str, Any]:
		return asdict(self)


def collect_skill_dirs(root: Path = SKILLS_ROOT) -> list[Path]:
	return sorted(path for path in root.iterdir() if path.is_dir())


def _extract_frontmatter(text: str) -> tuple[str, str] | None:
	if not text.startswith('---\n'):
		return None
	parts = text.split('\n---\n', 1)
	if len(parts) != 2:
		return None
	return parts[0][4:], parts[1]


def _parse_frontmatter(frontmatter: str) -> dict[str, str]:
	parsed: dict[str, str] = {}
	current_key: str | None = None
	buffer: list[str] = []

	def flush() -> None:
		nonlocal current_key, buffer
		if current_key is not None:
			value = '\n'.join(buffer).strip()
			if value in {'>', '|'}:
				value = ''
			elif value.startswith('>\n') or value.startswith('|\n'):
				value = value[2:].lstrip('\n')
			parsed[current_key] = value
		current_key = None
		buffer = []

	for line in frontmatter.splitlines():
		if not line.strip():
			if current_key is not None:
				buffer.append('')
			continue
		if not line.startswith((' ', '\t')) and ':' in line:
			flush()
			key, value = line.split(':', 1)
			current_key = key.strip()
			buffer = [value.strip()]
		else:
			buffer.append(line.strip())
	flush()
	return parsed


def _contains_forbidden_angle_brackets(value: str) -> bool:
	return '<' in value or '>' in value


def _word_count(text: str) -> int:
	return len(text.split())


def validate_skill_dir(skill_dir: Path) -> SkillValidationResult:
	errors: list[str] = []
	skill_file = skill_dir / 'SKILL.md'
	rubric_file = skill_dir / 'references' / 'eval-rubric.md'
	evals_file = skill_dir / 'evals' / 'cases.yaml'
	description_length = 0
	word_count = 0

	if not KEBAB_CASE_RE.match(skill_dir.name):
		errors.append('directory name must be kebab-case')
	if (skill_dir / 'README.md').exists():
		errors.append('README.md is not allowed inside a skill folder')
	if not skill_file.exists():
		errors.append('missing SKILL.md')
		return SkillValidationResult(skill_dir.name, False, errors, word_count, description_length)
	if not rubric_file.exists():
		errors.append('missing references/eval-rubric.md')
	if not evals_file.exists():
		errors.append('missing evals/cases.yaml')

	text = skill_file.read_text()
	word_count = _word_count(text)
	frontmatter_result = _extract_frontmatter(text)
	if frontmatter_result is None:
		errors.append('SKILL.md must start with YAML frontmatter delimited by ---')
		return SkillValidationResult(skill_dir.name, False, errors, word_count, description_length)

	frontmatter_text, body = frontmatter_result
	frontmatter = _parse_frontmatter(frontmatter_text)
	name = frontmatter.get('name', '')
	description = frontmatter.get('description', '')

	if not name:
		errors.append('frontmatter missing name')
	elif name != skill_dir.name:
		errors.append('frontmatter name must match directory name')
	elif not KEBAB_CASE_RE.match(name):
		errors.append('frontmatter name must be kebab-case')

	if not description:
		errors.append('frontmatter missing description')
	else:
		flat_description = re.sub(r'\s+', ' ', description).strip()
		description_length = len(flat_description)
		if description_length > 1024:
			errors.append('description must be 1024 characters or fewer')
		if _contains_forbidden_angle_brackets(flat_description):
			errors.append('description must not contain angle brackets')
		if 'use ' not in flat_description.lower():
			errors.append('description must say when to use the skill')

	for key, value in frontmatter.items():
		if _contains_forbidden_angle_brackets(value):
			errors.append(f'frontmatter field {key!r} must not contain angle brackets')
	if word_count > 5000:
		errors.append('SKILL.md should stay under 5000 words for progressive disclosure')

	for section in REQUIRED_BODY_SECTIONS:
		if section not in body:
			errors.append(f'missing body section {section!r}')

	if rubric_file.exists():
		rubric_text = rubric_file.read_text().lower()
		if 'pass' not in rubric_text or 'fail' not in rubric_text:
			errors.append('eval rubric should define pass and fail behavior')

	if evals_file.exists():
		evals_text = evals_file.read_text()
		for suite in REQUIRED_EVAL_SUITES:
			if f'- name: {suite}' not in evals_text:
				errors.append(f'evals/cases.yaml missing suite {suite!r}')
		if 'expect_trigger: true' not in evals_text:
			errors.append('evals/cases.yaml must include positive trigger cases')
		if 'expect_trigger: false' not in evals_text:
			errors.append('evals/cases.yaml must include negative trigger cases')
		if 'baseline_without_skill:' not in evals_text:
			errors.append('evals/cases.yaml must include a no-skill baseline section')

	return SkillValidationResult(skill_dir.name, not errors, errors, word_count, description_length)


def _load_eval_cases(skill_dir: Path) -> dict[str, Any]:
	with (skill_dir / 'evals' / 'cases.yaml').open() as handle:
		loaded = yaml.safe_load(handle)
	if not isinstance(loaded, dict):
		raise ValueError('evals/cases.yaml must contain a mapping at the top level')
	return loaded


def _find_suite(data: dict[str, Any], suite_name: str) -> dict[str, Any] | None:
	for suite in data.get('suites', []):
		if isinstance(suite, dict) and suite.get('name') == suite_name:
			return suite
	return None


def evaluate_skill_dir(skill_dir: Path) -> SkillEvaluationResult:
	validation = validate_skill_dir(skill_dir)
	coverage_errors: list[str] = []
	trigger_case_count = 0
	trigger_positive_count = 0
	trigger_negative_count = 0
	functional_case_count = 0
	performance_expectation_count = 0

	if validation.passed:
		try:
			data = _load_eval_cases(skill_dir)
		except Exception as exc:
			coverage_errors.append(f'could not parse evals/cases.yaml: {exc}')
		else:
			triggering = _find_suite(data, 'triggering')
			functional = _find_suite(data, 'functional')
			performance = _find_suite(data, 'performance-and-robustness')

			if triggering is None:
				coverage_errors.append('missing triggering suite')
			else:
				trigger_cases = triggering.get('cases', [])
				if not isinstance(trigger_cases, list):
					coverage_errors.append('triggering suite cases must be a list')
				else:
					trigger_case_count = len(trigger_cases)
					trigger_positive_count = sum(1 for case in trigger_cases if case.get('expect_trigger') is True)
					trigger_negative_count = sum(1 for case in trigger_cases if case.get('expect_trigger') is False)
					if trigger_case_count < 4:
						coverage_errors.append('triggering suite should contain at least 4 cases')
					if trigger_positive_count < 2:
						coverage_errors.append('triggering suite should contain at least 2 positive trigger cases')
					if trigger_negative_count < 2:
						coverage_errors.append('triggering suite should contain at least 2 negative trigger cases')
				if not isinstance(triggering.get('pass_thresholds'), dict):
					coverage_errors.append('triggering suite should define pass_thresholds')

			if functional is None:
				coverage_errors.append('missing functional suite')
			else:
				functional_cases = functional.get('cases', [])
				if not isinstance(functional_cases, list):
					coverage_errors.append('functional suite cases must be a list')
				else:
					functional_case_count = len(functional_cases)
					if functional_case_count < 2:
						coverage_errors.append('functional suite should contain at least 2 cases')
					for case in functional_cases:
						criteria = case.get('success_criteria')
						if not isinstance(criteria, list) or len(criteria) < 2:
							coverage_errors.append(
								f'functional case {case.get("id", "<unknown>")} should define at least 2 success criteria'
							)

			if performance is None:
				coverage_errors.append('missing performance-and-robustness suite')
			else:
				baseline = performance.get('baseline_without_skill')
				expectations = performance.get('expectations_with_skill')
				if not isinstance(baseline, dict) or not baseline:
					coverage_errors.append('performance suite must define baseline_without_skill')
				if not isinstance(expectations, list) or not expectations:
					coverage_errors.append('performance suite must define expectations_with_skill')
				else:
					performance_expectation_count = len(expectations)

	passed = validation.passed and not coverage_errors
	return SkillEvaluationResult(
		skill_name=skill_dir.name,
		passed=passed,
		validation=validation,
		coverage_errors=coverage_errors,
		trigger_case_count=trigger_case_count,
		trigger_positive_count=trigger_positive_count,
		trigger_negative_count=trigger_negative_count,
		functional_case_count=functional_case_count,
		performance_expectation_count=performance_expectation_count,
	)


def evaluate_skill_tree(root: Path = SKILLS_ROOT) -> list[SkillEvaluationResult]:
	return [evaluate_skill_dir(skill_dir) for skill_dir in collect_skill_dirs(root)]


def render_text_report(results: list[SkillEvaluationResult]) -> str:
	passed = sum(1 for result in results if result.passed)
	lines = [f'Evaluated {len(results)} skills: {passed} passed, {len(results) - passed} failed.']
	for result in results:
		status = 'PASS' if result.passed else 'FAIL'
		lines.append(
			f'{status} {result.skill_name}: words={result.validation.word_count}, '
			f'description_chars={result.validation.description_length}, '
			f'trigger_cases={result.trigger_case_count}, functional_cases={result.functional_case_count}, '
			f'performance_expectations={result.performance_expectation_count}'
		)
		for error in result.validation.errors:
			lines.append(f'  - validation: {error}')
		for error in result.coverage_errors:
			lines.append(f'  - evaluation: {error}')
	return '\n'.join(lines)
