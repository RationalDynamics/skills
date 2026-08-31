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
# The worktree comes from the checkout you are in, so that checkout must be the
# PR's repository. Targets are accepted in the same three forms as
# fetch_pr_context.py, and the fetched head is verified against the forge: a
# target from another repository, or a bare number typed in the wrong checkout,
# is refused rather than silently turned into a same-numbered PR from whatever
# origin happens to be.
#
# Usage:
#   pr_worktree.sh add 519                                   # prints the path
#   pr_worktree.sh add owner/repo#519
#   pr_worktree.sh add https://github.com/owner/repo/pull/519
#   pr_worktree.sh remove 519
#   pr_worktree.sh list             # any worktrees this script left behind
#
# Options:
#   --checkout <path>   repository to work from (default: current directory)
#   --root <path>       where worktrees go (default: $TMPDIR/pr-worktrees)
#
# Always `remove` when finished. `list` exists because a killed review leaves
# them behind, and a stale worktree still holds a lock in the real repository.

set -euo pipefail

CMD=""
TARGET=""
CHECKOUT="$PWD"
# TMPDIR often carries a trailing slash; git normalises paths when it reports
# them, so an unnormalised root here makes `list` match nothing.
_tmp="${TMPDIR:-/tmp}"
ROOT="${_tmp%/}/pr-worktrees"

# One pass over everything, so flags may precede or follow the command: the
# first bare word is the command, the second is the target.
while [ $# -gt 0 ]; do
  case "$1" in
    --checkout) CHECKOUT="${2:?--checkout needs a path}"; shift 2 ;;
    --root) ROOT="${2:?--root needs a path}"; shift 2 ;;
    -*) echo "unknown flag: $1" >&2; exit 2 ;;
    *)
      if [ -z "$CMD" ]; then CMD="$1"; elif [ -z "$TARGET" ]; then TARGET="$1"
      else echo "unexpected argument: $1" >&2; exit 2; fi
      shift ;;
  esac
done

# Usage before anything else, so a bare invocation explains itself rather than
# complaining about whatever directory it was run in.
case "$CMD" in
  add|remove|rm|list) ;;
  *) sed -n '3,36p' "$0" >&2; exit 2 ;;
esac

git -C "$CHECKOUT" rev-parse --git-dir >/dev/null 2>&1 || {
  echo "not a git repository: $CHECKOUT" >&2; exit 2; }

PR=""
TARGET_SLUG=""   # owner/repo, when the target named one
if [ -n "$TARGET" ]; then
  case "$TARGET" in
    http://*|https://*)
      # https://host/owner/repo/pull/N[/anything]
      _rest="${TARGET#*://}"; _rest="${_rest#*/}"
      TARGET_SLUG="$(printf '%s' "$_rest" | cut -d/ -f1,2)"
      PR="$(printf '%s' "$_rest" | cut -d/ -f4)"
      ;;
    */*#*)
      TARGET_SLUG="${TARGET%%#*}"; PR="${TARGET##*#}" ;;
    *)
      PR="$TARGET" ;;
  esac
  case "$PR" in
    ''|*[!0-9]*) echo "cannot parse PR target: $TARGET" >&2; exit 2 ;;
  esac
fi

# owner/repo of the checkout's origin, from any of the URL spellings git accepts.
origin_slug() {
  local url
  url="$(git -C "$CHECKOUT" remote get-url origin 2>/dev/null)" || return 0
  url="${url%.git}"
  printf '%s' "$url" | sed -E 's#^[a-z+]+://##; s#^[^@]+@##; s#^[^:/]+[:/]##'
}

# Name the worktree after the repository as well as the PR, so two repos with
# the same PR number cannot collide in the shared temp root.
slug="$(basename "$(git -C "$CHECKOUT" rev-parse --show-toplevel)")"
dir="$ROOT/${slug}__pr${PR}"

case "$CMD" in
  add)
    [ -n "$PR" ] || { echo "usage: pr_worktree.sh add <PR|owner/repo#PR|url>" >&2; exit 2; }

    # What the forge says this PR points at. This is the authoritative check:
    # comparing repository names cannot survive a rename (origin still spells
    # the old name, and the API redirects), while a head SHA either matches or
    # does not. Fail closed when that authoritative SHA cannot be resolved.
    here="$(origin_slug)"
    command -v gh >/dev/null 2>&1 || {
      echo "cannot resolve ${TARGET_SLUG:-${here:-unknown}}#${PR} head: gh is not installed" >&2
      exit 1
    }
    if ! want="$(gh api "repos/${TARGET_SLUG:-$here}/pulls/${PR}" --jq '.head.sha // empty' 2>/dev/null)" ||
       [ -z "$want" ]; then
      echo "could not resolve ${TARGET_SLUG:-${here:-unknown}}#${PR} head from the forge" >&2
      exit 1
    fi

    if [ -d "$dir" ]; then
      # Reuse only a worktree that still points at the PR's current head: one
      # left over from before a force-push is a wrong review, not a fast one.
      have="$(git -C "$dir" rev-parse HEAD 2>/dev/null || true)"
      if [ -n "$want" ] && [ -n "$have" ] && [ "$want" != "$have" ]; then
        echo "existing worktree is at ${have}, but ${TARGET_SLUG:-$here}#${PR} is at ${want}" >&2
        echo "run: pr_worktree.sh remove ${PR}   (then add again)" >&2
        exit 1
      fi
      echo "$dir"; exit 0
    fi

    # Fetch into FETCH_HEAD only: writing refs/heads/* would create a branch in
    # the shared repository, which is the thing this script exists to avoid.
    git -C "$CHECKOUT" fetch -q origin "pull/${PR}/head" || {
      echo "could not fetch pull/${PR}/head from origin (${here:-unknown})" >&2; exit 1; }
    sha="$(git -C "$CHECKOUT" rev-parse FETCH_HEAD)"

    # The case prose cannot catch: a bare number typed in an unrelated
    # checkout, where the fetch succeeds and hands back a plausible tree from
    # the wrong repository's PR of the same number.
    if [ "$want" != "$sha" ]; then
      echo "fetched ${sha}, but ${TARGET_SLUG:-$here}#${PR} is at ${want}" >&2
      echo "this checkout is not the PR's repository — not creating a worktree" >&2
      echo "run with --checkout <a clone of ${TARGET_SLUG:-that repository}>" >&2
      exit 1
    fi

    mkdir -p "$ROOT"
    git -C "$CHECKOUT" worktree add --detach -q "$dir" "$sha"
    echo "$dir"
    ;;
  remove|rm)
    [ -n "$PR" ] || { echo "usage: pr_worktree.sh remove <PR|owner/repo#PR|url>" >&2; exit 2; }
    if [ -d "$dir" ]; then
      # Refuse to discard work: a dirty worktree means something was written
      # here that the review did not account for. Say so rather than deleting.
      if [ -n "$(git -C "$dir" status --porcelain 2>/dev/null)" ]; then
        echo "worktree is dirty, not removing: $dir" >&2
        git -C "$dir" status --short >&2
        exit 1
      fi
      git -C "$CHECKOUT" worktree remove "$dir"
    fi
    git -C "$CHECKOUT" worktree prune
    echo "removed $dir"
    ;;
  list)
    # git reports resolved paths. On macOS $TMPDIR is a symlink (/var/... ->
    # /private/var/...), so comparing against the unresolved root matches
    # nothing. Check both spellings.
    root="${ROOT%/}"
    root_p="$root"
    [ -d "$root" ] && root_p="$(cd "$root" && pwd -P)"
    git -C "$CHECKOUT" worktree list --porcelain \
      | awk -v a="$root" -v b="$root_p" \
        '/^worktree /{p=$2; if (index(p, a) == 1 || index(p, b) == 1) print p}'
    ;;
esac
