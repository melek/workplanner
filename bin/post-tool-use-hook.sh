#!/bin/bash
# Workplanner PostToolUse hook.
#
# Secondary feedback channel (per Q2 / Stream A): when the LLM invokes a
# Bash tool whose command is a `wpl` call, inject a short state anchor
# (counts and current task) into the next turn's context. The primary
# channel is the full compact task list on stdout of every wpl command, so
# the hook doesn't repeat it (issue #65); it only keeps the headline state
# in view when long outputs or compaction push the list out of sight.
#
# Protocol: stdin carries hook JSON with tool_name and tool_input.command.
# Output: JSON on stdout shaped per Claude Code hook spec:
#   {"hookSpecificOutput": {"hookEventName": "PostToolUse",
#                           "additionalContext": "..."}}
# If not a wpl invocation, exit 0 silently.

set -eu

input=$(cat)

# Require jq for reliable parsing. If missing, fail open (emit nothing).
command -v jq >/dev/null 2>&1 || exit 0

tool_name=$(printf '%s' "$input" | jq -r '.tool_name // empty')
cmd=$(printf '%s' "$input" | jq -r '.tool_input.command // empty')

[ "$tool_name" = "Bash" ] || exit 0
[ -n "$cmd" ] || exit 0

# Recognize a wpl call (issue #65): a simple command whose first word, after
# any VAR=value assignments, is `wpl`, `*/wpl`, or `python3 .../transition.py`.
# bin/hook_is_wpl.py tokenizes like a shell, so quoted strings and heredoc
# bodies that mention wpl (a commit message, an issue body) don't count.
is_wpl=$(printf '%s' "$cmd" | python3 "$(dirname "$0")/hook_is_wpl.py" 2>/dev/null || echo 0)
[ "$is_wpl" = "1" ] || exit 0

# Resolve the wpl wrapper. Prefer the per-user wrapper path; fall back
# to PATH resolution. Both are fine.
WPL_BIN="${WPL_BIN:-$HOME/.workplanner/bin/wpl}"
if [ ! -x "$WPL_BIN" ]; then
    WPL_BIN=$(command -v wpl 2>/dev/null || true)
fi
[ -n "${WPL_BIN:-}" ] && [ -x "$WPL_BIN" ] || exit 0

# Pull compact status as JSON, then fold down to a short context block.
# Keep the injection under ~500 tokens (roughly <2KB of ASCII).
status_json=$("$WPL_BIN" --format json status 2>/dev/null || true)
[ -n "$status_json" ] || exit 0

# Transform JSON → compact markdown-ish text, capped in line count.
# The inline Python script reads the JSON payload from stdin (piped in from
# $status_json). We use `-c` rather than a heredoc so stdin stays free for
# the pipe input.
summary=$(printf '%s' "$status_json" | python3 -c '
import json, sys

try:
    s = json.load(sys.stdin)
except Exception:
    sys.exit(0)

lines = ["Workplanner session state (post-wpl injection; full list is in the command output):"]
counts = s.get("counts", {})
dropped = counts.get("dropped", 0)
lines.append(
    "  Done: {}/{}{} | ~{}m left | EOD: {}".format(
        counts.get("done", 0),
        counts.get("total", 0),
        " \u00b7 {} dropped".format(dropped) if dropped else "",
        counts.get("remaining_min", 0),
        s.get("eod_target", "?"),
    )
)
# A closed session looks open everywhere except the dashboard without this
# marker (issue #82).
if s.get("checkpoint") == "closed":
    lines[-1] += " | CLOSED (work now is not in the handoff)"

cur = s.get("current_task")
if cur:
    lines.append(
        "  Current: {} {} \"{}\" (~{}m)".format(
            cur.get("tid"),
            cur.get("uid"),
            cur.get("title"),
            cur.get("estimate_min", "?"),
        )
    )
else:
    lines.append("  Current: (none)")

# The output of the wpl command already carries the full task list
# (post-mutation feedback loop), so it is not repeated here (issue #65). Keep
# only the counts and current-task anchor above.

print("\n".join(lines))
' 2>/dev/null || true)

[ -n "$summary" ] || exit 0

# Emit hook output as JSON. jq -Rs builds a valid JSON string from the
# summary; the jq expression wraps it in the required hook-response
# envelope.
printf '%s' "$summary" | jq -Rs '{
  hookSpecificOutput: {
    hookEventName: "PostToolUse",
    additionalContext: .
  }
}'
