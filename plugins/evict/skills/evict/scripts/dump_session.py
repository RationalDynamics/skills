#!/usr/bin/env python3
"""Backend dump for the `evict` skill.

Locates the current Claude Code session transcript, renders it *exactly* into a
readable form, and segments it into "turns" (one human prompt plus every
assistant/tool exchange that followed until the next human prompt). Turns are the
atoms the model groups into evict "blocks".

Outputs, under --out-dir (default /tmp/claude-evict/<session-id>/):
  full-transcript.md   the whole session, turn by turn (verbatim)
  turns/turn-<N>.md    one file per turn (verbatim slice used for reload)
  index.json           [{turn, preview, chars, kind}]  machine-readable index

Also prints a compact index to stdout so the model sees turn boundaries directly.

Turn 0 (if present) is any pre-amble before the first human prompt.
Assistant "thinking" blocks are internal and omitted by default (they are not
reloaded and only add noise); pass --include-thinking to keep them.
Content is rendered in full — nothing is truncated.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path


def project_dir_for_cwd(cwd: str) -> str:
    """Claude Code mangles the project path by replacing non-alphanumerics with '-'."""
    return re.sub(r"[^A-Za-z0-9]", "-", cwd)


def resolve_transcript(session_id: str | None, project_dir: str | None) -> Path:
    base = Path.home() / ".claude" / "projects"
    if project_dir:
        pdir = Path(project_dir)
        if not pdir.is_absolute():
            pdir = base / project_dir
    else:
        pdir = base / project_dir_for_cwd(os.getcwd())
    if not pdir.exists():
        sys.exit(f"error: project transcript dir not found: {pdir}")
    if session_id:
        cand = pdir / f"{session_id}.jsonl"
        if cand.exists():
            return cand
        sys.stderr.write(
            f"warning: {cand.name} not found; falling back to newest transcript\n"
        )
    jsonls = sorted(pdir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not jsonls:
        sys.exit(f"error: no .jsonl transcripts in {pdir}")
    return jsonls[0]


def _stringify(content) -> str:
    """tool_result content may be a str or a list of blocks. Rendered in full."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for b in content:
            if isinstance(b, dict):
                if b.get("type") == "text":
                    parts.append(b.get("text", ""))
                else:
                    parts.append(json.dumps(b))
            else:
                parts.append(str(b))
        return "\n".join(parts)
    return json.dumps(content)


def is_human_user(rec: dict) -> bool:
    if rec.get("type") != "user":
        return False
    c = rec.get("message", {}).get("content")
    if isinstance(c, str):
        return c.strip() != ""
    if isinstance(c, list):
        return any(isinstance(b, dict) and b.get("type") == "text" for b in c)
    return False


def render_record(rec: dict, include_thinking: bool) -> list[str]:
    t = rec.get("type")
    msg = rec.get("message", {})
    content = msg.get("content")
    out: list[str] = []

    if t == "user":
        if isinstance(content, str):
            out.append(f"**USER:** {content}")
        elif isinstance(content, list):
            for b in content:
                if not isinstance(b, dict):
                    continue
                bt = b.get("type")
                if bt == "text":
                    out.append(f"**USER:** {b.get('text', '')}")
                elif bt == "tool_result":
                    body = _stringify(b.get("content", ""))
                    out.append(f"  ↳ _[tool_result]_ {body}")
                elif bt == "image":
                    out.append("  ↳ _[user image attachment]_")
        return out

    if t == "assistant":
        if isinstance(content, list):
            for b in content:
                if not isinstance(b, dict):
                    continue
                bt = b.get("type")
                if bt == "text":
                    out.append(f"**ASSISTANT:** {b.get('text', '')}")
                elif bt == "thinking":
                    if include_thinking:
                        out.append(f"_(thinking)_ {b.get('thinking', '')}")
                elif bt == "tool_use":
                    inp = json.dumps(b.get("input", {}), ensure_ascii=False)
                    out.append(f"  → _[tool_use: {b.get('name')}]_ {inp}")
        elif isinstance(content, str):
            out.append(f"**ASSISTANT:** {content}")
        return out

    if t == "attachment":
        out.append("_[attachment]_")
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--session-id", default=os.environ.get("CLAUDE_SESSION_ID"))
    ap.add_argument("--project-dir", default=None)
    ap.add_argument("--out-dir", default=None)
    ap.add_argument("--include-thinking", action="store_true")
    args = ap.parse_args()

    transcript = resolve_transcript(args.session_id, args.project_dir)
    session_id = transcript.stem
    out_dir = Path(args.out_dir) if args.out_dir else Path("/tmp/claude-evict") / session_id
    turns_dir = out_dir / "turns"

    # Assemble turns: each human prompt opens a new turn.
    turns: list[dict] = []
    cur: dict | None = None

    def new_turn(preview: str) -> dict:
        return {"turn": len(turns), "preview": preview, "lines": []}

    with transcript.open() as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            t = rec.get("type")
            if t not in ("user", "assistant", "attachment"):
                continue
            if is_human_user(rec):
                c = rec.get("message", {}).get("content")
                if isinstance(c, str):
                    preview = c.strip()
                else:
                    preview = next(
                        (b.get("text", "") for b in c
                         if isinstance(b, dict) and b.get("type") == "text"),
                        "",
                    )
                preview = " ".join(preview.split())[:140]
                if cur is not None:
                    turns.append(cur)
                cur = new_turn(preview)
            if cur is None:
                cur = new_turn("(session preamble)")
            cur["lines"].extend(render_record(rec, args.include_thinking))
    if cur is not None:
        turns.append(cur)

    # Fail loudly rather than leaving an empty out_dir behind (a marker pointing at
    # an empty capture is exactly what makes /clear restore nothing silently).
    if not turns:
        sys.exit(
            f"error: no turns parsed from {transcript} — refusing to stage an empty "
            f"capture. Nothing written."
        )

    # Only now that we have content do we create the output tree.
    turns_dir.mkdir(parents=True, exist_ok=True)

    index = []
    full_parts = []
    for tinfo in turns:
        n = tinfo["turn"]
        body = "\n\n".join(tinfo["lines"]).strip() or "_(no renderable content)_"
        header = f"## [turn {n}] {tinfo['preview'] or '(no prompt text)'}"
        block = f"{header}\n\n{body}\n"
        (turns_dir / f"turn-{n}.md").write_text(block)
        full_parts.append(block)
        index.append({
            "turn": n,
            "preview": tinfo["preview"],
            "chars": len(body),
            "kind": "preamble" if tinfo["preview"] == "(session preamble)" else "exchange",
        })

    (out_dir / "full-transcript.md").write_text("\n---\n\n".join(full_parts))
    (out_dir / "index.json").write_text(json.dumps(index, indent=2))

    # Compact stdout index for the model.
    print(f"session_id: {session_id}")
    print(f"transcript: {transcript}")
    print(f"out_dir:    {out_dir}")
    print(f"turns:      {len(turns)}")
    print("")
    print("TURN INDEX (group contiguous turns into blocks):")
    for row in index:
        print(f"  turn {row['turn']:>2}  [{row['chars']:>6} ch]  {row['preview']}")


if __name__ == "__main__":
    main()
