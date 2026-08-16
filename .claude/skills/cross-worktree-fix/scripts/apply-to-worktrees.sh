#!/usr/bin/env bash
# Apply a single patch file to every FinancialDevelopment worktree that contains
# the touched paths, committing per-worktree with the same message.
# Usage: apply-to-worktrees.sh <canonical.patch> "<commit message>"
set -euo pipefail

REPO_ROOT="C:/Users/bottl/FinancialDevelopment"
PATCH_FILE="${1:?usage: apply-to-worktrees.sh <canonical.patch> \"<commit message>\"}"
COMMIT_MSG="${2:?usage: apply-to-worktrees.sh <canonical.patch> \"<commit message>\"}"

cd "$REPO_ROOT"
if [ ! -f "$PATCH_FILE" ]; then
  echo "patch file not found: $PATCH_FILE" >&2
  exit 1
fi

# Files touched by the patch (used to skip worktrees that lack them).
# Parse 'diff --git a/... b/...' headers.
TOUCHED=()
while IFS= read -r line; do
  case "$line" in
    'diff --git '*)
      # take the 'b/<path>' side, strip prefix and possible quoting/trailing
      p="${line#diff --git a/}"
      p="${p#*/}"
      p="${p%% *}"
      TOUCHED+=("$p")
      ;;
  esac
done < <(grep '^diff --git' "$PATCH_FILE")
if [ "${#TOUCHED[@]}" -eq 0 ]; then
  echo "no diff --git headers found in patch; aborting" >&2
  exit 1
fi

applied=0
skipped=0
while read -r wt branch; do
  [ -n "$wt" ] || continue
  case "$wt" in
    */FinancialDevelopment) continue ;; # skip the primary repo itself
  esac
  # Skip if any touched file is absent in this worktree
  missing=0
  for p in "${TOUCHED[@]}"; do
    if [ ! -f "$wt/$p" ]; then
      missing=1
      break
    fi
  done
  if [ "$missing" -eq 1 ]; then
    echo "SKIP (missing touched file): $wt [$branch]"
    skipped=$((skipped+1))
    continue
  fi
  if (cd "$wt" && git apply --check "$PATCH_FILE" 2>/dev/null); then
    (cd "$wt" && git apply "$PATCH_FILE" && git add -A && git commit -m "$COMMIT_MSG")
    echo "APPLIED: $wt [$branch]"
    applied=$((applied+1))
  else
    echo "SKIP (patch does not apply / diverged): $wt [$branch]"
    skipped=$((skipped+1))
  fi
done < <(git -C "$REPO_ROOT" worktree list --porcelain | awk '/^worktree /{wt=$2} /^branch /{print wt, substr($2,12)}')

echo "Done. applied=$applied skipped=$skipped"
