---
name: sync-repos
description: Sync all git repos under a directory — fetch, fast-forward the default branch, and rebase the checked-out branch onto origin's default. Runs a bundled script for the mechanical part, then resolves any rebase conflicts (auto-fixing obvious ones, asking the user about anything non-obvious). Use when the user wants to "sync my repos", "pull everything", "update all repos", or rebase a workspace of clones.
---

# sync-repos

Sync every git repo under a workspace directory and clean up any conflicts the
automated rebase couldn't handle.

## Step 1 — run the script

The sync script is bundled at `scripts/sync.sh` **next to this SKILL.md**. Resolve
it relative to this file and run it by absolute path — never assume the working
directory is the installed plugin directory.

```bash
bash "<dir-of-this-SKILL.md>/scripts/sync.sh" "$WORKSPACE_ROOT"
```

The first argument is the directory containing the repos. Resolve it in this order:

1. an explicit path the user named in the request;
2. `$REPO_SYNC_ROOT`, if set;
3. the current directory, if it directly contains git repos;
4. otherwise ask the user which directory to sync — do not guess.

Add `--stash` only if the user asked to include uncommitted changes.

The script fetches, fast-forwards each repo's default branch, and rebases the
checked-out branch onto `origin/<default>`. It never pushes and never touches a
dirty working tree (unless `--stash`). Read the `SUMMARY` and `NEEDS ATTENTION`
sections of its output.

## Step 2 — handle what needs attention

The script leaves any conflicted rebase **paused in place** (it does not abort
it). Work through each repo listed under `NEEDS ATTENTION`:

### CONFLICT
A rebase (or stash pop) is paused with conflicts. `cd` into the repo and:

1. `git status` and `git diff` to see the conflicted files and understand both
   sides. `git log --oneline -5 origin/<default>` and `git rebase --show-current-patch`
   help show what the incoming commit was trying to do.
2. **Check whether the branch is already fully superseded before resolving
   anything.** This is common with squash-merged PRs whose local branch/worktree
   never got cleaned up: `git ls-remote origin <branch>` (empty = remote branch
   already deleted, i.e. merged) and `git diff origin/<default> <branch> -- <the
   conflicted files>` (empty, or only unrelated churn, = nothing left to land).
   If confirmed superseded:
   - **Worktree checkout:** `git rebase --abort`, then from the *main* clone
     `git worktree remove <path>` and `git branch -d <branch>` (falls back to
     `-D` only after confirming with the user, since a squash-merged branch tip
     is never an ancestor of main and `-d` will refuse it).
   - **Non-worktree clone on that branch:** `git rebase --abort`, then treat it
     like any other fully-merged branch in the branch-deletion pass below.
   Either way, tell the user what you found and what you removed — don't do
   this silently.
3. **Resolve the obvious ones yourself**, e.g.:
   - lockfiles / generated files (`package-lock.json`, `poetry.lock`, `*.sum`,
     `Cargo.lock`): regenerate or take the side that matches the rebased deps,
     then re-run the lock tool if quick.
   - import-ordering, both-sides-added imports, changelog/whitespace/formatting
     collisions: combine both sides.
   - a clean "both added the same thing slightly differently" where intent is
     unambiguous.
   Then `git add <files>` and `git rebase --continue` (repeat per step).
   Generated files (protobuf/gRPC output, OpenAPI/Swagger specs, sqlc/codegen
   output) are a special case of "obvious": if the *source* the generator reads
   from merged cleanly, don't hand-merge the generated diff — resolve by
   rerunning the generator and staging its output.
4. **Ask the user** before guessing on anything non-obvious — overlapping edits
   to the same logic, semantic conflicts, deletions vs. modifications, two
   independently-designed features that both claim to be "the" successor to
   something, anything where picking a side could drop someone's work or paper
   over a real design collision. Show the conflict, explain both sides and your
   read of it, and propose an option. Do not invent business logic to make a
   conflict go away.
5. If a repo turns out to be more involved than the others, `git rebase --abort`
   to restore it to its pre-sync state and flag it for the user rather than
   leaving it half-done.
6. Check the project's own agent-instruction file (`CLAUDE.md`, `AGENTS.md`, or
   equivalent) for protected-file rules before touching a conflict (e.g. "never
   modify `.pre-commit-config.yaml` without approval") — those override the
   "resolve obvious ones yourself" default, even when the resolution itself is
   mechanically obvious. Ask first.

### DIVERGED_DEFAULT
The local default branch has commits not on `origin/<default>`, so the script
didn't fast-forward it. `cd` in, inspect with
`git log --oneline origin/<default>..<default>`, and rebase the default branch
onto origin (`git checkout <default> && git rebase origin/<default>`), handling
conflicts as above. Ask if the local commits look like they shouldn't be there.

### ERROR
Something failed (fetch, unexpected rebase error). `cd` in, reproduce the
failing command to see the real error, and report it. Common causes: no network
/ auth, a detached or corrupted state.

### SKIP_DIRTY
The script already handled the easy dirty repos for you: ones with nothing
upstream are reported `DIRTY_UP_TO_DATE`, and ones whose uncommitted files don't
overlap the incoming changes were safely stashed/rebased/popped and reported
`STASHED+UPDATED`. A repo only reaches `SKIP_DIRTY` when its local edits
**overlap the files the rebase would touch** — i.e. a clean stash pop isn't
guaranteed, so it needs judgment. For each one:

1. `cd` in and look at both sides: `git diff` (and `git diff --cached`) for the
   local edits, `git diff <cur>..origin/<default>` for what's incoming, focusing
   on the overlapping files.
2. **If the overlap is trivial** (the incoming change and the local edit clearly
   don't actually collide, or the local edit is throwaway you can re-apply):
   `git stash push -u`, `git rebase origin/<default>`, `git stash pop`, and
   resolve the pop conflict if it's obvious (see CONFLICT above).
3. **Otherwise leave it for the user.** If you started a stash/rebase and it got
   messy, restore the pre-sync state (`git rebase --abort` if mid-rebase, then
   `git stash pop` to bring the changes back) and report it. Never discard
   uncommitted work to make a sync succeed.

## Step 3 — branch-deletion pass (only if the user asks)

Not part of the default sync — run it when the user separately asks to "clean
up branches", "delete merged branches", or similar. For each local branch
across the workspace (worktree checkouts included):

1. Skip the repo's default branch and whatever's currently checked out
   elsewhere if switching would disrupt other work.
2. A branch is safe to delete when `git ls-remote origin <branch>` is empty
   (remote already deleted, i.e. merged/closed) **and**
   `git diff origin/<default> <branch>` is empty or only unrelated churn (the
   same check as the superseded-branch case in CONFLICT above).
3. Worktree checkouts: `git worktree remove <path>` first, then delete the
   branch. Plain local branches: `git branch -d <branch>` (let it refuse and
   report rather than forcing past a real "not merged" refusal — squash-merges
   need the diff check above to confirm safety, not `-D`).
4. List what you're about to delete and get a one-time confirmation before
   deleting anything, rather than asking per-branch — this is a bulk cleanup
   operation.

## Step 4 — report

Give a short summary: how many repos were up-to-date, how many were updated
(including dirty repos you safely stashed/rebased/popped), which were left
skipped (dirty-with-overlap, detached, no upstream) and why, and the outcome of
each repo that needed attention (resolved automatically, resolved with
confirmation, aborted, or still waiting on the user). If a branch-deletion pass
ran, include what was removed.

## Notes
- Default branch is auto-detected per repo from `origin/HEAD` (main, master, …).
- `SKIP_DETACHED` / `SKIP_NO_UPSTREAM` are intentional no-ops; mention them but
  don't act unless the user asks. `DIRTY_UP_TO_DATE` / `STASHED+UPDATED` are the
  script's automatic dirty-repo handling — just report them. `SKIP_DIRTY` is the
  leftover that needs the judgment step above.
- The script is idempotent — safe to re-run after resolving conflicts to confirm
  everything is clean.
- The workspace root does not need to be a git repo itself; the script walks its
  immediate children looking for repos.
