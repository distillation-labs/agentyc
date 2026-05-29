from __future__ import annotations

import argparse
import json
from pathlib import Path

from agentyc.skill_quality import evaluate_skill_tree, render_text_report


def main() -> int:
	parser = argparse.ArgumentParser(description='Evaluate skill readiness from manifests and validation rules.')
	parser.add_argument(
		'--skills-root',
		type=Path,
		default=Path('.agents/skills'),
		help='Skill root directory to evaluate.',
	)
	parser.add_argument(
		'--format',
		choices=('text', 'json'),
		default='text',
		help='Report output format.',
	)
	parser.add_argument(
		'--output',
		type=Path,
		help='Optional file path for the report.',
	)
	args = parser.parse_args()

	results = evaluate_skill_tree(args.skills_root)
	if args.format == 'json':
		report = json.dumps([result.to_dict() for result in results], indent=2)
	else:
		report = render_text_report(results)

	if args.output is not None:
		args.output.write_text(report + ('\n' if not report.endswith('\n') else ''))
	else:
		print(report)

	return 0 if all(result.passed for result in results) else 1


if __name__ == '__main__':
	raise SystemExit(main())
