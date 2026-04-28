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
# Graceful fallback: if `wpl` isn't available or its resolver can't
# claim the cwd, fall back to $HOME/.workplanner/profiles/active so a
# single-profile setup (or any user whose cwd sits outside every
# declared workspace) still gets context injected. Hook must fit in the
# 5s SessionStart timeout — keep every branch lean.

WPL_BIN="${WPL_BIN:-$HOME/.workplanner/bin/wpl}"
if [ ! -x "$WPL_BIN" ]; then
    WPL_BIN=$(command -v wpl 2>/dev/null || true)
fi

PROFILE_ROOT=""
if [ -n "${WPL_BIN:-}" ] && [ -x "$WPL_BIN" ]; then
    # Resolve the profile root once via `wpl`. If resolution fails (no
    # profile claims this cwd, no single-profile fallback available),
    # the call exits non-zero and stderr is swallowed. WPL_CHILD=1
    # suppresses the interactive first-run prompt since there's no TTY
    # here.
    PROFILE_ROOT=$(WPL_CHILD=1 "$WPL_BIN" profile whoami --print-root 2>/dev/null)
fi

# Fallback: the legacy `active` symlink. Preserved so single-profile
# setups and cwd-outside-any-workspace sessions still get a workplan.
# Respect $WPL_ROOT so test runs don't leak into the real profile tree.
if [ -z "$PROFILE_ROOT" ]; then
    WPL_ROOT_DIR="${WPL_ROOT:-$HOME/.workplanner}"
    FALLBACK="$WPL_ROOT_DIR/profiles/active"
    if [ -d "$FALLBACK" ]; then
        PROFILE_ROOT="$FALLBACK"
    else
        exit 0
    fi
fi

SESSION="$PROFILE_ROOT/session/current-session.json"
[ -f "$SESSION" ] || exit 0

# Quick validation — must be today's session and not closed
DATE=$(python3 -c "import json,sys; s=json.load(open('$SESSION')); print(s.get('date',''))" 2>/dev/null)
TODAY=$(date +%Y-%m-%d)
[ "$DATE" = "$TODAY" ] || exit 0

CHECKPOINT=$(python3 -c "import json; s=json.load(open('$SESSION')); print(s.get('checkpoint',''))" 2>/dev/null)
[ "$CHECKPOINT" = "closed" ] && exit 0

# Generate compact status
python3 -c "
import json, sys
from pathlib import Path

with open('$SESSION') as f:
    s = json.load(f)

tasks = s.get('tasks', [])
idx = s.get('current_task_index')
eod = s.get('eod_target', '18:00')

done = sum(1 for t in tasks if t.get('status') == 'done')
total = len(tasks)
remaining = sum(t.get('estimate_min', 0) or 0 for t in tasks if t.get('status') in ('pending', 'in_progress', 'blocked'))

lines = ['Active workplan for today:']

dispatched = [(i, t) for i, t in enumerate(tasks) if t.get('dispatched')]

if idx is not None and 0 <= idx < len(tasks):
    t = tasks[idx]
    tag = ' (dispatched)' if t.get('dispatched') else ''
    lines.append(f'  Current: t{idx+1} — {t[\"title\"]} (~{t.get(\"estimate_min\",\"?\")}m){tag}')

lines.append(f'  Progress: {done}/{total} done | ~{remaining}m remaining | EOD: {eod}')

pending = [(i, t) for i, t in enumerate(tasks) if t.get('status') == 'pending']
if pending:
    items = ', '.join(f't{i+1}' for i, _ in pending[:5])
    lines.append(f'  Pending: {items}')

other_dispatched = [(i, t) for i, t in dispatched if i != idx]
if other_dispatched:
    items = ', '.join(f't{i+1}' for i, _ in other_dispatched)
    lines.append(f'  Dispatched (other sessions): {items}')

lines.append('  Use wpl to manage tasks (e.g., wpl done, wpl status) — add ~/.workplanner/bin to PATH')

# Disposition line, kept separate by a blank to avoid being skimmed as more state.
plugin_root = '$CLAUDE_PLUGIN_ROOT'
methodology_path = plugin_root.rstrip('/') + '/docs/methodology.md' if plugin_root else 'docs/methodology.md (in the workplanner plugin)'
lines.append('')
lines.append(f'Operating under the workplanner EA/PA methodology. For work-shape questions (prioritization, triage, carryover, deferral, budget, scoping, rhythm), read {methodology_path} and apply its nine principles by name in your reasoning. Cite at least one principle (e.g. \"brief before gate\" / \"completed staff work\", \"no surprises\", \"timebox, don\\'t estimate\", \"carryover earns its place\", \"force the reckoning\", \"graceful degradation\") when making a recommendation; if you deviate from a principle, name it and say why. Generic PM vocabulary (\"critical path\", \"budget\", \"risk\") without principle-mapping means the framework is being read but not applied — the failure mode this directive exists to prevent.')

print('\n'.join(lines))
" 2>/dev/null
