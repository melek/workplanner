#!/bin/bash
# Workplanner session hook — injects active workplan context into any Claude Code session.
# Registered automatically via plugin.json hooks when the plugin is enabled.

SESSION="$HOME/.workplanner/profiles/active/session/current-session.json"

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

print('\n'.join(lines))
" 2>/dev/null
