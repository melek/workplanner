# Triage Framework

Transforms raw `session.inbox_items[]` into the ordered `session.tasks[]` agenda. Applied during the agenda build phase (checkpoint: `agenda_built`).

---

## Pipeline

```
inbox_items[] → Deduplicate → Triage & Prioritize → Filter for Today → Order → Insert Time Structure → tasks[]
```

---

## Step 1: Deduplicate

Match items by `dedupe_key` (Linear ref or normalized URL).

When duplicates exist across sources:
- **Keep the highest priority** across all instances
- **Combine context notes** (e.g., "via digest + linear query")
- **Preserve all source tags** as a list (for audit)
- **Prefer the most specific URL** (Linear issue URL over digest mention)

Example: `PROJ-123` appears in both digest (priority: high) and Linear query (priority: medium) → merged item keeps `priority: high` with combined context.

---

## Step 2: Triage & Prioritize

### Priority Tiers

Priority assignment uses `config.triage.source_priority` (see `docs/state-schema.md` for the full schema). Each source type maps to a tier. If `config.triage` is absent, these defaults apply:

| Tier | Sources (default mapping) |
|------|--------------------------|
| **Critical** | Linear P1 (urgent), overdue items |
| **High** | Linear P2, direct Slack pings, due-today items, digest high-priority, backlog (target date due) |
| **Medium** | Linear P3, team Slack pings, PR reviews, digest medium, carryover, priority-domain gmail, manual, focus |
| **Low** | Linear P4, Slack channel activity, blog/feed posts, non-priority gmail, digest low |
| **FYI** | Announcements, org-wide posts → headlines, not tasks |

**Key design choice:** Carryover defaults to `medium`, not top-of-list. Data shows carryover tasks are completed at 41% vs 81% for manual tasks — forcing them first doesn't help. They get their priority from the tier mapping like everything else.

Runtime overrides from item-specific data:
- `overdue: true` → promote to `critical` regardless of source mapping
- `due_date == session.date` → promote to at least `high`

### Estimation Methodology (timebox, not completion)

Estimates use `config.triage.estimates` (see schema). If absent, these defaults apply:

| Source | Default Estimate | Rationale |
|--------|-----------------|-----------|
| `slack-ping` | 5m | Data shows actual ~0-1m; 5m for buffer |
| `slack-team`, `slack-channel` | 10m | Quick triage |
| `github` | 15m | PR review |
| `linear-high` (P1/P2) | 30m | Advance the issue |
| `linear-low` (P3/P4) | 15m | Triage or quick action |
| `digest-high` | 30m | Deep engagement |
| `digest-low` | 15m | Quick scan |
| `p2` | 10m | Read and respond |
| `gmail` | 5m | Quick scan |
| `carryover` | null (reuse previous) | Already estimated |
| `manual` | 30m | User-added work |
| `focus` | 30m | Weekly check-in |

Story points from Linear are **not used** — most teams don't estimate consistently there.

---

## Step 3: Filter for Today

Not everything collected becomes a task.

### Always include
- Carryover (blocked or deferred from previous session)
- Critical priority items
- High priority items
- Overdue items (`overdue: true`)
- Due-today items (`due_date == session.date`)

### Include if budget allows
- Medium priority items (up to available time after higher-priority work)

### FYI items
- Go to `session.headlines[]` header, not task list
- Includes: announcements, org-wide posts, low-signal gmail

### Low priority with no deadline
- Mention in agenda footer as "Backlog awareness" section
- Do NOT create tasks for these

### Cap
- If > 10 tasks after filtering, keep top 10 by priority tier
- Excess goes to "Backlog awareness" footer
- This prevents overwhelming agendas

---

## Step 3.5: "Waiting on you" block

Before ordering tasks, identify items where someone is actively waiting on a response:

- Source is `slack-ping` (direct @-mention)
- Source is `github` (PR review request)
- Source is `linear` with a recent comment mentioning the user

Surface these as a distinct visibility block at the top of the agenda output (max 3 items). These items are also in the task list at their normal priority — the block is a reminder, not a separate queue.

```
Waiting on you:
  → Reply to Alex's integration ping (slack, 2h ago)
  → PR review for Sam (#4521, requested yesterday)
```

## Step 4: Order Tasks

Order by priority tier per `config.triage.ordering` (default: `["critical", "high", "medium", "focus"]`):

1. **Critical** items — overdue first
2. **High-priority** items — due-today first, then by Linear priority (P1 before P2)
3. **Medium-priority** items (includes carryover by default)
4. **Focus/check-in** items — end of day

Within each tier, order by: overdue → due-today → due-soon → no date.

---

## Step 5: Insert Time Structure

1. Insert `config.protected_blocks` at their fixed times
2. Insert `session.calendar_events` as protected blocks (from Runbook 8)
3. Compute budget:
   - Available time = (now → EOD) - protected blocks - calendar events
   - Estimated work = sum of all task `estimate_min` values
4. If budget < total estimates → flag as **"Over budget by ~Xm"** in agenda output
5. Tasks flow in order around protected blocks (not clock-pinned)

---

## Agenda Output Format

```
Morning Assembly — Fri 13 Mar, W11
Today: Friday, March 13 (Europe/Paris)
Focus: PROJ-400 — Team weekly focus

Headlines:
  - API response time improved 15% this week
  - New partner onboarding next Monday

Waiting on you:
  → Respond to @username ping in #team-general (slack, 3h ago)
  → Review PR #4521 for Sam (github, requested yesterday)

t1  [critical]  Fix notification loop [PROJ-670]        ~30m  (linear, overdue)
── 10:00-11:00 Team standup ──
── 10:00-12:00 Lunch break ──
t2  [high]      Review PR #4521                         ~15m  (github)
t3  [high]      Respond to @username ping in #team-general ~5m   (slack-ping)
t4  [medium]    Reply to focus thread [PROJ-398]        ~15m  (carryover)
t5  [medium]    Performance analysis [PROJ-660]         ~30m  (linear)
t6  [medium]    Weekly check-in                         ~30m  (focus)

Budget: ~2h5m work / ~5h available → Buffer: OK

Backlog awareness:
  - Write blog post + document feedback (~60m, deadline: Apr 1)
  - PROJ-680: Update test fixtures (P4, no deadline)
```

---

## Data Lifecycle

- `session.inbox_items[]` is populated during inbox sweep (checkpoint: `inbox_swept`)
- `session.inbox_items[]` is consumed during agenda build and **cleared after `agenda_built`** to keep the session JSON lean
- `session.tasks[]` is the durable output — used for the rest of the day
- `session.headlines[]` persists for the agenda header display
