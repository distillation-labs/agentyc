from __future__ import annotations

import argparse
import json
from pathlib import Path

from agentyc.dogfood import evaluate_file_size_guard, write_json


def main() -> int:
	parser = argparse.ArgumentParser(description='Validate release-time modularity and file-size guards.')
	parser.add_argument(
		'--package-root',
		type=Path,
		default=Path('agentyc'),
		help='Package root to scan for Python source files.',
	)
	parser.add_argument(
		'--watch-python-lines',
		type=int,
		default=800,
		help='Track Python files above this line count in the JSON artifact without failing the guard.',
	)
	parser.add_argument(
		'--max-python-lines',
		type=int,
		default=1000,
		help='Maximum allowed line count for a single Python file under the package root.',
	)
	parser.add_argument(
		'--artifact-file',
		type=Path,
		help='Optional JSON artifact path for the guard result.',
	)
	args = parser.parse_args()
	if args.watch_python_lines >= args.max_python_lines:
		parser.error('--watch-python-lines must be lower than --max-python-lines')

	result = evaluate_file_size_guard(
		args.package_root,
		max_lines=args.max_python_lines,
		watch_lines=args.watch_python_lines,
	)
	if args.artifact_file is not None:
		write_json(args.artifact_file, result)

	print(json.dumps(result, indent=2, sort_keys=False))
	return 0 if result['passed'] else 1


if __name__ == '__main__':
	raise SystemExit(main())
