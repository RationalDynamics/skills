#!/usr/bin/env bash
#
# pr_worktree.sh — check out a PR's code without touching the user's checkout.
#
# Reviewing sometimes needs the PR's files on disk: to run one test, to read a
# file the diff only partially shows, to check a path the patch does not carry.
# The obvious move — `git fetch origin pull/N/head:pr-N` or a checkout in the
# working tree — writes into a tree that is not yours. Other sessions and the
# user may be working in it, a stray branch outlives the review, and switching
# HEAD under someone mid-edit is worse than useless.
#
# So: a DETACHED worktree, outside the repository, under a temp directory. No
# branch is created (`--detach`), the primary checkout's HEAD, index and working
# tree are never touched, and `remove` puts everything back.
#
# Usage:
#   pr_worktree.sh add 519          # prints the worktree path on stdout
#   pr_worktree.sh remove 519
#   pr_worktree.sh list             # any worktrees this script left behind
#
# Options:
#   --repo <path>   repository to work from (default: current directory)
#   --root <path>   where worktrees go (default: $TMPDIR/pr-worktrees)
#
# Always `remove` when finished. `list` exists because a killed review leaves
# them behind, and a stale worktree still holds a lock in the real repository.

set -euo pipefail

CMD="${1:-}"; shift || true
PR=""
REPO="$PWD"
# TMPDIR often carries a trailing slash; git normalises paths when it reports
# them, so an unnormalised root here makes `list` match nothing.
_tmp="${TMPDIR:-/tmp}"
ROOT="${_tmp%/}/pr-worktrees"

while [ $# -gt 0 ]; do
  case "$1" in
    --repo) REPO="$2"; shift 2 ;;
    --root) ROOT="$2"; shift 2 ;;
    -*) echo "unknown flag: $1" >&2; exit 2 ;;
    *) PR="$1"; shift ;;
  esac
done

git -C "$REPO" rev-parse --git-dir >/dev/null 2>&1 || {
  echo "not a git repository: $REPO" >&2; exit 2; }

# Name the worktree after the repository as well as the PR, so two repos with
# the same PR number cannot collide in the shared temp root.
slug="$(basename "$(git -C "$REPO" rev-parse --show-toplevel)")"
dir="$ROOT/${slug}__pr${PR}"

case "$CMD" in
  add)
    [ -n "$PR" ] || { echo "usage: pr_worktree.sh add <PR>" >&2; exit 2; }
    if [ -d "$dir" ]; then echo "$dir"; exit 0; fi
    # Fetch into FETCH_HEAD only: writing refs/heads/* would create a branch in
    # the shared repository, which is the thing this script exists to avoid.
    git -C "$REPO" fetch -q origin "pull/${PR}/head" || {
      echo "could not fetch pull/${PR}/head from origin" >&2; exit 1; }
    sha="$(git -C "$REPO" rev-parse FETCH_HEAD)"
    mkdir -p "$ROOT"
    git -C "$REPO" worktree add --detach -q "$dir" "$sha"
    echo "$dir"
    ;;
  remove|rm)
    [ -n "$PR" ] || { echo "usage: pr_worktree.sh remove <PR>" >&2; exit 2; }
    if [ -d "$dir" ]; then
      # Refuse to discard work: a dirty worktree means something was written
      # here that the review did not account for. Say so rather than deleting.
      if [ -n "$(git -C "$dir" status --porcelain 2>/dev/null)" ]; then
        echo "worktree is dirty, not removing: $dir" >&2
        git -C "$dir" status --short >&2
        exit 1
      fi
      git -C "$REPO" worktree remove "$dir"
    fi
    git -C "$REPO" worktree prune
    echo "removed $dir"
    ;;
  list)
    # git reports resolved paths. On macOS $TMPDIR is a symlink (/var/... ->
    # /private/var/...), so comparing against the unresolved root matches
    # nothing. Check both spellings.
    root="${ROOT%/}"
    root_p="$root"
    [ -d "$root" ] && root_p="$(cd "$root" && pwd -P)"
    git -C "$REPO" worktree list --porcelain \
      | awk -v a="$root" -v b="$root_p" \
        '/^worktree /{p=$2; if (index(p, a) == 1 || index(p, b) == 1) print p}'
    ;;
  *)
    sed -n '3,28p' "$0" >&2
    exit 2
    ;;
esac
