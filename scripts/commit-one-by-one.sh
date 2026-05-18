#!/usr/bin/env zsh
set -euo pipefail

# Commits each changed file individually with auto-generated commit messages.
# Usage: scripts/commit-one-by-one.sh [--staged | --all]
#   --staged  only files already in the index (default)
#   --all     also include untracked and unstaged files

MODE="${1:-}"

# Collect files into array
FILES=()
case "$MODE" in
	--all)
		while IFS= read -r line; do
			[[ -n "$line" ]] && FILES+=("$line")
		done < <(git diff --name-only HEAD; git ls-files --others --exclude-standard)
		;;
	--staged)
		while IFS= read -r line; do
			[[ -n "$line" ]] && FILES+=("$line")
		done < <(git diff --name-only --cached)
		;;
	*)
		# Default: unstaged changes + deleted files (not yet staged)
		while IFS= read -r line; do
			[[ -n "$line" ]] && FILES+=("$line")
		done < <(git diff --name-only; git diff --name-only --cached; git ls-files --others --exclude-standard)
		;;
esac

# Deduplicate while preserving order
typeset -A seen
UNIQUE_FILES=()
for f in "${FILES[@]}"; do
	if [[ -z "${seen[$f]:-}" ]]; then
		seen[$f]=1
		UNIQUE_FILES+=("$f")
	fi
done

if [[ ${#UNIQUE_FILES[@]} -eq 0 ]]; then
	echo "Nothing to commit."
	exit 0
fi

echo "Found ${#UNIQUE_FILES[@]} file(s) to commit:"
printf '  %s\n' "${UNIQUE_FILES[@]}"
echo

# Infer conventional commit type from file path
_commit_type() {
	case "$1" in
		docs/*|*.md|*.rst) echo "docs" ;;
		tests/*|test_*.py|*_test.py) echo "test" ;;
		scripts/*|bin/*|*.sh) echo "chore" ;;
		*.toml|*.yaml|*.yml|*.json|*.cfg|*.ini|.github/*|.gitignore) echo "chore" ;;
		*) echo "feat" ;;
	esac
}

# Infer verb from git tracking state
_commit_verb() {
	if ! git ls-files --error-unmatch "$1" &>/dev/null 2>&1; then
		echo "add"
	elif git ls-files --deleted -- "$1" | grep -q .; then
		echo "remove"
	else
		echo "update"
	fi
}

for FILE in "${UNIQUE_FILES[@]}"; do
	echo "─────────────────────────────────────────"
	echo "File: $FILE"

	TYPE=$(_commit_type "$FILE")
	VERB=$(_commit_verb "$FILE")
	BASE="${FILE##*/}"
	MSG="${TYPE}: ${VERB} ${BASE}"

	git add -- "$FILE"
	git commit -m "$MSG" -m "Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
	echo "Committed: $FILE — $MSG"
	echo
done

echo "Done."
