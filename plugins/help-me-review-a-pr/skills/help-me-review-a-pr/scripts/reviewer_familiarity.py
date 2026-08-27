#!/usr/bin/env python3
"""Estimate how familiar a reviewer is with the code a change touches.

Reads git blame over the hand-written files a diff touches and reports the
reviewer's authorship share per directory, so a review can decide how much
orientation to write. Read-only: runs `git` plumbing and writes nothing.

The hard part is not the arithmetic, it is identity. A person's configured
email is routinely not the address in the history (a GitHub noreply or personal
address on merged commits), and keying on the configured address alone reports
an author of a file as a stranger to it. So identities are resolved as a SET and
then sanity-checked: an identity with commits but no blamed lines, or the
reverse, is a broken mapping and the verdict becomes "unknown" rather than
"unfamiliar" -- the two must never be confused.

Usage:
  reviewer_familiarity.py --diff-range origin/main...HEAD
  reviewer_familiarity.py --files-from - < changed_files.txt
  reviewer_familiarity.py path/to/a.go path/to/b.py --as someone@example.com
  ... --json     machine-readable output
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import subprocess
import sys
from collections import defaultdict

# Generated output: authorship there measures who ran the generator.
DEFAULT_EXCLUDES = (
    "*/gen/*", "*_pb2.py", "*_pb2.pyi", "*_pb2_grpc.py", "*.pb.go", "*_grpc.pb.go",
    "*/sqlcdb/*", "*/vendor/*", "*/dist/*", "*/build/*", "*/node_modules/*",
    "*.lock", "*.sum", "package-lock.json", "yarn.lock", "poetry.lock", "uv.lock",
    "*.snap", "*.golden", "*.min.js", "*.min.css",
)

# Share of blamed lines in the touched directories, after exclusions.
HIGH_SHARE = 0.50
LOW_SHARE = 0.10


def git(repo, *args, check=True):
    p = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True)
    if check and p.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {p.stderr.strip()}")
    return p.stdout


def gather_seeds(repo, explicit):
    """Every handle that might identify the reviewer, from every cheap source.

    One seed is not enough. A configured work address and a personal address on
    the merged commits share neither name nor email, so nothing in the git log
    can bridge them -- but the forge login usually equals the author name on
    those commits, which is what closes the gap. `gh` is optional; without it,
    pass the missing handle with --as.
    """
    seeds = [s for s in (explicit or []) if s]
    if not seeds:
        seeds += [git(repo, "config", "user.email", check=False).strip(),
                  git(repo, "config", "user.name", check=False).strip()]
        try:
            p = subprocess.run(["gh", "api", "user", "--jq", ".login, .name, .email"],
                               capture_output=True, text=True, timeout=15)
            if p.returncode == 0:
                seeds += [l.strip() for l in p.stdout.splitlines() if l.strip() and l.strip() != "null"]
        except (OSError, subprocess.SubprocessError):
            pass
    mailmap = os.path.join(repo, ".mailmap")
    if os.path.exists(mailmap):
        with open(mailmap, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.split("#", 1)[0].strip()
                if any(s and s.lower() in line.lower() for s in seeds):
                    seeds += [tok.strip("<>") for tok in line.replace("<", " <").split() if "@" in tok]
    return [s for s in dict.fromkeys(s.strip() for s in seeds) if s]


def resolve_identity(repo, seeds):
    """Grow an identity from the seeds by alternating name and email matches.

    Walks the whole author list once, then closes over it: an email reached via
    a known name pulls in that email's other names, and so on. This is what
    catches work-address-in-config vs personal-address-in-history.
    """
    emails, names = set(), set()
    for seed in seeds:
        (emails if "@" in seed else names).add(seed.strip().lower())

    pairs = []
    for line in git(repo, "log", "--no-merges", "--format=%an|%ae%n%(trailers:key=Co-authored-by,valueonly)").splitlines():
        line = line.strip()
        if not line:
            continue
        if "|" in line:
            n, _, e = line.partition("|")
            is_author = True
        elif "<" in line and ">" in line:  # a Co-authored-by trailer
            n, _, rest = line.partition("<")
            e = rest.split(">")[0]
            is_author = False
        else:
            continue
        n, e = n.strip().lower(), e.strip().lower()
        if n or e:
            pairs.append((n, e, is_author))

    changed = True
    while changed:
        changed = False
        for n, e, _ in pairs:
            if e in emails and n and n not in names:
                names.add(n); changed = True
            elif n in names and e and e not in emails:
                emails.add(e); changed = True

    authored = sum(1 for n, e, is_author in pairs if is_author and (e in emails or n in names))
    return emails, names, authored


def excluded(path, patterns):
    return any(fnmatch.fnmatch(path, pat) or fnmatch.fnmatch("/" + path, pat) for pat in patterns)


def changed_files(repo, args):
    if args.files:
        files = list(args.files)
    elif args.files_from:
        src = sys.stdin if args.files_from == "-" else open(args.files_from, encoding="utf-8")
        files = [l.strip() for l in src if l.strip()]
    elif args.diff_range:
        files = git(repo, "diff", "--name-only", args.diff_range).split()
    else:
        files = git(repo, "diff", "--name-only", "HEAD").split()
    return [f for f in files if not excluded(f, args.exclude)], len(files)


def blame_file(repo, path, ignore_revs):
    cmd = ["blame", "--line-porcelain", "HEAD", "--"]
    if ignore_revs:
        cmd = ["blame", "--line-porcelain", f"--ignore-revs-file={ignore_revs}", "HEAD", "--"]
    out = git(repo, *cmd[:-1], "--", path, check=False)
    counts = defaultdict(int)
    for line in out.splitlines():
        if line.startswith("author-mail <"):
            counts[line[len("author-mail <"):].rstrip(">").strip().lower()] += 1
    return counts


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="*", help="explicit file list")
    ap.add_argument("--repo", default=".", help="repository path (default: cwd)")
    ap.add_argument("--diff-range", help="e.g. origin/main...HEAD")
    ap.add_argument("--files-from", help="file with one path per line, or - for stdin")
    ap.add_argument("--as", dest="identity", action="append",
                    help="reviewer email, name, or forge login; repeatable "
                         "(default: git config + gh api user + .mailmap)")
    ap.add_argument("--max-files", type=int, default=25, help="blame at most N files (default: 25)")
    ap.add_argument("--exclude", action="append", default=list(DEFAULT_EXCLUDES), help="extra glob to treat as generated")
    ap.add_argument("--json", action="store_true", help="emit JSON")
    args = ap.parse_args()

    repo = os.path.abspath(args.repo)
    try:
        git(repo, "rev-parse", "--git-dir")
    except RuntimeError:
        print(f"not a git repository: {repo}", file=sys.stderr)
        return 2

    seeds = gather_seeds(repo, args.identity)
    emails, names, commits = resolve_identity(repo, seeds)
    seed = ", ".join(seeds) if seeds else "(none)"

    ignore_revs = os.path.join(repo, ".git-blame-ignore-revs")
    ignore_revs = ignore_revs if os.path.exists(ignore_revs) else None

    files, raw_count = changed_files(repo, args)
    files = [f for f in files if os.path.exists(os.path.join(repo, f))]
    truncated = max(0, len(files) - args.max_files)
    files = files[: args.max_files]

    per_dir = defaultdict(lambda: [0, 0])  # dir -> [reviewer_lines, total_lines]
    repo_reviewer = repo_total = 0
    for f in files:
        counts = blame_file(repo, f, ignore_revs)
        mine = sum(n for e, n in counts.items() if e in emails)
        tot = sum(counts.values())
        d = os.path.dirname(f) or "."
        per_dir[d][0] += mine
        per_dir[d][1] += tot
        repo_reviewer += mine
        repo_total += tot

    share = (repo_reviewer / repo_total) if repo_total else 0.0

    # The sanity check. A mapping that finds commits but no lines (or lines but
    # no commits) is broken, and a broken mapping must not read as "unfamiliar".
    warnings = []
    if not files:
        verdict, reason = "unknown", (
            "no files given to blame" if not raw_count else
            f"all {raw_count} changed file(s) were excluded as generated, or do not exist at HEAD")
    elif commits == 0:
        # No authored commits under ANY resolved address means the mapping never
        # closed -- a 0% share would then be an artifact of the lookup, not a
        # fact about the reviewer. Refuse to call that unfamiliar.
        verdict, reason = "unknown", (
            f"no authored commits found for {seed}; the identity mapping is unusable, so a 0% "
            f"share here says nothing about the reviewer. Pass the missing handle with --as.")
    elif share >= HIGH_SHARE:
        verdict, reason = "high", f"reviewer authored {share:.0%} of the blamed lines in the touched files"
    elif share < LOW_SHARE:
        verdict, reason = "low", f"reviewer authored {share:.0%} of the blamed lines in the touched files"
    else:
        verdict, reason = "partial", f"reviewer authored {share:.0%} of the blamed lines in the touched files"

    if 0 < commits < 3:
        warnings.append(f"identity has only {commits} authored commit(s) in this repo; the share is thin evidence")
    if not ignore_revs:
        warnings.append("no .git-blame-ignore-revs: formatting and regeneration commits inflate whoever ran them")
    if truncated:
        warnings.append(f"{truncated} further changed files not blamed (--max-files {args.max_files})")

    dirs = sorted(
        ({"dir": d, "reviewer_lines": v[0], "total_lines": v[1],
          "share": round(v[0] / v[1], 4) if v[1] else 0.0} for d, v in per_dir.items()),
        key=lambda r: -r["total_lines"])

    if args.json:
        print(json.dumps({
            "verdict": verdict, "reason": reason, "share": round(share, 4),
            "identity": {"seed": seed, "emails": sorted(emails), "names": sorted(names), "commits": commits},
            "files_blamed": len(files), "files_skipped": truncated,
            "directories": dirs, "warnings": warnings,
        }, indent=2))
        return 0

    print(f"familiarity: {verdict.upper()} - {reason}")
    print(f"identity: {seed} -> {len(emails)} address(es), {commits} commits")
    if len(emails) > 1:
        print(f"  resolved: {', '.join(sorted(emails))}")
    print(f"blamed {len(files)} hand-written file(s){f', skipped {truncated}' if truncated else ''}")
    for r in dirs[:10]:
        print(f"  {r['share']:6.0%}  {r['reviewer_lines']:>6}/{r['total_lines']:<6}  {r['dir']}")
    for w in warnings:
        print(f"warning: {w}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
