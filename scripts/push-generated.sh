#!/usr/bin/env bash
# Run after committing generated files. Preserve other workflows' commits.
set -euo pipefail
target_branch="${GITHUB_REF_NAME:-main}"
for attempt in 1 2 3 4; do
  git fetch origin "$target_branch"
  if ! git rebase "origin/$target_branch"; then
    git rebase --abort
    echo "Generated output conflicts with remote changes; refusing to overwrite them." >&2
    exit 1
  fi
  if git push origin "HEAD:$target_branch"; then
    exit 0
  fi
  if [ "$attempt" -lt 4 ]; then
    sleep "$((attempt * 4))"
  fi
done
echo "Could not publish generated output after four attempts." >&2
exit 1
