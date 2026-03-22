---
name: start
description: "Start or resume the workday. Handles first-run setup, stale session recovery, morning assembly (inbox sweep → focus → agenda build → finalize), and status checks for active sessions. The two daily bookends — start opens the day, eod closes it."
argument-hint: ""
allowed-tools: Read, Write, Bash, AskUserQuestion, Agent, ToolSearch, Skill, mcp__linear-server__list_issues, mcp__linear-server__get_issue, mcp__linear-server__list_cycles, mcp__linear-server__list_comments, mcp__claude_ai_Gmail__gmail_search_messages, mcp__claude_ai_Gmail__gmail_read_message, mcp__claude_ai_Google_Calendar__gcal_list_events
---

# Start

Start or resume the workday. Routes automatically based on session state.

**Plugin root:** `${CLAUDE_PLUGIN_ROOT}`
**Transition CLI:** `${CLAUDE_PLUGIN_ROOT}/bin/transition.py`
**Morning assembly procedure:** `${CLAUDE_PLUGIN_ROOT}/docs/morning-assembly.md`
**Inbox runbooks:** `${CLAUDE_PLUGIN_ROOT}/docs/inbox-runbooks.md`
**Triage framework:** `${CLAUDE_PLUGIN_ROOT}/docs/triage-framework.md`
**State schema:** `${CLAUDE_PLUGIN_ROOT}/docs/state-schema.md`

## Dashboard Pane (every invocation)

Before routing, ensure the tmux dashboard pane is running. This runs on **every** `/start` invocation — not just during assembly.

```bash
# Ensure wrappers exist at ~/.workplanner/bin/ (persistent, user-scoped).
# transition.py's ensure_wrapper() handles this on every invocation, but
# we also run it explicitly here for the render wrapper and tmux setup.
python3 "${CLAUDE_PLUGIN_ROOT}/bin/transition.py" status > /dev/null 2>&1

if [ -n "$TMUX" ]; then
  # Re-render dashboard-view.txt from current session state
  wpl-render

  # Open pane if not already running
  if ! tmux list-panes -F '#{pane_title}' 2>/dev/null | grep -q 'workplan'; then
    tmux split-window -vb -l 15 "python3 ${CLAUDE_PLUGIN_ROOT}/bin/dashboard_tui.py"
    tmux select-pane -t '{top}' -T 'workplan'
    tmux select-pane -t '{bottom}'
  fi
fi
```

The `wpl` and `wpl-render` wrappers live at `~/.workplanner/bin/` — persistent across reboots, co-located with the data they operate on. They contain the resolved plugin path and self-heal on every invocation. Legacy symlinks at `/tmp/wp` are maintained for backward compatibility.

```bash
wpl done
wpl status
wpl-render
```

If `~/.workplanner/bin` is on the user's PATH, these short forms work directly.

If not in tmux, the wrappers are still created (useful for cross-session coordination), but the dashboard pane is skipped.

## Pre-flight Checks

Run once at the start of Morning Assembly (skip on resume if checkpoint >= initialized).

### Load deferred tools (parallel)

ToolSearch: `select:mcp__linear-server__list_issues,mcp__linear-server__get_issue,mcp__linear-server__list_cycles,mcp__linear-server__list_comments,mcp__claude_ai_Gmail__gmail_search_messages,mcp__claude_ai_Gmail__gmail_read_message,mcp__claude_ai_Google_Calendar__gcal_list_events`

### Connectivity (sequential)

1. **Linear MCP** — `list_issues` with `limit: 1`
   - OK → "Linear: ✓"
   - Fail → warn "Linear MCP needs re-auth. Focus will use digest fallback."
     Setup: `claude mcp add --transport sse --scope user linear-server https://mcp.linear.app/sse`
2. **Context MCP (Slack)** — load the `slack` provider via your organizational context MCP
   - OK → "Slack: ✓"
   - Fail → warn "Slack unavailable. Slack sweep will be skipped."
3. **Gmail MCP** — `gmail_search_messages` with `query: "is:unread newer_than:1d"`, `maxResults: 1`
   - OK → "Gmail: ✓"
   - Fail → "Gmail: ✗ (skipping)"
4. **Google Calendar MCP** — `gcal_list_events` for today
   - OK → "Calendar: ✓"
   - Fail → "Calendar: ✗ (will ask manually)"

### Report

One-line status: `MCP: Linear ✓ | Slack ✓ | Gmail ✓ | Calendar ✓` (or ✗ with reason)

Proceed to Routing. Individual runbooks already handle graceful degradation.

## Routing

Read `~/.workplanner/user.json`, `~/.workplanner/profiles/active/config.json`, and `~/.workplanner/profiles/active/session/current-session.json`. Route based on state:

```
if user.json missing (~/.workplanner/user.json)  → First-Run Setup
if config.json missing                            → First-Run Setup (profile not configured)
if session missing             → Morning Assembly
if session.date ≠ local_today  → Stale Session Handler → Morning Assembly
if checkpoint < assembly_complete → Resume Morning Assembly (skip completed steps)
if checkpoint = assembly_complete → Status Check
if checkpoint = closed         → "Yesterday's session is closed. Starting fresh." → Morning Assembly
```

**Date comparison:** When checking whether a session is stale, compare `session.date` against today's date **in the configured timezone** (`config.timezone`, e.g. `Europe/Paris`). Use `zoneinfo.ZoneInfo` to get the correct local date — do not rely on naive `datetime.now()` which can give the wrong calendar date near midnight UTC.

## First-Run Setup

Triggered when `~/.workplanner/user.json` does not exist.

### Pre-Interview: Environment Probe

Before asking any questions, silently inventory what's available:

1. **MCP availability:** Test each connected MCP server with a minimal query:
   - Linear MCP: `list_issues` with `limit: 1`
   - Gmail MCP: `gmail_search_messages` with `query: "is:unread"`, `maxResults: 1`
   - Google Calendar MCP: `gcal_list_events` for today
   - Any organizational context MCP: attempt to load a provider

   Report results: "I can see [Linear, Gmail, Calendar]. I don't have access to [Slack]."

2. **Environment heuristics (best-effort, silent on failure):**
   - `.github/` in cwd → pre-fill `github_username` from `git config user.name`
   - Linear issue refs in `git log -20` → suggest `linear_teams` from detected project keys
   - `.mcp.json` in cwd → detect configured MCP servers
   - `~/.claude/settings.json` → detect MCP server configs
   - `CLAUDE.md` in cwd → extract tool/channel mentions as suggestions

### Phase 1: Identity & Environment

Use AskUserQuestion for each:

1. "What should I call you?" → `display_name`
2. Timezone: detect from system (`date +%Z` and map to IANA), confirm: "It looks like you're in America/New_York — is that right?"
3. "When does your workday typically end?" → `eod_target` (default: "17:00" or "18:00")
4. Report MCP probe results

### Phase 2: Inboxes (GTD Capture)

The goal is to identify every "inbox" the user checks for actionable work. Ask principled questions, then map answers to available MCPs:

1. "Where does work get assigned to you? (e.g., Linear, Jira, GitHub Issues, Asana...)" → project management config
2. "Where do people reach you for quick requests? (e.g., Slack, Teams, Discord...)" → messaging config
3. "Do you check email for work items, or is it mostly noise?" → email triage config
4. "Any internal blogs, forums, or feeds you scan for relevant activity?" → feed/blog config
5. "Do you use a shared calendar for meetings?" → calendar config

For each answer, map to available MCPs. If they mention a tool but no MCP is connected: "I don't have [Slack] access yet. We can add that later — I'll work with what I have."

If an MCP is available for a mentioned tool, gather the specific config:
- Linear: "What's your Linear user ID?" (from Settings), "Which team keys?" (e.g., `["ENG", "PLATFORM"]`)
- Slack: "What's your Slack handle?", "Any team handles to watch?" → `inbox_slack_team_handles`
- GitHub: "What's your GitHub username?", "Which orgs?" → `inbox_github_orgs`
- Gmail: "Any email domains to treat as high priority?" → `inbox_gmail_priority_domains`

### Phase 3: Work Rhythms

1. "Any time blocks I should protect every day? (lunch, childcare, standup...)" → `protected_blocks`
2. "Do you do a team check-in or standup? Where does that get posted?" → determines EOD posting target (Linear sub-issue, Slack channel, etc.)
3. "What should we call this work context?" → profile name (default: "work")

### Phase 4: Terminal Environment

If not in tmux (`$TMUX` is unset):

> "I see you're using a bare terminal. If we get interrupted, you can use `claude --resume` to restore the session, or `/workplanner:start` to pick up where we left off. But I recommend tmux — if your terminal crashes, you can reopen your session with `tmux attach`, and with the `tmux-resurrect` plugin you can even recover after a computer restart. It also lets me show a live progress pane with your workplan, and I can configure mouse support so you can click between panes. Want me to help set that up?"

If in tmux: note it, set `tmux_recommended: true`.

### Phase 5: Confirmation

Show the derived config in plain language:

> "Here's what I'll do each morning:
> - Sweep [Linear] for assigned issues across [ENG, PLATFORM]
> - Check [Gmail] for unread mail (high priority from [mycompany.com])
> - Pull your [Google Calendar] events
> - Protect [12:00-13:00 Lunch] and [09:30-09:45 Standup]
> - Target EOD at [17:30] ([America/New_York])
>
> Anything to adjust?"

### Step 6: Write config

```bash
mkdir -p ~/.workplanner/profiles/{profile_name}/session/agendas/archive
mkdir -p ~/.workplanner/profiles/{profile_name}/briefings
```

Write `~/.workplanner/user.json` with:
- `schema_version: 1`
- `display_name`, `timezone`, `eod_target`
- `default_profile: "{profile_name}"`
- `non_workday_profile: null`
- `workday_schedule` (default Mon-Fri)
- `tmux_recommended`

Write `~/.workplanner/profiles/{profile_name}/config.json` with all gathered config per the schema in `${CLAUDE_PLUGIN_ROOT}/docs/state-schema.md`.

Write empty backlog: `~/.workplanner/profiles/{profile_name}/backlog.json` with `{"schema_version": 1, "items": []}`.

Create `active` symlink: `~/.workplanner/profiles/active -> {profile_name}`

Use atomic writes (tmp file → mv) for ALL JSON writes.

Confirm: "Config saved. Run `/workplanner:start` again to begin your day."

## Stale Session Handler

Triggered when `current-session.json` exists but `date` ≠ today.

1. Read the stale session
2. If `eod_posted: false` — offer a **retroactive EOD** before proceeding:
   - Show yesterday's task summary: done count, deferred/blocked count, total
   - Draft a Linear update for yesterday (same format as `/eod` Step 2)
   - Ask: "Post yesterday's update to your weekly check-in? [Post / Skip]"
   - If "Post": use Linear MCP `save_comment` on yesterday's `personal_sub_issue`. Note success.
   - If "Skip": note "Skipped retroactive EOD" and continue.
   - This prevents the recurring pattern of missing daily updates when deep work runs past EOD.
3. Extract `deferred` and `blocked` tasks as carryover candidates
4. Archive: move session to `~/.workplanner/profiles/active/session/agendas/archive/{date}.json`
5. Archive the agenda markdown too if it exists
6. Compute `sweep_since` from the stale session's `eod_target` on its `date`
7. Proceed to Morning Assembly with carryover list and `sweep_since`

## Morning Assembly

Run Pre-flight Checks if this is a fresh assembly (not a resume past `initialized`).

Four checkpointed steps. On resume, skip steps where checkpoint already recorded.

*Detailed procedures: `${CLAUDE_PLUGIN_ROOT}/docs/morning-assembly.md`*
*Individual runbook specs: `${CLAUDE_PLUGIN_ROOT}/docs/inbox-runbooks.md`*
*Triage methodology: `${CLAUDE_PLUGIN_ROOT}/docs/triage-framework.md`*

### Initialize session

Create `current-session.json` with the schema from `${CLAUDE_PLUGIN_ROOT}/docs/state-schema.md`:
- `checkpoint: "initialized"`, `current_task_index: null`, `tasks: []`, `inbox_items: []`, `headlines: []`
- Set `sweep_since` (from stale session handler or computed fresh)

Use atomic writes (tmp file → mv) for ALL state mutations.

### Step 0.5: Pre-work activity scan

If `config.triage.pre_work` exists, scan for work the user already did this morning before running `/start`. This recognizes the natural pattern of checking Slack, replying to threads, and reading team blogs before formal planning.

1. **Compute scan window:** `config.triage.pre_work.scan_from` (default: `"06:00"`) today → now
2. **Scan each source** in `config.triage.pre_work.sources` (default: `["slack", "p2"]`):
   - **Slack:** Use context MCP slack `search` with `from:me` in the scan window (use `days: 1`). Count distinct threads replied to, messages sent, reactions given. Catches all channels including DMs and ad-hoc threads.
   - **Team blogs:** Use context MCP blog provider `search` with `author: config.user_display_name`, `date_from: today`. Count posts and comments.
3. **Estimate time spent:** count of distinct interactions × 2-3 minutes (rough heuristic — a Slack reply is ~2m, a blog comment ~3m)
4. **If estimated time >= `config.triage.pre_work.min_minutes_for_task`** (default: 5):
   - Insert a completed task at position 0:
     ```bash
     wpl add "Morning communication work" --est {estimated_min} --done --started {scan_from} --finished {now_hhmm} --at top --notes "Exclude from team updates. {summary_of_activity}"
     ```
   - The summary lists key interactions: "3 Slack threads, 1 blog comment"
5. **If activity found but below threshold:** add to headlines: "Pre-work: {N} Slack replies, {N} blog comments"
6. **If no activity found or config absent:** skip silently.

This step runs before the inbox sweep so the pre-work task appears at position 0 in the final agenda.

### Step 1: Inbox Sweep (checkpoint: `inbox_swept`)

Execute all 9 runbooks sequentially per `${CLAUDE_PLUGIN_ROOT}/docs/inbox-runbooks.md`:

0. **Backlog sweep** — Read `~/.workplanner/profiles/active/backlog.json`. Auto-promote items to `inbox_items[]`:
   - Items with `target_date <= today` (and `not_before` is null or `<= today`): add with `source: "backlog"`, `priority: "high"`. Remove from backlog.json after promotion.
   - Items with `deadline` and no `target_date`: if deadline is ≤2 days away, add with `source: "backlog"`, `priority: "critical"`, `overdue: true` if past. Remove from backlog.json.
   - Items with `deadline` 3-7 days out: include in "Backlog awareness" footer (see agenda format below), not promoted.
   - Undated items: skip (only surface via `/horizon` review).
1. **Digest** — Check/generate digest, parse priority-tiered action items
2. **Linear Issues** — Assigned issues in active states across `config.linear_teams`
3. **Linear Inbox** — Notifications/mentions via context MCP
4. **Slack** — 4 layers: direct pings → team pings → channel activity → announcements
5. **Blog/feed activity** — Recent posts from `config.inbox_p2s`
6. **GitHub** — PRs requesting review or mentioning user (username from `config.user.github`)
7. **Gmail** — Unread non-promotional emails (quick scan)
8. **Calendar** — Today's events → `session.calendar_events[]`

Each runbook appends to `session.inbox_items[]`. Failures log a warning and continue. After all runbooks complete, set checkpoint to `inbox_swept`.

**Carryover:** Deferred/blocked tasks from stale session are also added to `inbox_items[]` with `source: "carryover"` and their previous priority/estimate.

**Backlog staleness nudge:** If backlog has items >14 days old and `last_reviewed` is >7 days ago (or null), append to headlines: "Backlog has N stale items — run `/horizon stale` to review."

### Step 2: Load weekly focus (checkpoint: `focus_loaded`)

If `config.integrations.focus_skill` is configured, invoke it (use the Skill tool) and extract `focus_issue` and `personal_sub_issue` from the output. Otherwise, query Linear MCP directly using `config.weekly_focus` parameters (label, team, sub-issue pattern).

**If Linear MCP unavailable:** Skip gracefully. Note: "Couldn't load focus issue."

### Step 3: Build agenda (checkpoint: `agenda_built`)

Apply the triage framework from `${CLAUDE_PLUGIN_ROOT}/docs/triage-framework.md`. Priority mappings and estimates come from `config.triage` (see `${CLAUDE_PLUGIN_ROOT}/docs/state-schema.md` for schema and defaults).

1. **Deduplicate** — Match by `dedupe_key`. Merge: keep highest priority, combine context
2. **Triage & Prioritize** — Assign priority tiers using `config.triage.source_priority` (or defaults). Assign estimates using `config.triage.estimates` (or defaults). Runtime overrides: `overdue: true` → critical; `due_date == today` → at least high.
3. **"Waiting on you"** — Identify items where someone is blocked on a response (source: `slack-ping`, `github` review request, `linear` with recent user-mentioning comment). Surface as a distinct block in the agenda output (max 3). These are also in the task list at their normal priority.
4. **Deferral reckoning** — For carryover items where `deferral_count >= config.triage.deferrals.reckoning_threshold` (default: 3), present a reckoning prompt: break down, delegate, drop, timebox, or keep. Apply the user's decision before proceeding.
5. **Filter** — Always: critical, high, overdue, due-today. Medium if budget allows. Cap at `config.triage.filter.task_cap` (default: 10) tasks.
6. **Order** — Per `config.triage.ordering` (default: critical → high → medium → focus). Carryover ordered by its assigned tier, not auto-first.
7. **Time structure** — Insert `config.protected_blocks` + `session.calendar_events`. Compute budget.

After triage, **clear `session.inbox_items[]`** to keep session lean. Set checkpoint to `agenda_built`.

### Step 4: Finalize (checkpoint: `assembly_complete`)

1. Write `session.tasks[]` to state (atomic write)
2. Set first pending task to `in_progress`, set `current_task_index`
3. Re-render the dashboard:
   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/bin/render_dashboard.py"
   ```
4. Output the agenda:

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
  - Write blog post + document feedback (~60m, deadline: Apr 1)
  - Onboarding prep (~45m, deadline: Mar 22, due soon)
  - PROJ-680: Update test fixtures (P4, no deadline)
```

5. Quick fallback: "Anything not on your calendar?" (replaces old "Any meetings today?" since calendar events are now swept automatically)

No "anything to add?" — the user speaks up if needed.

## Status Check

Default when assembly is complete and no args. Display concisely:

```
▶ t2 — API integration review (~2h, 45m elapsed)
Done: 1/7 │ ~4h25m left │ EOD: 18:00 │ Buffer: OK
```

## Task Transitions During the Day

Use `wpl` (or `~/.workplanner/bin/wpl` if not on PATH) for all task state changes:

```bash
wpl done
wpl blocked "waiting on API access"
wpl defer
wpl defer --until friday          # defer to backlog with target date
wpl add "Review PR #4521" --est 30
wpl add "1:1 with Ash" --est 30 --at top --done --started 09:00 --finished 09:30
wpl move t5 --to t2
wpl switch t4
wpl switch t4 --no-pause          # keep previous task in_progress (parallel work)
wpl backlog "Future task" --est 30 --target next-week
wpl status
```

**During the day, always use `wpl`** (or `~/.workplanner/bin/wpl`) for task state changes — it handles atomic writes, validation, and re-rendering. During assembly and EOD, write JSON directly with atomic writes (tmp → mv).

## Error Handling

- **Context MCP unavailable:** Skip that data source, note it, continue.
- **Linear MCP unavailable:** Skip focus loading and Linear runbooks. Note: "Linear MCP not available."
- **Gmail MCP unavailable:** Skip Gmail runbook. Note: "Gmail: skipped."
- **Calendar MCP unavailable:** Skip calendar runbook, fall back to "Any meetings today?" prompt.
- **Corrupt session JSON:** Offer: "Session file is corrupt. Start fresh or try to recover?"
- **Not in tmux:** Skip dashboard pane. Note: "Not in tmux — dashboard skipped."
- **Individual runbook failure:** Log warning, continue to next runbook. Assembly never blocks on a single source.
