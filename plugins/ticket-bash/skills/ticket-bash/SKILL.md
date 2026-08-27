---
name: ticket-bash
description: >
  Autonomous backlog burndown — take unclaimed, unambiguous tickets from ticket to draft PR with
  no human round-trips. Pull a batch, triage for ambiguity and access blockers, record verdicts in
  a durable queue file, then work the actionable batch with parallel sub-agents: plan every ticket
  at once (the ambiguity gate), then implement and review in parallel (each in its own worktree),
  verifying and opening a draft PR per ticket. After the PR, drive CI and the automated review bots
  to green by iterating on their findings. Use when asked to "do a ticket bash", "burn down the
  backlog", "close tickets autonomously", or to address PR review comments on work from a prior run.
---

# Ticket Bash

A repeatable procedure for autonomously burning down an unclaimed backlog. The output of each
pickup is a **draft PR** linked to its ticket.

This runs unattended for long stretches, so two things matter more than anything else: a
**durable queue file** so an interruption never costs triage, and a **planning gate** that kills
bad tickets before they reach code.

## Configure before the first run

Nothing below assumes a particular workspace. Resolve each setting, and **ask rather than guess**
when the answer isn't discoverable:

| Setting | How to resolve |
|---|---|
| Workspace root | `$TICKET_BASH_ROOT`, else the current directory if it holds the repo clones, else ask. |
| Queue file | `$TICKET_BASH_QUEUE`, else `<workspace root>/ticket-bash-queue.md`. |
| Teams / projects to scan | Ask on the first run and record the answer in the queue file's header. |
| Per-repo verify commands | **Discover them** — read the repo's task runner (`justfile`, `Makefile`, `command.sh`, `package.json`, `pyproject.toml`) and its agent-instruction file. Never assume a command exists. Record what worked in the queue file. |
| Branch / PR conventions | Read them off the repo's recent history (`git log`) and its contributing docs. |
| Git host CLI | Must be authenticated with push rights on every target repo. Verify once, up front. |
| Review bots | Discover which bots comment on that repo's PRs (see step 3b) — they differ per repo, and some skip drafts entirely. |

Record the resolved configuration in the queue file header so the next run doesn't re-derive it.

## Eligibility bar

Take a ticket only if BOTH hold:
- **Unambiguous** — no product/design question, no follow-up needed; it's clear what "done" is.
- **Self-completable** — code → local test → push → PR with the access you have. No work that
  needs credentials, permissions, or cloud-console access you lack.

Skip on sight: design/research tickets, vendor/ops/data-collection, infra-console and
terraform work.

**A "needs grooming" label is NOT a skip** — but treat it skeptically. The label means the team has
not yet reviewed or refined the ticket, so the premise, scope, and "done" definition are unvetted.
Apply the ambiguity bar *harder*: if it's genuinely unambiguous and self-completable as written,
it's eligible (don't assume the un-reviewed text is wrong — verify it against the code). But the
common case is that ungroomed tickets are large, vague, or design-laden, and those still fail the
bar on their own merits, not because of the label. (RD case, DEM-485: ungroomed AND a multi-day,
design-laden hot-path refactor → declined on the size and ambiguity grounds, not the label.)

### Ambiguity / skip smells (grow this list)
- Ticket text hedges with "worth a product confirmation", "need to confirm", "decide whether" →
  AMBIGUOUS. (DEM-285: scoping a read surface "could break workflows" → needs a product call.)
- Removing a demo or dev affordance while the product is still demo-stage is **premature** — defer.
  (DEM-270: "demo mode is a good default until we're in real production".) When in doubt whether a
  cleanup is wanted *now*, it's ambiguous.
- "Design ticket, not implementation" in the title or body → skip.
- A bug with no clear root cause until you've investigated → pull it, but the **plan** sub-agent is
  the gate: if root cause stays unclear, demote to AMBIGUOUS and move on.
- **Self-declaring decision tickets.** "A decision ticket, not an implementation ticket",
  "whoever picks this up should triage first, then create subtickets" → SKIP on sight. But read on:
  such a ticket often names sub-items it calls **"defects under any posture"**. Those ARE a clean
  autonomous slice once the user confirms the scope-down — ask, don't assume.
- **A ticket that presents options A/B/C is ambiguous, but it is a CHEAP ask when the user is
  present** and the author is the user. Prefer asking over deferring when: the author is the user,
  the options are enumerated in the ticket, and the diff after the decision is small. (IAO-40 went
  from AMBIGUOUS to a shipped plan with one question.)
- **"Blocked by X" in the ticket body is usually true — verify X before planning.** Check the
  blocker's status in the tracker AND whether its code is actually on the default branch.

## Conventions to settle up front

- **Assignee is not automatically a skip.** Tickets assigned to *the user* are eligible. Default
  working order when the user doesn't say otherwise: *assigned-to-user + Todo* > *assigned-to-user +
  Backlog* > *unassigned*. Tickets assigned to **someone else** ARE a skip.
- **Claiming: comment-only.** Do not change the tracker's assignee or status. Record verdicts and PR
  links as comments on the ticket.
- **PRs open as drafts.** Blocked-but-worth-shipping work gets a `WIP:` title prefix with the
  blocker stated in the body.
- **Incidental tickets** (unrelated bugs or gaps found while working): file them with the team's
  grooming label and a `BUG:` prefix where it applies. They're eligible next round.
- **Branch convention:** take it from the repo. A common shape is
  `<type>/<TICKET-ID>_<short-desc>`, squash-merge, author is not the approver — so you cannot
  self-approve, which is fine; just open the PR.

## Procedure

### 0. Resume from the durable queue FIRST (do this before anything else)

At the very start of every run, **read the queue file if it exists.** It carries forward all prior
triage so you don't redo it:

- Rows marked `DONE`, `PARTIAL`, `SKIP`, `DEFER`, `AMBIGUOUS`, or `BLOCKED` are **already handled** —
  do NOT re-triage, re-plan, or re-pick them. Honor the recorded reason.
- Start from the file's "Remaining ACTIONABLE candidates" list, then triage only tickets not already
  in the file.
- Tickets you shipped usually get auto-assigned by the tracker's git integration once a PR
  references the ID, so they drop out of an `assignee = null` query on their own. But deferred and
  skipped tickets stay unassigned and WILL reappear in the pull — the queue file is the only thing
  that stops you re-assessing them. Read it.

If the file does not exist, this is a fresh start: create it with the resolved configuration in its
header and proceed.

### 1. Pull and triage a batch

Re-pull unassigned backlog + unstarted issues from the configured teams (this catches newly filed
tickets, including ones you filed yourself). Cross-reference against the queue file; for each
candidate **not already recorded**, append a row:

`id | title | priority | verdict (ACTIONABLE / AMBIGUOUS:<reason> / BLOCKED:<reason> / SKIP) | repo | notes`

The queue file is what survives an interruption — write verdicts there **before** picking anything
up, and keep updating each row's verdict and PR link as you progress.

### 2. Plan the whole batch IN PARALLEL (this is the ambiguity gate)

Don't plan one ticket, implement it, then plan the next — that leaves agents idle. Instead:

- **Isolate every ACTIONABLE ticket up front.** Fetch, then create one worktree per ticket. Put
  worktrees **outside the workspace root**, in a scratch location. A worktree nested under the
  workspace inherits the workspace's agent settings, including any pre-approved-push permissions —
  that combination is how an RD run in 2026-08 dropped four reviewed commits.
- **Launch one Plan sub-agent per ticket in a single message** so they run concurrently, within the
  host's concurrency limit. Each reads its ticket plus the code and returns a concrete plan and an
  explicit ACTIONABLE / AMBIGUOUS verdict.
- This is the **second ambiguity gate**: any ticket that comes back AMBIGUOUS (underspecified,
  needs product input, wrong premise) drops out here — comment on it, mark AMBIGUOUS/DEFER in the
  queue, and reclaim its worktree. Reclaim only worktrees that are clean, not mid-rebase, and hold
  no unpushed commits; never force-remove one by hand. Do all of this before any implementation, so
  a bad plan never reaches code.

### 3. Implement and review IN PARALLEL, then verify and PR per ticket

Each ticket has its own worktree, so they don't collide — fan out:

- **Implement:** launch the implement sub-agents for all still-ACTIONABLE tickets concurrently (one
  message, multiple agent calls), each handed its approved plan. A cross-repo ticket (backend +
  frontend) can be two parallel implement agents.
- **Review:** as each implementation returns, launch its review sub-agent (correctness plus
  convention fit). Reviews for different tickets run in parallel too. Address findings — small
  fixes inline, re-spawn the implementer for larger ones.
- **Verify (the real bottleneck):** run the repo's test suite. Whether two suites can run at once
  depends on the repo's test isolation — see the resource note in step 5. Spin up servers and
  manual-test where the change warrants it. Proceed per ticket only when confident.
- **Draft PR and record:** open the draft PR (small and single-purpose; a backend+frontend pair
  becomes two linked draft PRs noting release ordering). Comment the PR link on the ticket. Update
  the queue row. Remove the worktree.

Pipelining beats a strict barrier: kick off implement as soon as a plan lands rather than waiting
for the whole planning batch, and start each review and verify the moment its implementation
returns. Order the PRs by priority when several finish together.

### 3b. After the draft PR: drive CI and automated review to GREEN (iterate)

A draft PR is **not** the finish line. **Discover which bots review that repo before you wait on
any of them** — they differ per repo, they post on **different API endpoints**, and polling only one
is the easy mistake:

- **Issue-comment bots** post on the PR's *issue* comments endpoint. A review bot of this shape
  usually ends its comment with an explicit verdict line. Fetch the *latest* such comment by
  `created_at`.
- **Inline-review bots** (code-scanning and code-quality bots) post **review comments on diff
  lines** — a *different* endpoint from issue comments. These fire on lines your diff **touched**,
  so a fix that re-touches a pre-existing idiom can surface a finding that was always latent. Each
  inline comment carries an `id` you reply to.
- Tracker link-back bots are noise — ignore them.
- **Some bots skip draft PRs entirely** and post nothing but "review skipped: draft pull request".
  On such a repo this whole bot loop does not fire: don't sit waiting for a verdict that will never
  come. Drive the CI checks to green instead, and check the inline endpoint (usually empty on drafts).

This is an **iterative loop, not one-shot: every push re-triggers every bot**, and re-reviews
routinely surface NEW, often deeper findings — and can be mildly non-deterministic (one RD push
produced both a PASS and a BLOCK comment). Each round, re-pull every stream, read the latest by
`created_at`, fix, push, re-check. Repeat until a round comes back clean on all of them. Bound it
to a few rounds — if it won't converge, summarize and surface rather than churn.

**Whack-a-mole circuit-breaker (important):** if the SAME concern recurs across 2 rounds, or a
second round surfaces an adjacent finding in the same area, STOP patching. The fixes are probably
locally correct but the underlying *primitive* is wrong — an inference or heuristic where a designed
mechanism belongs. Dispatch a read-only Plan agent to read the ticket, the full diff, and the WHOLE
chronological review history, and report how it would have built the change from scratch. Present a
decision-grade recommendation — usually "redesign" or "split and defer the hard part to a new
ticket" — and get the user's call before any rewrite. Verify the agent's load-bearing assumptions
against real usage (entrypoints, scripts, docs) before acting. Two RD cases resolved this way
(DEM-484 select-by-content → wipe-and-recreate; DEM-279 inferred liveness → post-fanout-only plus a
deferred ticket) and both *retired* code rather than adding to it.

Per finding:

1. **Rebase first.** Before editing a PR branch to fix findings, fetch and rebase onto the default
   branch, then force-push with lease. The default branch moves during a bash; rebasing locally
   keeps the fix on current code so CI doesn't have to.
2. **Assess against the code.** A finding can be right, wrong, or right-but-out-of-scope. Don't
   reflexively apply. A bot's own PASS/BLOCK verdict is a triage hint, not gospel — PASS findings are
   often still worth fixing, because they can undercut the PR's own stated contract.
3. **Valid and tractable → fix it**, with a focused test that *fails without the fix* (verify by
   temporarily reverting the fix and running that one test), run the suite, push. **The bot's
   suggested changeset can itself be WRONG** — verify it against actual behavior before applying.
   (Seen: a bot flagged an intentional MRO-skipping `super(Mixin, self)` call and suggested a plain
   `super()`, which would have routed through the skipped mixin's filter and silently dropped rows.
   The deliberate skip was correct; the fix went in a third way, with a reply explaining why.)
4. **Judged a false positive or won't-fix → REPLY to the bot** explaining why. Silently ignoring is
   not allowed — a human reviewer cannot tell "didn't see it" from "decided it's wrong". Reply on
   the same endpoint the finding came from: a comment for issue-comment bots, a threaded reply
   against the comment `id` for inline bots. Also reply when you fixed it a *different* way than
   suggested, so a later reviewer doesn't "correct" it back to the broken suggestion.
5. **Design-laden, security, or reworks-the-PR's-approach → escalate to the user** with options;
   don't unilaterally decide. (Seen: "ACL-scope these reads" *also* broke an intended same-org
   collaboration feature. Only a failing test — one expecting 409, not 404 — revealed the conflict,
   and the resolution, scope reads only and keep mutations org-wide, was a product call.)
   Corollary: after any query-scoping change, run the BROAD suite and read what breaks as a design
   signal, not as noise.
6. **Stacked PRs:** if PR B's finding is "feature X is deferred until sibling PR A merges", rebase B
   onto A so it inherits X, convert the deferral note into a real regression assertion, and leave a
   PR comment documenting the stack and merge order.

### 4. Stop conditions

Running out of unambiguous tickets is a fine, expected outcome — stop and summarize. If a sub-agent
stalls or a local stack won't come up cleanly, treat it as a blocker: comment, record, move on
rather than thrashing.

**Don't pause between rounds to ask "should I keep going?"** Once the bash is running, keep pulling,
triaging, and shipping autonomously until the actionable queue is genuinely empty (or every
remaining ticket is ambiguous or blocked), then summarize. Re-triage to refill the batch on your
own. Only stop early for a real blocker or an explicit stop from the user.

### 5. Autonomous command safety — NEVER run a command that can hang unbounded

This runs unattended, so a single hang stalls the whole bash. Every command needs a timeout or a
heartbeat:

- **Always set an explicit timeout** on calls that could hang: test suites, server boots, network
  and git-host calls, pushes, dependency syncs, container builds. Pick a bound generous enough for
  the real work but finite. A command with no timeout that wedges is a dead run.
- **Long commands (multi-minute): run them in the background** and poll a heartbeat rather than
  blocking. The harness re-invokes you when a background command exits, so you can do other work
  meanwhile.
- **Do NOT pipe a long-running command through `tail` or `grep`** when backgrounding — those buffer
  and emit nothing until EOF, so the output file stays empty and you get zero progress signal.
  Redirect full output to a file and heartbeat by tailing that file, or read the container's logs
  where the test runner streams there live. That tail is your "still making progress vs. wedged"
  check.
- **Servers: start them detached and poll a health endpoint with a bounded wait.** Prefer the
  repo's own wait-for-health command over tailing a foreground boot that may never become healthy.
  If health never comes up within the bound, treat it as a blocker, record it, and move on. Always
  tear the stack back down.
- **Skipping a pre-push hook is allowed only for a failure you PROVED is pre-existing.** The bar is
  evidence, not convenience: reproduce the failure on a pristine default-branch checkout first,
  then state the skip and its reproduction in the PR body. Never use it to skip a gate your own diff
  broke. Prefer the hook's own targeted skip mechanism over disabling verification wholesale — and
  note that a push guard may refuse to skip verification and force-push at once, since that
  disables every safety check simultaneously. When that happens, don't rebase: reset to the remote
  branch and cherry-pick the fix so a plain fast-forward push works.
- **Keep the machine awake for the whole run.** If the host sleeps, in-flight sub-agents die
  mid-response and you lose their work in progress. Start the OS's stay-awake utility detached at
  the top of the run so it never holds a background-task slot, and kill it when the bash ends.
  Recovery when it happens anyway: the agent's commit usually survives — check the worktree, then
  **resume the agent with a message rather than re-spawning it**, so it keeps its context, and tell
  it what changed while it was gone (rebases, new environment findings).
- **Two full suites at once can be slower than running them in sequence.** Measured on one RD repo,
  a suite that took ~4 min alone took 33 min for two concurrent runs with workers capped, and ~2
  hours for two concurrent runs at default workers. More RAM did not fix it: the bottleneck is CPU
  oversubscription from doubled test-runner workers. Before running suites in parallel, check how
  the repo picks its worker count; if it scales to core count, queue the suites one at a time. Fast
  suites (seconds) are fine anytime.
- **Clean up as you go.** Remove finished worktrees AND tear down their container stacks. Orphaned
  stacks from removed worktrees pile up and starve later test runs.

## Environment gotchas worth checking in any repo

These were learned on RD repos, but each one generalizes — check the equivalent before you trust a
run:

- **Use worktrees so an in-flight checkout on a shared repo is never disturbed.** A shared clone may
  move under you while you work (someone else pulls or merges). Always branch each worktree off a
  freshly fetched default branch; never operate on the primary checkout, and never assume its HEAD
  is stable.
- **Pushing is the claim, not branching.** A tracker's git integration auto-links and auto-assigns
  the ticket as soon as a branch or commit references the ticket ID. Creating a worktree and branch
  is free; the push is what claims the ticket. So keep ticket-ID branches local until implementation
  actually starts — a ticket that fails the plan gate must never have been pushed, so it stays
  genuinely available. Plan-gate output goes on the ticket as a comment; comments do not assign.
- **A worktree may silently skip the repo's pre-commit hook** when `core.hooksPath` points at the
  primary checkout's hooks directory. Verify the hook actually fires in the worktree; if it doesn't,
  run the suite yourself before pushing.
- **The test recipe is often not the CI gate.** Running the unit suite is not enough if CI also runs
  type-checking and lint jobs that no test exercises. Find the repo's lint aggregator, and when it
  fails read the *prerequisite* job that failed, not the aggregator. Where a linter takes build tags
  or feature flags, run it once per tag your diff adds — the default build skips tagged files, so
  their failures surface only under the tag.
- **Code-review-spawned tickets often reference files that live on an UNMERGED base PR.** Before
  planning any "from code review of PR #N" ticket, check whether PR #N merged; if the referenced
  files aren't on the default branch, the ticket is BLOCKED until that base PR lands — branching off
  the default branch gives you an empty target.
- **A "workaround applied locally, pending owner decision" ticket may already be resolved.** Verify
  the bug still reproduces on the current default branch before planning.
- **A passing environment check can be a false positive from an earlier manual workaround.** One RD
  ticket's check showed a bucket present only because someone had created it by hand months
  earlier, so the fix was a no-op and proved nothing. Re-verify with a *fresh* value that could only
  exist if the new code ran.
- **Never recreate or rebuild shared infrastructure to verify a ticket.** A running stack belongs to
  whoever's primary checkout started it, and a worktree may share the default container project name
  while its compose file differs. Use the no-dependencies flag when running one-off containers. If a
  test genuinely needs a rebuild, say so in the PR body and leave it to the author — a draft PR that
  states honestly what was NOT verified beats one that trashed someone's environment.
- **A bundled ticket ("fix C4 and C14") can be split.** If one sub-fix is solid and the other turns
  out subtler or wrong, ship the good one as a complete, non-WIP draft PR scoped to it, and bounce
  the other back to grooming with the analysis. Don't block a good fix on a bad one.
- **Repos with gitignored inputs or outputs need a copy step in a fresh worktree.** Where the
  verification gate is a consistency script rather than a test runner, that script IS the
  verification — and PRs there are source-only, since the outputs aren't committed.
- **A grooming label may be scoped to one team** and not exist on another. Check before applying it;
  on teams that lack it, the grooming flag lives in a comment.

## Why the plan gate is not ceremony

Evidence from one RD run: all 6 plans came back ACTIONABLE, and **every one** found something that
would have produced a wrong or incomplete PR.

- The ticket named ONE gate; there were two, and the second raised a different error on an empty
  evidence set.
- The "obvious" fix — editing a schema field's description — was **dead text**: the caller never
  serialized that schema at all. That also proved the ticket's alternative wasn't a code change.
- The bug existed at TWO sites, not the one cited.
- A FOURTH site existed and already had the fix, meaning one engine disagreed with itself.
- A drift's root cause was a unit-test fake, not the end-to-end test it was filed against; the
  naive fix would have turned the nightly red.
- The ticket's call chain was wrong by one layer, and the "obvious" version bump was structurally
  impossible, because a purity constraint elsewhere would have made the sweep loop forever.

**Lesson: line numbers in tickets drift, and "the ticket says X lives at Y" is a hypothesis, not a
fact.** Always require the plan agent to report corrected `file:line` locations and to state which
of the ticket's claims failed to verify.

## Honesty rules that earned their keep

- When an acceptance criterion **cannot** be met in this environment, say so in the plan, the PR
  body, AND the ticket comment — and propose the honest substitute. Never assert an unmeasured
  number. (One RD criterion, "measure recall impact on a real room", was unmeasurable *because* the
  eval snapshots are built through the very filter under question, so no snapshot can ever contain
  the population needed. That is a finding, not an excuse.)
- **Verify a guard by reverting it.** Prove a no-loop guard by removing it and watching the test
  fail. Do this for any test whose whole point is a guard.

## Recording what you learn

This procedure improves by accumulating gotchas, and there are two places to put them:

- **Run-scoped facts** — per-repo test commands, environment quirks, which bots this repo actually
  runs — go in the **queue file header**. That's where the next run looks first, and it stays local
  to the workspace.
- **Generalizable lessons** — a new ambiguity smell, a new class of hang, a safety rule — belong in
  this skill. Open a PR against the plugin repo. Do **not** edit the installed copy in place: it
  lives in a version-pinned plugin cache, so an in-place edit is invisible to the rest of the team
  and is lost on the next reinstall.
