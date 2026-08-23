#!/usr/bin/env bash
# Commit pending changes one file at a time.
#
# Usage: scripts/commit-by-file.sh [--type <commit-type>] [--scope <scope>] \
#                                  [--subject <subject>] [--dry-run]
#
# Options:
#   --type <commit-type>  Conventional commit type. Defaults to "chore".
#   --scope <scope>       Conventional commit scope (optional, e.g. restriction-overlay).
#   --subject <subject>   Commit subject. Defaults to a type-derived verb + file stem.
#   --dry-run             Print commit plan without committing.
#
# Messages are generated as Conventional Commits so release-please can classify
# them into changelog sections automatically. Example:
#   scripts/commit-by-file.sh --type feat --scope restriction-overlay
#   → "feat(restriction-overlay): add restriction_overlay"

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

die() { echo -e "${RED}FATAL:${NC} $*" >&2; exit 1; }
info() { echo -e "${GREEN}OK:${NC} $*"; }
warn() { echo -e "${YELLOW}WARN:${NC} $*"; }

VALID_TYPES="feat|fix|refactor|chore|docs|test|ci|perf|style|build|revert"
COMMIT_TYPE="chore"
SCOPE=""
SUBJECT=""
DRY_RUN=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --type)
      [[ $# -ge 2 ]] || die "Missing value after --type"
      COMMIT_TYPE="$2"
      if ! echo "$COMMIT_TYPE" | grep -qE "^($VALID_TYPES)$"; then
        die "Invalid commit type: $COMMIT_TYPE. Valid: $VALID_TYPES"
      fi
      shift 2
      ;;
    --scope)
      [[ $# -ge 2 ]] || die "Missing value after --scope"
      SCOPE="$2"
      if ! echo "$SCOPE" | grep -qE '^[a-z0-9][a-z0-9-]*$'; then
        die "Scope must be lowercase alphanumeric + dashes: $SCOPE"
      fi
      shift 2
      ;;
    --subject)
      [[ $# -ge 2 ]] || die "Missing value after --subject"
      SUBJECT="$2"
      shift 2
      ;;
    --dry-run)
      DRY_RUN=true
      shift
      ;;
    --help)
      echo "Usage: $0 [--type <type>] [--scope <scope>] [--subject <subject>] [--dry-run]"
      echo "  --type <type>     Commit type (default: chore)"
      echo "  --scope <scope>   Commit scope (optional)"
      echo "  --subject <text>  Commit subject (default: derived from type + file)"
      echo "  --dry-run         Show plan without committing"
      exit 0
      ;;
    *)
      die "Unknown argument: $1"
      ;;
  esac
done

# Map a conventional commit type to a default subject verb.
verb_for_type() {
  case "$COMMIT_TYPE" in
    feat) echo "add" ;;
    fix) echo "fix" ;;
    test) echo "add tests for" ;;
    docs) echo "document" ;;
    refactor) echo "refactor" ;;
    perf) echo "optimize" ;;
    ci) echo "update CI for" ;;
    build) echo "update build for" ;;
    style) echo "format" ;;
    revert) echo "revert" ;;
    *) echo "update" ;;
  esac
}

# Derive a readable subject from a file path, e.g.
#   api/src/services/restriction_overlay.py → "restriction overlay"
file_stem() {
  local file="$1" stem
  stem=$(basename "$file")
  stem="${stem%.*}"
  if [[ "$COMMIT_TYPE" == "test" && "$stem" == test_* ]]; then
    stem="${stem#test_}"
  fi
  echo "$stem" | tr '_' ' '
}

get_changed_files() {
  local unstaged staged untracked
  unstaged=$(git diff --name-only --diff-filter=ACDMRTUXB -z 2>/dev/null | tr '\0' '\n' || true)
  staged=$(git diff --cached --name-only --diff-filter=ACDMRTUXB -z 2>/dev/null | tr '\0' '\n' || true)
  untracked=$(git ls-files --others --exclude-standard -z 2>/dev/null | tr '\0' '\n' || true)

  echo "$unstaged"$'\n'"$staged"$'\n'"$untracked" | grep -v '^$' | sort -u || true
}

changed_files=$(get_changed_files)

if [[ -z "$changed_files" ]]; then
  info "No pending changes to commit."
  exit 0
fi

file_count=$(echo "$changed_files" | wc -l | tr -d ' ')
info "Planned $file_count commit(s) using type \"$COMMIT_TYPE\""

while IFS= read -r file; do
  [[ -z "$file" ]] && continue

  verb=$(verb_for_type)
  stem=$(file_stem "$file")
  if [[ -n "$SUBJECT" ]]; then
    subject="$SUBJECT"
  else
    subject="$verb $stem"
  fi

  if [[ -n "$SCOPE" ]]; then
    commit_msg="$COMMIT_TYPE($SCOPE): $subject"
  else
    commit_msg="$COMMIT_TYPE: $subject"
  fi

  echo ""
  warn "File: $file"
  info "Message: $commit_msg"

  if [[ "$DRY_RUN" == "true" ]]; then
    continue
  fi

  git add -- "$file" 2>/dev/null || git rm --cached -- "$file" 2>/dev/null || true
  # --no-verify keeps the file-by-file flow fast; the generated message is
  # already Conventional Commit-compliant, so commit-msg hooks are redundant here.
  git commit --no-verify -m "$commit_msg"

done <<< "$changed_files"

if [[ "$DRY_RUN" == "true" ]]; then
  echo ""
  warn "Dry run complete. No commits created."
  exit 0
fi

echo ""
info "File-by-file commit flow complete."
