---
name: horizon
description: "Review and manage the backlog — future-scoped work items that aren't on today's agenda. Groups items by urgency, surfaces stale entries, and supports batch actions (promote, reschedule, drop)."
argument-hint: "[upcoming | stale | tag <name>] — defaults to full review"
allowed-tools: Read, Write, Bash, AskUserQuestion, ToolSearch
---

# Horizon

Review and manage the backlog. The backlog holds work items that don't belong on today's agenda but need to be captured and surfaced at the right time.

**Plugin root:** `${CLAUDE_PLUGIN_ROOT}`
**Transition CLI:** `${CLAUDE_PLUGIN_ROOT}/bin/transition.py`
**Backlog file:** `~/.workplanner/profiles/active/backlog.json`

## Arguments

`$ARGUMENTS`

- Empty → full review (all groups)
- `upcoming` → items with target_date or deadline within 7 days
- `stale` → items older than 14 days with no dates
- `tag <name>` → filter by tag

## Procedure

### Step 1: Load backlog

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/bin/transition.py" backlog --list
```

Also read `~/.workplanner/profiles/active/backlog.json` directly for full field access (target_date, not_before, deadline, created_at, tags).

### Step 2: Group items

Categorize each item into one of four groups based on dates and age:

| Group | Criteria | Display |
|-------|----------|---------|
| **Upcoming** | `target_date` or `deadline` within 7 days | Sorted by nearest date first. Show days until due. |
| **Scheduled** | `target_date` set but >7 days out | Sorted by target_date. |
| **Open** | No dates, created <14 days ago | Sorted by created_at (newest first). |
| **Stale** | No dates, created ≥14 days ago | Sorted by age (oldest first). Show age in days. |

If an argument filters to a specific group, only show that group.

### Step 3: Present grouped listing

```
## Backlog Review — {date}

### Upcoming (3)
  d014de31  Create AI blog [writing, feedback]  ~60m  deadline: Apr 1 (16 days)
  a1b2c3d4  Review integration metrics [PROJ-700]  ~30m  target: Mar 20 (4 days)
  e5f6g7h8  Onboarding prep                      ~45m  deadline: Mar 22 (6 days)

### Scheduled (1)
  b2c3d4e5  Meetup agenda finalization           ~30m  target: Mar 25

### Open (2)
  c3d4e5f6  Explore data.blog posting            ~20m  added 3 days ago
  f7g8h9i0  Set up monitoring dashboard           ~45m  added 5 days ago

### Stale (1)
  h9i0j1k2  Update test fixtures                  ~15m  added 21 days ago — consider dropping

Last reviewed: {last_reviewed or "never"}
```

### Step 4: Prompt for actions

```
Any items to promote, reschedule, or drop?

Examples:
  promote d014de31              → add to today's session
  reschedule a1b2c3d4 friday    → change target date
  drop h9i0j1k2                 → remove from backlog
  done                          → finish review
```

Use AskUserQuestion. Parse the response and apply via transition.py:

| Action | Command |
|--------|---------|
| promote | `python3 transition.py backlog --promote <uid>` |
| reschedule | `python3 transition.py backlog --edit <uid> --target <date>` |
| drop | `python3 transition.py backlog --drop <uid>` |
| tag | `python3 transition.py backlog --edit <uid> --tag <tag>` |

Allow multiple actions in one response. Process sequentially.

### Step 5: Update last_reviewed

After the review, update `last_reviewed` in backlog.json:

```bash
python3 -c "
import json, os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

path = Path.home() / '.workplanner' / 'profiles' / 'active' / 'backlog.json'
with open(path) as f: bl = json.load(f)
bl['last_reviewed'] = datetime.now().strftime('%Y-%m-%d')
tmp = str(path) + '.tmp'
with open(tmp, 'w') as f: json.dump(bl, f, indent=2); f.write('\n')
os.rename(tmp, str(path))
"
```

### Step 6: Report

```
Backlog reviewed: {total} items ({upcoming} upcoming, {scheduled} scheduled, {open} open, {stale} stale)
Actions: {N} promoted, {N} rescheduled, {N} dropped
```

## Notes

- The backlog is workspace-agnostic — stored at `~/.workplanner/profiles/active/backlog.json`
- Items enter the backlog via `transition.py backlog "title"`, `transition.py backlog --from t5`, or `transition.py defer --until friday`
- Morning assembly auto-promotes items whose `target_date` or `deadline` is today or past
- All mutations go through `transition.py` and are in the undo log
