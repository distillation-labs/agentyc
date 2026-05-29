from __future__ import annotations

from pathlib import Path

from agentyc.skill_quality import collect_skill_dirs, validate_skill_dir


def main() -> int:
	all_errors: list[str] = []
	skill_dirs = collect_skill_dirs(Path('.agents/skills'))
	for skill_dir in skill_dirs:
		result = validate_skill_dir(skill_dir)
		all_errors.extend(f'{skill_dir}: {error}' for error in result.errors)

	if all_errors:
		for error in all_errors:
			print(f'FAIL: {error}')
		return 1

	print(f'Validated {len(skill_dirs)} skills successfully.')
	return 0


if __name__ == '__main__':
	raise SystemExit(main())
