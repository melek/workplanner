#!/bin/bash
# Workplanner PostToolUse hook.
#
# Secondary feedback channel (per Q2 / Stream A): when the LLM invokes a
# Bash tool whose command is a `wpl` call, re-inject the current session
# state into the next turn's context. The primary channel is the full
# compact task list on stdout of every mutating command; this hook is
# defense-in-depth for cases where the assistant's model of state drifts
# across turns anyway (long outputs, compaction, or third-party tools
# elbowing the stdout off the visible context).
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

# Recognize either a bare `wpl ...` call or the explicit wrapper path.
# Match conservatively: first token is `wpl` or ends with `/wpl`,
# or the command contains the wpl wrapper path substring.
first_token=$(printf '%s' "$cmd" | awk '{print $1}')
is_wpl=0
case "$first_token" in
    wpl|*/wpl) is_wpl=1 ;;
esac
case "$cmd" in
    */.workplanner/bin/wpl*) is_wpl=1 ;;
    */workplanner/bin/transition.py*) is_wpl=1 ;;
esac
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

lines = ["Workplanner session state (post-wpl injection):"]
counts = s.get("counts", {})
lines.append(
    "  Done: {}/{} | ~{}m left | EOD: {}".format(
        counts.get("done", 0),
        counts.get("total", 0),
        counts.get("remaining_min", 0),
        s.get("eod_target", "?"),
    )
)

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

glyphs = {
    "in_progress": "▶",
    "done":        "✓",
    "blocked":     "⚠",
    "deferred":    "↷",
    "pending":     " ",
}

tasks = s.get("tasks") or []
# Cap at 15 tasks to stay well under ~500 tokens.
if tasks:
    lines.append("  Tasks:")
    for t in tasks[:15]:
        g = glyphs.get(t.get("status", "pending"), " ")
        lines.append(
            "    [{}] {}  {}  \"{}\"".format(
                g, t.get("tid"), t.get("uid"), t.get("title")
            )
        )
    if len(tasks) > 15:
        lines.append("    ... {} more".format(len(tasks) - 15))

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
