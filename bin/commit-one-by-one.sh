#!/usr/bin/env zsh
set -euo pipefail

# Commits each changed file individually, prompting for a message per file.
# Usage: bin/commit-one-by-one.sh [--staged | --all]
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

for FILE in "${UNIQUE_FILES[@]}"; do
	echo "─────────────────────────────────────────"
	echo "File: $FILE"

	# Show a quick diff summary
	if git ls-files --error-unmatch "$FILE" &>/dev/null 2>&1; then
		git diff HEAD -- "$FILE" | head -30 || true
	else
		echo "(new untracked file)"
	fi
	echo

	read "MSG?Commit message (leave blank to skip): "

	if [[ -z "$MSG" ]]; then
		echo "Skipping $FILE"
		echo
		continue
	fi

	git add -- "$FILE"
	git commit -m "$MSG"
	echo "Committed: $FILE"
	echo
done

echo "Done."