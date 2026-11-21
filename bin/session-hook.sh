#!/bin/bash
# Workplanner session hook — injects active workplan context into any Claude Code session.
# Registered automatically via plugin.json hooks when the plugin is enabled.
#
# Profile resolution: this hook runs at SessionStart with no active `wpl`
# invocation, so we defer to `wpl` itself (which does path-based cwd
# resolution). This fixes the split-brain described in issue #16: the
# hook's cwd is the session's cwd, which is what path-based resolution
# keys off anyway, so the right profile is selected automatically.
#
# No `active`-symlink fallback (issue #82): the CLI's resolver already
# covers single-profile setups, and the fallback injected one profile's
# plan into sessions in unrelated directories. If no profile claims the
# cwd, the hook prints nothing. Hook must fit in the 5s SessionStart
# timeout, so keep every branch lean.

# Prefer this plugin version's own CLI over the ~/.workplanner/bin/wpl
# wrapper. The wrapper hardcodes the versioned plugin path it was written
# from, and Claude Code prunes old version dirs after an upgrade, so it can
# point at a deleted directory. Running transition.py from
# $CLAUDE_PLUGIN_ROOT rewrites the wrapper (ensure_wrapper) as a side
# effect, so every session start repairs it. An explicit $WPL_BIN wins.
if [ -n "${WPL_BIN:-}" ]; then
    WPL_CMD=("$WPL_BIN")
elif [ -n "${CLAUDE_PLUGIN_ROOT:-}" ] && [ -f "$CLAUDE_PLUGIN_ROOT/bin/transition.py" ]; then
    WPL_CMD=(python3 "$CLAUDE_PLUGIN_ROOT/bin/transition.py")
elif [ -x "$HOME/.workplanner/bin/wpl" ]; then
    WPL_CMD=("$HOME/.workplanner/bin/wpl")
elif command -v wpl >/dev/null 2>&1; then
    WPL_CMD=("$(command -v wpl)")
else
    WPL_CMD=()
fi

PROFILE_ROOT=""
if [ "${#WPL_CMD[@]}" -gt 0 ]; then
    # Resolve the profile root once via `wpl`. If resolution fails (no
    # profile claims this cwd, no single-profile fallback available),
    # the call exits non-zero and stderr is swallowed. WPL_CHILD=1
    # suppresses the interactive first-run prompt since there's no TTY
    # here.
    PROFILE_ROOT=$(WPL_CHILD=1 "${WPL_CMD[@]}" profile whoami --print-root 2>/dev/null)
fi

[ -n "$PROFILE_ROOT" ] || exit 0

# SessionStart hook input is JSON on stdin; `source` is startup | resume |
# clear | compact. Resume skips the methodology pointer (already in the
# transcript).
# Read with a short timeout: a hook must never block when stdin is a
# terminal or an inherited pipe that never closes.
SOURCE=$(python3 -c 'import json,os,select,sys,time
src, buf, fd = "startup", b"", sys.stdin.fileno()
try:
    if not os.isatty(fd):
        deadline = time.monotonic() + 0.5
        while time.monotonic() < deadline:
            if not select.select([fd], [], [], max(0.0, deadline - time.monotonic()))[0]:
                break
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            buf += chunk
        src = json.loads(buf or b"{}").get("source") or "startup"
except Exception:
    pass
print(src)' 2>/dev/null)

python3 "$(dirname "$0")/session_context.py" "$PROFILE_ROOT" "${SOURCE:-startup}" "${CLAUDE_PLUGIN_ROOT:-}" 2>/dev/null
