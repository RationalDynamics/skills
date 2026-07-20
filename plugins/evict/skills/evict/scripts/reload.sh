#!/usr/bin/env bash
# SessionStart hook for the `evict` skill.
# If an evict reload is pending, inject the kept (verbatim / summarized) context
# via hookSpecificOutput.additionalContext (the ONLY SessionStart channel that
# reaches the model — `systemMessage` is not a valid SessionStart output field and
# is silently discarded) and clean up all temporary state. No-op otherwise.

PENDING_FILE="/tmp/.evict_pending.json"

[ -f "$PENDING_FILE" ] || exit 0

PENDING=$(cat "$PENDING_FILE" 2>/dev/null)
IS_PENDING=$(printf '%s' "$PENDING" | python3 -c "import sys,json;
try: print(json.load(sys.stdin).get('pending', False))
except Exception: print(False)" 2>/dev/null)

[ "$IS_PENDING" = "True" ] || exit 0

RELOAD_FILE=$(printf '%s' "$PENDING" | python3 -c "import sys,json; print(json.load(sys.stdin).get('reload_file',''))" 2>/dev/null)
WORK_DIR=$(printf '%s' "$PENDING" | python3 -c "import sys,json; print(json.load(sys.stdin).get('work_dir',''))" 2>/dev/null)

if [ ! -f "$RELOAD_FILE" ]; then
  # Marker said a reload was pending but the staged file is gone. Don't silently
  # drop it — surface it via additionalContext (SessionStart has no `systemMessage`
  # field; it is discarded), so the failure actually reaches the model/user.
  rm -f "$PENDING_FILE"
  python3 -c "import json; print(json.dumps({'hookSpecificOutput': {'hookEventName': 'SessionStart', 'additionalContext': '[evict] A context reload was staged but its content file was missing at /clear time — nothing was restored. Re-run /evict.'}}))"
  exit 0
fi

CONTENT=$(cat "$RELOAD_FILE")

# Clean up working dir + pending marker before injecting.
[ -n "$WORK_DIR" ] && rm -rf "$WORK_DIR"
rm -f "$PENDING_FILE"

python3 -c "import json,sys; print(json.dumps({'hookSpecificOutput': {'hookEventName': 'SessionStart', 'additionalContext': sys.stdin.read()}}))" <<< "$CONTENT"
