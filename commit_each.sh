#!/bin/bash
set -e

git status --porcelain | while IFS= read -r line; do
  status="${line:0:2}"
  file="${line:3}"

  # Normalize: strip leading/trailing whitespace from file
  file="$(echo "$file" | sed 's/^ *//;s/ *$//')"

  echo ""
  echo "============================================"
  echo "Processing: [$status] $file"
  echo "============================================"

  case "$status" in
    "M "|" M")
      git add "$file"
      git commit -m "update: $file"
      ;;
    "D "|" D")
      git rm "$file"
      git commit -m "remove: $file"
      ;;
    "??")
      git add "$file"
      git commit -m "add: $file"
      ;;
    *)
      echo "Unknown status '$status' for '$file', skipping..."
      continue
      ;;
  esac

  echo "Pushing..."
  git push origin main
  echo "Done: $file"
done

echo ""
echo "All files processed!"
