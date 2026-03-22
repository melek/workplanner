---
name: eod
description: "End-of-day consolidation. Finalizes open tasks, drafts a Linear check-in update and Slack handoff, and closes the session. Deferred/blocked tasks carry over tomorrow. The two daily bookends — start opens the day, eod closes it."
argument-hint: ""
allowed-tools: Read, Write, Bash, AskUserQuestion, ToolSearch, mcp__linear-server__save_comment, mcp__linear-server__list_comments
---

# EOD Consolidation

End-of-day wrap-up. Four steps: finalize tasks, draft Linear update, draft Slack handoff, close session.

**Plugin root:** `${CLAUDE_PLUGIN_ROOT}`
**Transition CLI:** `wpl` (or `${CLAUDE_PLUGIN_ROOT}/bin/transition.py` if wrapper not created yet)
**Full procedure:** `${CLAUDE_PLUGIN_ROOT}/docs/eod-consolidation.md`

## Procedure

### Step 1: Finalize open tasks

```bash
wpl status
```

Check for any tasks with status `in_progress` or `pending`. If any exist, present them:

```
These tasks are still open:
▶ t2 — API integration review (in progress, 45m elapsed)
○ t5 — Weekly check-in (pending)
```

Ask once: "For each: done, defer, blocked, or backlog?"

Let the user answer for all at once (free text, e.g. "t2 done, t5 defer, t6 backlog friday"). Parse and apply each via transition CLI:

```bash
wpl done
wpl switch t5
wpl defer
wpl switch t6
wpl backlog --from-current --target friday   # sends to backlog with target date
```

**Backlog option:** When the user says "backlog" for a task, optionally with a date (e.g., "backlog friday", "backlog 2026-04-01"), move it to `~/.workplanner/profiles/active/backlog.json` instead of deferring. Use `--from-current` or `--from t{N}` with `--target` if a date is given. This is for work that doesn't belong on tomorrow's agenda but needs to surface later.

**Deferral reckoning:** If `wpl defer` exits with code 2, the task has hit its deferral threshold (default: 3x). The CLI already printed the reckoning prompt. Read the user's choice and apply via `wpl reckon <choice> [--date <date>]`. Options: `b` (break down — defer, then help decompose), `d` (delegate), `x` (drop), `t` (timebox to backlog, requires `--date`), `k` (keep deferring).

### Step 2: Draft Linear update

Read `personal_sub_issue` from session state. Compile an update from the day's tasks:

```markdown
### Thursday, March 5 — Daily Update
**Done:**
- <task title> — <brief note if any>
**Carrying over:**
- <task title> — <reason if blocked>
**Sent to backlog:**
- <task title> — <target date or "no date">
**Notes:**
- <any user-provided notes>
```

Show the draft. Ask: "Post this to your weekly check-in sub-issue?"
- Options: "Post it", "Edit first", "Skip"

If "Post it": use Linear MCP `save_comment` on the sub-issue ID. Record `eod_posted: true` and the comment URL in session state.

### Step 3: Slack handoff draft

Draft 2-3 sentences for the coordination channel (`config.coordination_channel`):
- What was accomplished today
- What's carrying over
- Any blockers for the team

**Display only.** Say: "Here's a draft handoff for the coordination channel — post it yourself if useful."

Do NOT post to Slack.

### Step 4: Close session

Write final state to session JSON:
- Set `checkpoint: "closed"`
- Atomic write + re-render dashboard

Confirm: "Day closed. Deferred tasks will carry over tomorrow."
