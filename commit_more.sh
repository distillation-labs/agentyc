#!/bin/bash
set -e

git status --porcelain | while IFS= read -r line; do
  st="${line:0:2}"
  fl="${line:3}"
  fl="$(echo "$fl" | sed 's/^ *//;s/ *$//')"
  echo "--- [$st] $fl ---"
  case "$st" in
    "M "|" M") git add "$fl" && git commit -m "update: $fl" ;;
    "D "|" D") git rm "$fl" && git commit -m "remove: $fl" ;;
    "??") git add "$fl" && git commit -m "add: $fl" ;;
  esac
  git push origin main
  echo "Done: $fl"
done
