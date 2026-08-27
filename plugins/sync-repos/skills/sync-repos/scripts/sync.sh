#!/usr/bin/env bash
#
# sync.sh — fetch + rebase every git repo under a root directory.
#
# For each repo:
#   1. git fetch --prune origin
#   2. fast-forward the local default branch (main/master, auto-detected) to
#      origin/<default>  (or rebase it if it IS the checked-out branch)
#   3. rebase the currently checked-out branch onto origin/<default>
#      (plain `git rebase`; any merge commits on the branch are linearized)
#
# Repos with a dirty working tree are skipped (use --stash to auto stash/pop).
# When a rebase hits conflicts the rebase is LEFT PAUSED in place so an agent
# (or you) can resolve it and `git rebase --continue`; the repo is reported
# under NEEDS ATTENTION.
#
# Usage:
#   sync.sh [ROOT] [--stash]
#     ROOT     directory containing the repos (default: $REPO_SYNC_ROOT or $PWD)
#     --stash  stash uncommitted changes before rebasing, pop afterward
#
# Output: one line per repo with a status token, then a machine-readable
# summary. Status tokens:
#   UP_TO_DATE  UPDATED  STASHED+UPDATED  DIRTY_UP_TO_DATE  SKIP_DIRTY
#   SKIP_DETACHED  SKIP_NO_UPSTREAM  CONFLICT  DIVERGED_DEFAULT  ERROR

set -u

ROOT="${REPO_SYNC_ROOT:-$PWD}"
STASH=0
for arg in "$@"; do
  case "$arg" in
    --stash) STASH=1 ;;
    -*) echo "unknown flag: $arg" >&2; exit 2 ;;
    *) ROOT="$arg" ;;
  esac
done

if [ ! -d "$ROOT" ]; then
  echo "root is not a directory: $ROOT" >&2
  exit 2
fi
ROOT="$(cd "$ROOT" && pwd)"

# Collected results, one "<status>\t<path>\t<detail>" per line.
RESULTS=""

record() { RESULTS="${RESULTS}${1}\t${2}\t${3:-}"$'\n'; }

# Discover repos: immediate child directories that contain a .git, plus ROOT
# itself if it is a repo.
repos=()
if [ -e "$ROOT/.git" ]; then repos+=("$ROOT"); fi
for d in "$ROOT"/*/; do
  [ -e "${d}.git" ] && repos+=("${d%/}")
done

if [ ${#repos[@]} -eq 0 ]; then
  echo "No git repos found under $ROOT" >&2
  exit 1
fi

echo "Syncing ${#repos[@]} repos under $ROOT"
echo

for repo in "${repos[@]}"; do
  name="$(basename "$repo")"
  # NOTE: unquoted on purpose. An assignment never word-splits a command
  # substitution, so `out=$(...)` and `out="$(...)"` store the same bytes — but
  # with the quotes, bash tracks quote state through the comments INSIDE the
  # substitution, so an odd number of apostrophes in them ("worktree's") makes
  # the parser run to EOF looking for a closing quote and the whole script dies.
  # Note that shellcheck does not catch it; dropping the quotes removes the trap.
  out=$(
    cd "$repo" || { echo "[$name] ERROR (cannot cd into repo)"; exit 9; }

    git rev-parse --git-dir >/dev/null 2>&1 || { echo "[$name] ERROR (not a git repo)"; exit 9; }

    # Need a remote called origin.
    git remote get-url origin >/dev/null 2>&1 || { echo "[$name] SKIP_NO_UPSTREAM (no origin)"; exit 7; }

    # Fetch.
    if ! git fetch --prune origin >/dev/null 2>&1; then
      echo "[$name] ERROR fetch failed"; exit 9
    fi

    # Default branch from origin/HEAD, fall back to main/master.
    default="$(git symbolic-ref --quiet --short refs/remotes/origin/HEAD 2>/dev/null | sed 's@^origin/@@')"
    if [ -z "$default" ]; then
      git remote set-head origin --auto >/dev/null 2>&1
      default="$(git symbolic-ref --quiet --short refs/remotes/origin/HEAD 2>/dev/null | sed 's@^origin/@@')"
    fi
    if [ -z "$default" ]; then
      if git show-ref --verify --quiet refs/remotes/origin/main; then default=main
      elif git show-ref --verify --quiet refs/remotes/origin/master; then default=master
      else echo "[$name] SKIP_NO_UPSTREAM (no default branch)"; exit 7; fi
    fi

    if ! git show-ref --verify --quiet "refs/remotes/origin/$default"; then
      echo "[$name] SKIP_NO_UPSTREAM (origin/$default missing)"; exit 7
    fi

    # Current branch (empty => detached HEAD).
    cur="$(git symbolic-ref --quiet --short HEAD 2>/dev/null || true)"
    if [ -z "$cur" ]; then echo "[$name] SKIP_DETACHED"; exit 4; fi

    # Already mid-rebase/merge? Flag for attention, don't touch.
    if [ -d "$(git rev-parse --git-path rebase-merge 2>/dev/null)" ] || \
       [ -d "$(git rev-parse --git-path rebase-apply 2>/dev/null)" ]; then
      echo "[$name] CONFLICT (rebase already in progress on '$cur')"; exit 5
    fi

    # Dirty working tree: auto-handle the provably-safe cases, leave the rest
    # for human/agent judgment.
    #   - upstream has nothing new            -> DIRTY_UP_TO_DATE (no-op)
    #   - dirty files don't overlap the rebase -> stash, rebase, pop (clean by
    #                                             construction) = STASHED+UPDATED
    #   - dirty files overlap the rebase       -> SKIP_DIRTY (needs judgment),
    #                                             unless --stash forces it
    dirty=0
    [ -n "$(git status --porcelain)" ] && dirty=1
    stashed=0
    if [ "$dirty" -eq 1 ]; then
      if [ "$(git rev-list --count "${cur}..origin/${default}")" -eq 0 ]; then
        echo "[$name] DIRTY_UP_TO_DATE ($cur, nothing upstream to integrate)"; exit 12
      fi
      dirty_files="$( { git diff --name-only; \
                        git diff --cached --name-only; \
                        git ls-files --others --exclude-standard; } | sort -u )"
      incoming_files="$(git diff --name-only "$cur" "origin/$default" | sort -u)"
      overlap="$(comm -12 <(printf '%s\n' "$dirty_files") <(printf '%s\n' "$incoming_files") | sed '/^$/d')"
      if [ "$STASH" -eq 1 ] || [ -z "$overlap" ]; then
        if git stash push -u -m "sync.sh auto-stash" >/dev/null 2>&1; then
          stashed=1
        else
          echo "[$name] ERROR stash failed"; exit 9
        fi
      else
        echo "[$name] SKIP_DIRTY (local edits overlap incoming changes on '$cur'; rerun with --stash to force)"; exit 3
      fi
    fi

    before="$(git rev-parse HEAD)"

    # Update the local default branch if it isn't the one checked out.
    #
    # A bare update-ref would move the branch under ANOTHER worktree that has it
    # checked out: that worktree's HEAD would jump without its index or working
    # tree moving, so every difference between the old and new tip shows up there
    # as spurious local changes. Leave it alone in that case and say so.
    default_pinned=""
    if [ "$cur" != "$default" ]; then
      if git worktree list --porcelain | grep -qx "branch refs/heads/$default"; then
        default_pinned=" (local '$default' left alone: checked out in another worktree)"
      elif git show-ref --verify --quiet "refs/heads/$default"; then
        if git merge-base --is-ancestor "$default" "origin/$default"; then
          git update-ref "refs/heads/$default" "refs/remotes/origin/$default"
        else
          # local default has commits not on origin -> needs a real rebase; flag it.
          echo "[$name] DIVERGED_DEFAULT (local '$default' diverged from origin)"
          [ "$stashed" -eq 1 ] && git stash pop >/dev/null 2>&1
          exit 6
        fi
      else
        git branch "$default" "origin/$default" >/dev/null 2>&1 || true
      fi
    fi

    # Rebase the checked-out branch onto origin/<default>.
    if git rebase "origin/$default" >/dev/null 2>&1; then
      after="$(git rev-parse HEAD)"
      if [ "$stashed" -eq 1 ]; then
        if git stash pop >/dev/null 2>&1; then :; else
          echo "[$name] CONFLICT (stash pop conflicted on '$cur'; the rebase completed, the auto-stash is still in 'git stash list')"; exit 5
        fi
      fi
      if [ "$before" = "$after" ] && [ "$stashed" -eq 0 ]; then
        echo "[$name] UP_TO_DATE ($cur)${default_pinned}"; exit 0
      else
        [ "$stashed" -eq 1 ] && { echo "[$name] STASHED+UPDATED ($cur)${default_pinned}"; exit 11; }
        echo "[$name] UPDATED ($cur rebased onto origin/$default)${default_pinned}"; exit 10
      fi
    else
      # Rebase failed. If a rebase is in progress it's a conflict -> leave it.
      if [ -d "$(git rev-parse --git-path rebase-merge 2>/dev/null)" ] || \
         [ -d "$(git rev-parse --git-path rebase-apply 2>/dev/null)" ]; then
        if [ "$stashed" -eq 1 ]; then
          echo "[$name] CONFLICT (rebasing '$cur' onto origin/$default — left paused; an auto-stash 'sync.sh auto-stash' is HELD and must be popped after the rebase completes)"; exit 5
        fi
        echo "[$name] CONFLICT (rebasing '$cur' onto origin/$default — left paused)"; exit 5
      else
        [ "$stashed" -eq 1 ] && git stash pop >/dev/null 2>&1
        echo "[$name] ERROR rebase failed (no conflict state)"; exit 9
      fi
    fi
  )
  code=$?
  echo "$out"

  # Pull the parenthetical/trailing detail off the repo's status line so it can
  # ride along into the SUMMARY/NEEDS ATTENTION report, not just the live log.
  rest="${out#*] }"
  case "$rest" in
    *" "*) detail="${rest#* }" ;;
    *)     detail="" ;;
  esac

  case $code in
    0)  record UP_TO_DATE "$repo" "$detail" ;;
    10) record UPDATED "$repo" "$detail" ;;
    11) record STASHED+UPDATED "$repo" "$detail" ;;
    12) record DIRTY_UP_TO_DATE "$repo" "$detail" ;;
    3)  record SKIP_DIRTY "$repo" "$detail" ;;
    4)  record SKIP_DETACHED "$repo" "$detail" ;;
    5)  record CONFLICT "$repo" "$detail" ;;
    6)  record DIVERGED_DEFAULT "$repo" "$detail" ;;
    7)  record SKIP_NO_UPSTREAM "$repo" "$detail" ;;
    9)  record ERROR "$repo" "$detail" ;;
    *)  record ERROR "$repo" "unexpected exit $code" ;;
  esac
done

echo
echo "================ SUMMARY ================"
printf '%b' "$RESULTS" | awk -F'\t' 'NF{c[$1]++} END{for(k in c) printf "  %-18s %d\n", k, c[k]}'

attention="$(printf '%b' "$RESULTS" | awk -F'\t' '$1=="CONFLICT"||$1=="DIVERGED_DEFAULT"||$1=="ERROR"||$1=="SKIP_DIRTY"{print}')"
if [ -n "$attention" ]; then
  echo
  echo "============ NEEDS ATTENTION ============"
  printf '%b\n' "$attention" | while IFS=$'\t' read -r status path detail; do
    [ -z "$status" ] && continue
    if [ -n "$detail" ]; then
      echo "  $status  $path  $detail"
    else
      echo "  $status  $path"
    fi
  done
fi
echo "========================================="
