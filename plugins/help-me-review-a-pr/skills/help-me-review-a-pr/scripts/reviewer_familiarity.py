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
    "*sqlcdb/*", "*/vendor/*", "*/dist/*", "*/build/*", "*/node_modules/*",
    "*.sql.go", "*_templ.go", "*.generated.*",
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


def parse_churn(lines):
    """numstat: added<TAB>deleted<TAB>path. '-' means binary, which has no churn."""
    churn = {}
    for line in lines:
        parts = line.rstrip("\n").split("\t")
        if len(parts) < 3:
            continue
        a, d, path = parts[0], parts[1], parts[-1].strip()
        if " => " in path:  # a rename; score the destination
            path = re.sub(r"^(.*)\{(.*) => (.*)\}(.*)$", r"\1\3\4", path)
            path = path.split(" => ")[-1].strip("{} ")
        n = 0 if a == "-" else int(a or 0)
        n += 0 if d == "-" else int(d or 0)
        churn[path] = churn.get(path, 0) + n
    return churn


def changed_files(repo, args):
    """Return (kept_files, churn_by_path, total_seen, weighting)."""
    churn, weighting = {}, "churn"
    if args.churn_from:
        src = sys.stdin if args.churn_from == "-" else open(args.churn_from, encoding="utf-8")
        churn = parse_churn(src)
        files = list(churn)
    elif args.diff_range:
        churn = parse_churn(git(repo, "diff", "--numstat", args.diff_range).splitlines())
        files = list(churn)
    elif args.files or args.files_from:
        if args.files:
            files = list(args.files)
        else:
            src = sys.stdin if args.files_from == "-" else open(args.files_from, encoding="utf-8")
            files = [l.strip() for l in src if l.strip()]
        churn, weighting = {f: 1 for f in files}, "equal"
    else:
        churn = parse_churn(git(repo, "diff", "--numstat", "HEAD").splitlines())
        files = list(churn)

    kept = [f for f in files if not excluded(f, args.exclude)]
    return kept, churn, len(files), weighting


def blame_file(repo, path, ignore_revs):
    """author-email -> surviving line count for one file at HEAD."""
    cmd = ["blame", "--line-porcelain"]
    if ignore_revs:
        cmd.append(f"--ignore-revs-file={ignore_revs}")
    out = git(repo, *cmd, "HEAD", "--", path, check=False)
    counts = defaultdict(int)
    for line in out.splitlines():
        if line.startswith("author-mail <"):
            counts[line[len("author-mail <"):].rstrip(">").strip().lower()] += 1
    return counts


def blame_share(repo, path, emails, ignore_revs):
    """(reviewer_lines, total_lines) for one existing file."""
    counts = blame_file(repo, path, ignore_revs)
    return sum(n for e, n in counts.items() if e in emails), sum(counts.values())


def collapse(node):
    """Merge a directory that holds exactly one directory and no files."""
    while len(node["dirs"]) == 1 and not node["files"]:
        (name, only), = node["dirs"].items()
        base = node["name"]
        if base and not base.endswith("/"):
            base += "/"
        node = {"name": f"{base}{name}/", "dirs": only["dirs"], "files": only["files"]}
    node["dirs"] = {k: collapse(dict(v, name=k)) for k, v in node["dirs"].items()}
    return node


def build_tree(entries):
    root = {"name": "", "dirs": {}, "files": []}
    for e in entries:
        parts = e["path"].split("/")
        node = root
        for part in parts[:-1]:
            node = node["dirs"].setdefault(part, {"name": part, "dirs": {}, "files": []})
        node["files"].append(e)
    return collapse(root)


def weigh(node):
    """Attach churn-weighted share to every node, bottom-up."""
    w = sum(f["churn"] for f in node["files"])
    acc = sum(f["share"] * f["churn"] for f in node["files"])
    for child in node["dirs"].values():
        cw, cacc = weigh(child)
        w += cw
        acc += cacc
    node["churn"] = w
    node["share"] = (acc / w) if w else 0.0
    return w, acc


def render(node, out, prefix="", is_last=True, root=False):
    if not root:
        label = node["name"] if node["name"].endswith("/") else node["name"] + "/"
        out.append(f"{prefix}{'`-- ' if is_last else '|-- '} {node['share']:>4.0%}  {label}"
                   f"  [{node['churn']} changed]")
        prefix += "    " if is_last else "|   "
    children = ([("d", d) for d in sorted(node["dirs"].values(), key=lambda n: -n["churn"])]
                + [("f", f) for f in sorted(node["files"], key=lambda f: -f["churn"])])
    for i, (kind, child) in enumerate(children):
        last = i == len(children) - 1
        if kind == "d":
            render(child, out, prefix, last)
        else:
            mark = "~" if child["inferred"] else " "
            out.append(f"{prefix}{'`-- ' if last else '|-- '}{mark}{child['share']:>4.0%}  "
                       f"{child['path'].rsplit('/', 1)[-1]}  [{child['churn']} changed]"
                       + ("" if child["inferred"] else f"  ({child['reviewer_lines']}/{child['total_lines']} lines)"))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="*", help="explicit file list (equal weighting)")
    ap.add_argument("--repo", default=".", help="repository path (default: cwd)")
    ap.add_argument("--diff-range", help="e.g. origin/main...HEAD; churn comes from git diff --numstat")
    ap.add_argument("--files-from", help="file with one path per line, or - for stdin (equal weighting)")
    ap.add_argument("--churn-from", help="numstat lines (added<TAB>deleted<TAB>path), or - for stdin")
    ap.add_argument("--as", dest="identity", action="append",
                    help="reviewer email, name, or forge login; repeatable "
                         "(default: git config + gh api user + .mailmap)")
    ap.add_argument("--max-files", type=int, default=25, help="blame at most N files (default: 25)")
    ap.add_argument("--dir-sample", type=int, default=3,
                    help="for a directory whose changed files are all new, blame N existing siblings (default: 3)")
    ap.add_argument("--exclude", action="append", default=list(DEFAULT_EXCLUDES),
                    help="extra glob to treat as generated")
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

    ignore_revs_path = os.path.join(repo, ".git-blame-ignore-revs")
    ignore_revs = ignore_revs_path if os.path.exists(ignore_revs_path) else None

    files, churn, raw_count, weighting = changed_files(repo, args)
    warnings = []

    # Score each changed file by the reviewer's share of the code it lands in.
    # A file the change ADDS has no history, so its share is inferred from
    # existing siblings in the same directory: familiarity with new code is
    # familiarity with the area. Without this, greenfield work by the author of
    # the surrounding module reads as maximally unfamiliar.
    existing = [f for f in files if os.path.exists(os.path.join(repo, f))]
    new_files = [f for f in files if f not in set(existing)]

    blamed = 0
    entries, dir_ctx, context_dirs = [], {}, []
    for f in existing[: args.max_files]:
        mine, tot = blame_share(repo, f, emails, ignore_revs)
        blamed += 1
        entries.append({"path": f, "churn": churn.get(f, 1), "inferred": False,
                        "share": (mine / tot) if tot else 0.0,
                        "reviewer_lines": mine, "total_lines": tot})

    for f in new_files:
        d = os.path.dirname(f) or "."
        if d not in dir_ctx:
            siblings = [x for x in git(repo, "ls-files", "--", d).split()
                        if (os.path.dirname(x) or ".") == d and not excluded(x, args.exclude)][: args.dir_sample]
            m = t = 0
            for sib in siblings:
                a, b = blame_share(repo, sib, emails, ignore_revs)
                m += a; t += b
                blamed += 1
            dir_ctx[d] = (m / t) if t else None
            if t:
                context_dirs.append(d)
        share = dir_ctx[d]
        entries.append({"path": f, "churn": churn.get(f, 1), "inferred": True,
                        "share": share or 0.0, "measurable": share is not None,
                        "reviewer_lines": None, "total_lines": None})

    scored = [e for e in entries if not e["inferred"] or e.get("measurable", True)]
    total_w = sum(e["churn"] for e in scored)
    share = (sum(e["share"] * e["churn"] for e in scored) / total_w) if total_w else 0.0

    if not files:
        verdict, reason = "unknown", (
            "no files given to blame" if not raw_count else
            f"all {raw_count} changed file(s) were excluded as generated")
    elif not scored:
        verdict, reason = "unknown", "every changed file is new and no existing sibling could be measured"
    elif commits == 0:
        verdict, reason = "unknown", (
            f"no authored commits found for {seed}; the identity mapping is unusable, so a 0% "
            f"share here says nothing about the reviewer. Pass the missing handle with --as.")
    elif share >= HIGH_SHARE:
        verdict = "high"
    elif share < LOW_SHARE:
        verdict = "low"
    else:
        verdict = "partial"
    if commits and scored and files:
        reason = (f"reviewer authored {share:.0%} of the code the change lands in, "
                  f"weighted by {'lines changed per file' if weighting == 'churn' else 'file (equal weights)'}")

    if weighting == "equal":
        warnings.append("no churn data: every file weighted equally. Feed numstat via --churn-from "
                        "or --diff-range to weight by lines changed")
    if new_files:
        detail = f" ({', '.join(context_dirs)})" if context_dirs else ""
        warnings.append(f"{len(new_files)} changed file(s) are new and have no history; "
                        f"{'shares inferred from directory siblings' + detail if context_dirs else 'no existing siblings to measure'}")
    if 0 < commits < 3:
        warnings.append(f"identity has only {commits} authored commit(s) here; the share is thin evidence")
    if not ignore_revs:
        warnings.append("no .git-blame-ignore-revs: formatting and regeneration commits inflate whoever ran them")
    if len(existing) > args.max_files:
        warnings.append(f"{len(existing) - args.max_files} changed file(s) not blamed (--max-files {args.max_files})")

    tree = build_tree(entries)
    weigh(tree)

    if args.json:
        print(json.dumps({
            "verdict": verdict, "reason": reason, "share": round(share, 4), "weighting": weighting,
            "identity": {"seed": seed, "emails": sorted(emails), "names": sorted(names), "commits": commits},
            "files": [{k: (round(v, 4) if k == "share" else v) for k, v in e.items()} for e in entries],
            "files_blamed": blamed, "files_new": len(new_files), "context_dirs": context_dirs,
            "warnings": warnings,
        }, indent=2))
        return 0

    print(f"familiarity: {verdict.upper()} - {reason}")
    print(f"identity: {seed} -> {len(emails)} address(es), {commits} commits")
    if len(emails) > 1:
        print(f"  resolved: {', '.join(sorted(emails))}")
    print(f"blamed {blamed} file(s) for {len(entries)} changed file(s); "
          f"~ marks a share inferred from the directory because the file is new")
    print(f"{share:>4.0%}  (overall)  [{tree['churn']} changed]")
    out = []
    render(tree, out, root=True)
    print("\n".join(out))
    for w in warnings:
        print(f"warning: {w}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
