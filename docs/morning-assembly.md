# Morning Assembly Procedure

Internal reference for the morning assembly pipeline. The SKILL.md invokes this procedure to set up the day's session.

---

## Overview

Four checkpointed steps with a clear separation between **collection** (inbox sweep) and **triage** (agenda build). Each step writes its `checkpoint` value to `current-session.json` on completion. On resume, completed steps are skipped.

Checkpoint progression: `initialized` → `inbox_swept` → `focus_loaded` → `agenda_built` → `assembly_complete`

### Design Principles

1. **Deterministic runbooks** — Each inbox has a fixed sweep procedure, not ad-hoc queries
2. **Timebox philosophy** — Default estimates mean "spend 15-30m on this, not necessarily finish it"
3. **Graceful degradation** — Any source can fail without blocking assembly
4. **Abstracted from user** — Collection runs autonomously; user sees only the finished agenda
5. **Since-last-EOD horizon** — All sweeps use the previous session's close time as the cutoff
6. **Pre-work recognition** — Work done before `/start` (Slack, team blogs) is captured as a completed task, not ignored

---

## Step 1: Inbox Sweep

**Checkpoint:** `inbox_swept`

Sweep all inboxes in a fixed order. Each runbook appends to `session.inbox_items[]`. Failures are logged but don't block other sweeps.

**Full runbook details:** See `docs/inbox-runbooks.md`

### Time horizon

Compute `sweep_since` from previous session:
- If carryover session exists: use `{previous_session.date}T{previous_session.eod_target}` in `config.timezone`
- If no previous session: use yesterday 08:00 in `config.timezone`
- Store as `session.sweep_since` (ISO 8601)

### Step 0.5: Pre-work activity scan

Before the inbox sweep, check if the user already did work this morning (Slack replies, blog comments, etc.). Uses `config.triage.pre_work` settings. If activity exceeds `min_minutes_for_task` (default: 5), inserts a completed "Morning communication work" task at position 0. See `skills/start/SKILL.md` for full procedure.

### Runbook execution order

| # | Source | Tool | Signal level |
|---|--------|------|-------------|
| 1 | Digest | Skill / file read | High — primary signal |
| 2 | Linear Issues | Linear MCP | High — assigned work |
| 3 | Linear Inbox | context MCP linear | Medium — mentions/notifications |
| 4 | Slack | context MCP slack | Variable — 4 layers by signal |
| 5 | Blog/feed activity | context MCP blog provider | Low-Medium — team posts |
| 6 | GitHub | context MCP github | Medium — PR reviews |
| 7 | Gmail | Gmail MCP | Low — rare, quick scan |
| 8 | Calendar | Google Calendar MCP | Structural — protected blocks |

After all runbooks complete (or fail gracefully), set checkpoint to `inbox_swept`.

---

## Step 2: Load Weekly Focus

**Checkpoint:** `focus_loaded`

Unchanged from previous design. Query Linear for active cycle and focus issue.

1. If `config.integrations.focus_skill` configured → invoke it via Skill tool
2. Otherwise → query Linear MCP directly:
   - `list_cycles` for the active cycle
   - `list_issues` filtered by `config.weekly_focus.label` on `config.weekly_focus.team`
   - Find personal sub-issue matching `config.weekly_focus.sub_issue_pattern`
3. Store `focus_issue` and `personal_sub_issue` in session state
4. If Linear MCP unavailable → skip gracefully

---

## Step 3: Build Agenda

**Checkpoint:** `agenda_built`

Transform `session.inbox_items[]` into `session.tasks[]`.

**Full triage details:** See `docs/triage-framework.md`

### Pipeline

1. **Deduplicate** — Match by `dedupe_key` (Linear ref or normalized URL). Merge: keep highest priority, combine context
2. **Triage & Prioritize** — Assign priority tiers, apply timebox estimates
3. **Filter for Today** — Always include carryover/critical/high/overdue/due-today. Medium if budget allows. Cap at 10 tasks.
4. **Order** — Carryover → Critical → High (due-today first) → Medium → Focus/check-in
5. **Insert Time Structure** — Protected blocks + calendar events. Compute budget.

After triage, **clear `session.inbox_items[]`** to keep session JSON lean. The data has been consumed into `session.tasks[]` and `session.headlines[]`.

---

## Step 4: Finalize

**Checkpoint:** `assembly_complete`

1. Write `session.tasks[]` to state (atomic write)
2. Set first pending task to `in_progress`, set `current_task_index`
3. Re-render dashboard:
   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/bin/render_dashboard.py"
   ```
4. Output agenda with:
   - Date line: `Today: Friday, March 13 (Europe/Paris)`
   - Headlines (from digest + FYI items)
   - Task list with IDs, estimates, sources, priority tiers
   - Budget summary
   - Backlog awareness footer (low-priority items not scheduled)
5. Quick fallback prompt: "Anything not on your calendar?" (replaces old "Any meetings today?")

No "anything to add?" — the user speaks up if needed.

---

## Agenda Output Format

Group by time flow. Show task IDs, priority tiers, estimates, and sources. Include a timezone-anchored "Today" line.

```
Morning Assembly — Fri 13 Mar, W11
Today: Friday, March 13 (Europe/Paris)
Focus: PROJ-400 — Team weekly focus

Headlines:
  - API response time improved 15% this week
  - New partner onboarding next Monday

t1  [carryover] Reply to focus thread [PROJ-398]       ~15m  (deferred)
t2  [critical]  Fix notification loop [PROJ-670]        ~30m  (linear, overdue)
── 10:00-11:00 Team standup ──
── 10:00-12:00 Lunch break ──
t3  [high]      Review PR #4521                         ~15m  (github)
t4  [high]      Respond to @username ping in #team-general ~15m  (slack-ping)
t5  [medium]    Performance analysis [PROJ-660]         ~30m  (linear)
t6  [medium]    Weekly check-in                         ~30m  (focus)

Budget: ~2h15m work / ~5h available → Buffer: OK

Backlog awareness:
  - PROJ-680: Update test fixtures (P4, no deadline)
  - Team blog post: "Q2 planning thoughts" by Alex
```
