---
name: start
description: "Start or resume the workday. Handles first-run setup, stale session recovery, morning assembly (inbox sweep → focus → agenda build → finalize), and status checks for active sessions. The two daily bookends — start opens the day, eod closes it."
argument-hint: ""
allowed-tools: Read, Write, Bash, AskUserQuestion, Agent, ToolSearch, Skill
---

# Start

Start or resume the workday. Routes automatically based on session state.

**Plugin root:** `${CLAUDE_PLUGIN_ROOT}`
**Transition CLI:** `${CLAUDE_PLUGIN_ROOT}/bin/transition.py`
**Morning assembly procedure:** `${CLAUDE_PLUGIN_ROOT}/docs/morning-assembly.md`
**Inbox runbooks:** `${CLAUDE_PLUGIN_ROOT}/docs/inbox-runbooks.md`
**Triage framework:** `${CLAUDE_PLUGIN_ROOT}/docs/triage-framework.md`
**State schema:** `${CLAUDE_PLUGIN_ROOT}/docs/state-schema.md`

## Profile resolution

Before any file reads / writes that reference profile state, resolve the concrete profile root **once** and reuse it as `PROFILE_ROOT`. Do not hardcode `~/.workplanner/profiles/active/…` — the `active` symlink is no longer the source of truth:

```bash
PROFILE_ROOT=$(wpl profile whoami --print-root)
```

All paths in this skill should be expressed relative to `$PROFILE_ROOT`. Subprocesses that this skill launches (`transition.py`, `handoff.py`) do their own path-based resolution automatically.

## Dashboard Pane (every invocation)

Before routing, ensure the tmux dashboard pane is running according to the profile's policy. Wrapper installation and dashboard-view re-render always happen — they cost nothing and are useful for non-tmux coordination (dashboards can be tailed from other terminals). Pane spawning is gated on `config.dashboard_pane`:

- `"auto"` (default): spawn the pane if `$TMUX` is set and no `workplan`-titled pane already exists.
- `"never"`: never spawn the pane, even in tmux. Wrappers and dashboard-view re-render still happen.
- `"always"`: attempt to spawn even outside tmux. If `$TMUX` is unset, log a one-line note and proceed.

```bash
# Ensure wrappers exist at ~/.workplanner/bin/ (persistent, user-scoped).
# transition.py's ensure_wrapper() handles this on every invocation, but
# we also run it explicitly here for the render wrapper and tmux setup.
python3 "${CLAUDE_PLUGIN_ROOT}/bin/transition.py" status > /dev/null 2>&1

# Read config.dashboard_pane (default "auto") via `wpl config get dashboard_pane`,
# or parse config.json directly. Absent or unrecognized value → treat as "auto".
DASHBOARD_PANE_POLICY="auto"  # replace with the resolved config value

case "$DASHBOARD_PANE_POLICY" in
  never)
    # Skip pane-spawn entirely. Still re-render the view so the file stays
    # current for any external readers.
    wpl-render
    ;;
  always)
    wpl-render
    if [ -z "$TMUX" ]; then
      echo "dashboard_pane=always but not inside tmux — skipping spawn."
    else
      if ! tmux list-panes -F '#{pane_title}' 2>/dev/null | grep -q 'workplan'; then
        tmux split-window -vb -l 15 "python3 ${CLAUDE_PLUGIN_ROOT}/bin/dashboard_tui.py"
        tmux select-pane -t '{top}' -T 'workplan'
        tmux select-pane -t '{bottom}'
      fi
    fi
    ;;
  auto|*)
    if [ -n "$TMUX" ]; then
      wpl-render
      if ! tmux list-panes -F '#{pane_title}' 2>/dev/null | grep -q 'workplan'; then
        tmux split-window -vb -l 15 "python3 ${CLAUDE_PLUGIN_ROOT}/bin/dashboard_tui.py"
        tmux select-pane -t '{top}' -T 'workplan'
        tmux select-pane -t '{bottom}'
      fi
    fi
    ;;
esac
```

The `wpl` and `wpl-render` wrappers live at `~/.workplanner/bin/` — persistent across reboots, co-located with the data they operate on. They contain the resolved plugin path and self-heal on every invocation. Legacy symlinks at `/tmp/wp` are maintained for backward compatibility.

```bash
wpl done
wpl status
wpl-render
```

If `~/.workplanner/bin` is on the user's PATH, these short forms work directly.

Rationale: a user can be in tmux but not want the dashboard pane (screen real-estate, prefer a side terminal, screen-recording). `"auto"` preserves today's behavior; `"never"` is the opt-out without leaving tmux; `"always"` exists for users whose terminals split outside of tmux or who want the pane spawn attempt to happen regardless of ambient state.

## Pre-flight Checks

Run once at the start of Morning Assembly (skip on resume if checkpoint >= initialized).

The pre-flight verifies the integrations the user's profile actually uses. Do **not** hardcode a preload list of tools or a fixed sequence of provider-specific calls — those assume a particular integration shape that will not match every user. Derive the check surface from the profile's declared inbox sources and integrations.

### Procedure

1. Inspect the active profile's `config.json` to enumerate the integrations the morning assembly will touch (e.g. project-management, messaging, email, calendar, organizational-context). Each declared integration corresponds to one or more runbooks in `docs/inbox-runbooks.md`.
2. For each integration, load the tool schemas needed to exercise it (via `ToolSearch` with whatever selectors match the user's available MCPs), then perform a minimal round-trip that exercises auth and basic reachability — the cheapest call available that proves the integration is usable.
3. Record the result as `✓` or `✗ (reason)` per integration. Failures are non-blocking; runbooks already handle graceful degradation.

### Report

One-line status keyed by integration name, e.g. `Integrations: project-mgmt ✓ | messaging ✓ | email ✓ | calendar ✓` (or ✗ with reason). Use the labels the user's config declares.

Proceed to Routing.

## Routing

Resolve the active profile via `wpl profile whoami` (reads cwd, falls back to `--profile`/`$WPL_PROFILE` overrides, then single-profile fallback). Read `~/.workplanner/user.json`, the resolved profile's `config.json`, and the resolved profile's `session/current-session.json`. Route based on state:

```
if user.json missing (~/.workplanner/user.json)  → First-Run Setup
if `wpl profile whoami` returns "(unresolved)"   → Workspace Association (see below)
if config.json missing                            → First-Run Setup (profile not configured)
if profile has no workspaces and >1 profile exists → prompt to associate cwd (non-blocking warning)
if session missing             → Morning Assembly
if session.date ≠ local_today  → Stale Session Handler → Morning Assembly
if checkpoint < assembly_complete → Resume Morning Assembly (skip completed steps)
if checkpoint = assembly_complete → Status Check
if checkpoint = closed         → "Yesterday's session is closed. Starting fresh." → Morning Assembly
```

### Workspace Association

If `wpl profile whoami` prints `profile: (unresolved)` (cwd doesn't match any profile and single-profile fallback doesn't apply), prompt the user before continuing:

> "This directory (`<cwd>`) isn't associated with any profile. Your profiles are: `<list>`. Options: (1) associate this directory with an existing profile, (2) create a new profile rooted here, (3) run this session against a specific profile via `--profile NAME`."

On (1) or (2), run the corresponding `wpl profile associate` / `wpl profile create --workspace` and re-run `wpl profile whoami` to confirm. On (3), prefix subsequent `wpl` calls with `--profile NAME` for this session.

**Date comparison:** When checking whether a session is stale, compare `session.date` against today's date **in the configured timezone** (`config.timezone`, e.g. `Europe/Paris`). Use `zoneinfo.ZoneInfo` to get the correct local date — do not rely on naive `datetime.now()` which can give the wrong calendar date near midnight UTC.

## First-Run Setup

Triggered when `~/.workplanner/user.json` does not exist.

### Pre-Interview: Environment Probe

Before asking any questions, silently inventory what's available:

1. **Integration availability:** For each connected MCP server, perform the cheapest call that proves auth and basic reachability — a minimal list or read on the most common resource the server exposes. Report back the integrations you can see and the ones you cannot. Do not assume any particular MCP shape; describe what's available in the user's terms (e.g. "I can see project management, email, calendar; I don't see messaging").

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

Prefer `wpl profile create <name> --workspace <cwd>` so the new profile is immediately resolvable by cwd (path-based resolution — see `docs/profiles.md`). The command creates the profile directory, writes a minimal `config.json` with `workspaces: [cwd]`, and sets the `active` symlink if none exists.

After `wpl profile create`, populate the rest of the config:

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

Update `~/.workplanner/profiles/{profile_name}/config.json` to include the rest of the gathered settings per the schema in `${CLAUDE_PLUGIN_ROOT}/docs/state-schema.md`. Preserve the `workspaces` list written by `wpl profile create`.

Write empty backlog: `~/.workplanner/profiles/{profile_name}/backlog.json` with `{"schema_version": 1, "items": []}`.

Use atomic writes (tmp file → mv) for ALL JSON writes.

Confirm: "Config saved. Run `/workplanner:start` again to begin your day."

## Stale Session Handler

Triggered when `current-session.json` exists but `date` ≠ today.

1. Read the stale session.

2. **If `eod_handoff_written` is absent or `false` — backfill a local handoff for the stale date.**

   The handoff path is workplanner-owned: `~/.workplanner/profiles/<resolved-name>/handoffs/{stale_session.date}.md`. Check whether this session's backfill contribution already exists. Resolve the path and inspect it via `bin/handoff.py`:

   ```bash
   STALE_DATE="<stale_session.date>"
   python3 "${CLAUDE_PLUGIN_ROOT}/bin/handoff.py" read --date "$STALE_DATE"
   ```

   The read output is a JSON blob with `sections` keyed by section name → session-id → body. Look for a sub-section whose session-id starts with `stale-recovery-` under any section (most commonly `Session trajectory` or `Deferred with reasons`).

   - **If a `stale-recovery-*` sub-section is already present for this date:** log "Handoff already written for {stale_session.date}" and continue to step 3. This makes re-running `/start` on the same stale state idempotent — we do not rewrite an already-backfilled handoff.
   - **Otherwise:** draft a structured handoff from the stale session and write it via `handoff.py write` with a session-id that is visibly distinct from normal-path IDs:
     ```bash
     python3 "${CLAUDE_PLUGIN_ROOT}/bin/handoff.py" write \
       --session-id "stale-recovery-$STALE_DATE" \
       --date "$STALE_DATE" \
       --trajectory "<done / deferred / blocked summary + notes + scope decisions>" \
       --deferred-json '[{"title":"...","uid":"...","reason":"..."}]' \
       --open-questions "<open threads noted in the session>" \
       --context "<any pointers for today that the stale session implies>"
     ```
     Draft each section from the stale session JSON (done / deferred / blocked counts + titles, any `defer_reason`s, task notes, scope decisions). When the session JSON alone is thin, augment with:
     - Linear `completedAt` / recent-updates for the session's date window (for tasks that moved state after the session last wrote).
     - Relevant repo state — new directories, uncommitted files — if a task in the stale session was clearly mid-work.

     Log: "Backfilled handoff: {path}".

   No prompt. The backfill is silent recovery — the file IS the handoff artifact, and Step 0.25 below will read it the same way it reads a normal-path handoff written by yesterday's `/eod`.

3. **No retroactive external posting.** The backfilled handoff from step 2 is the recovery artifact, period. Do not draft or prompt to post a retroactive check-in to any external system (project-management, messaging, or otherwise) on the user's behalf — a missed day that rolls over is a signal to move on, not to retroactively catch up public channels. If the user wants to post something about yesterday, they'll do it themselves.

4. Extract `deferred` and `blocked` tasks as carryover candidates. Prefer the handoff file (via `handoff.py read`) over the raw session JSON when both are available — a scope pivot captured in the handoff overrides the stale session's original task titles and reasons.
5. Archive: move the session to `$PROFILE_ROOT/session/agendas/archive/{date}.json`.
6. Archive the agenda markdown too if it exists.
7. Compute `sweep_since` from the stale session's `eod_target` on its `date`.
8. Proceed to Morning Assembly with carryover list and `sweep_since`.

Why this exists: missed EODs otherwise cause stale task framings to carry forward unchanged for multiple days. The backfilled handoff captures scope pivots, decisions, and context that session JSON alone cannot reconstruct the morning after — and because it lands at the same path as a normal-path handoff, Step 0.25 finds it the next morning without any special-case logic.

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

### Step 0.25: Read yesterday's handoff doc

Before the inbox sweep, read yesterday's local handoff doc (written by `/eod` the previous day):

```bash
# Resolve yesterday's date in the profile's timezone (same logic as sweep_since).
YESTERDAY=$(python3 -c "
from datetime import timedelta
import json, os, sys
sys.path.insert(0, '${CLAUDE_PLUGIN_ROOT}/bin')
from transition import local_today, load_config
print((local_today(load_config()) - timedelta(days=1)).isoformat())
")
python3 "${CLAUDE_PLUGIN_ROOT}/bin/handoff.py" read --date "$YESTERDAY"
```

The output is a JSON blob with `sections` (aggregated across all session sub-sections), `deferred` (parsed list of `{title, uid, reason, session}` dicts), and `raw` (the full markdown for fallback).

**What to do with it:**

- **Deferred list** (`.deferred`) feeds the carryover mini-triage (see Step 3 below).
- **Open questions** → include in the morning headlines so they surface early ("Open from yesterday: <question>").
- **Context for tomorrow** → treat as LLM-addressed notes to self; read once, apply as pre-conditioning for the day ahead. Optionally surface top items in headlines.
- **Session trajectory** → contextual only; used to inform decisions but not re-shown verbatim in the agenda.

If the file is missing (fresh install, or yesterday's /eod didn't run), proceed with a log line "No handoff for yesterday" and rely on session-JSON carryover as before.

### Step 0.5: Pre-work activity scan

If `config.triage.pre_work` exists, scan for work the user already did this morning before running `/start`. This recognizes the natural pattern of checking Slack, replying to threads, and reading team blogs before formal planning.

1. **Compute scan window:** `config.triage.pre_work.scan_from` (default: `"06:00"`) today → now
2. **Scan each source** in `config.triage.pre_work.sources` (default: `["slack", "p2"]`):
   - **Slack:** Use context MCP slack `search` with `from:me` in the scan window (use `days: 1`). Count distinct threads replied to, messages sent, reactions given. Catches all channels including DMs and ad-hoc threads.
   - **Team blogs:** Use context MCP blog provider `search` with `author: config.user_display_name`, `date_from: today`. Count posts and comments.
3. **Estimate time spent:** count of distinct interactions × 2-3 minutes (rough heuristic — a Slack reply is ~2m, a blog comment ~3m)
4. **If any activity is found:** add a single headline line summarizing it: "Pre-work: {N} Slack replies, {N} blog comments". The estimated-minutes total is included parenthetically if it exceeds `config.triage.pre_work.min_minutes_for_task` (default: 5).
5. **If no activity found or config absent:** skip silently.

The scan produces a **headline only**. It does not insert a synthetic "Morning communication work" task into the agenda — the pre-work happened before the session started and isn't agenda work; padding the done-count with it distorts the day's actual task load. The headline keeps the signal visible without the padding.

### Step 1: Inbox Sweep (checkpoint: `inbox_swept`)

Execute all 9 runbooks sequentially per `${CLAUDE_PLUGIN_ROOT}/docs/inbox-runbooks.md`:

0. **Backlog sweep** — Read `$PROFILE_ROOT/backlog.json`. Auto-promote items to `inbox_items[]`:
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
4. **Carryover surfacing** — Present every carryover task's `defer_reason` alongside the task so the user can see the anchor without having to answer a prompt. Behavior splits on `deferral_count`, honoring methodology principles #4 (Carryover Earns Its Place: light-touch re-evaluation) and #5 (Force the Reckoning: heavy prompt at threshold):

   - **Below threshold** (`deferral_count < config.triage.deferrals.reckoning_threshold`, default 3): surface read-only. No prompt. The user speaks up if they want to change anything, per the existing "No 'anything to add?'" convention at Step 4.5. Example:

     ```
     Carryover from yesterday:
     1. Review Stéphane's PR (t3 est ~30m, ref: HAL-123)
        Reason yesterday: "waiting on Stéphane to push fixture refactor"
     2. Update API docs (t5 est ~15m)
        Reason yesterday: "underspecified — what do we actually want to document?"
     ```

   - **At or above threshold:** fire the full reckoning prompt (break down, delegate, drop, timebox, or keep). This is the principle #5 case — enough deferrals to demand a decision, not another light-touch pass. Apply the user's answer via the appropriate `wpl` command (`wpl remove t<N>`, `wpl backlog --from t<N> --target <date>`, `wpl reckon <choice> ...`, or leave in place). If the user defers again with a new reason, pass it via `wpl defer --reason "..."` so the updated reason lives on the task.

   The read-only surfacing is not a demotion of the carryover's importance — it's an acknowledgment that a fresh `defer_reason` from <24h ago is already the sweep's answer to "does this still matter?".
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

5. **Calendar fallback prompt — only if the sweep failed.** If the calendar runbook (Runbook 8) returned events successfully, do not ask "Anything not on your calendar?" — the mechanical sweep already answered. The fallback prompt fires only when the calendar integration was unavailable or the runbook errored. Log the trigger inline with the agenda output when it does fire: "Calendar sweep unavailable — anything not on your calendar?"

No "anything to add?" — the user speaks up if needed. The assembly output is designed to be read, not to solicit confirmation at every step.

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
wpl add "Sub-step" --est 20 --parent t1     # nested under t1; see "Sub-tasks" in docs/task-transitions.md
wpl move t5 --to t2
wpl switch t4
wpl switch t4 --no-pause          # keep previous task in_progress (parallel work)
wpl backlog "Future task" --est 30 --target next-week
wpl status
```

**Parent/child for project work.** When a task rolls up several related sub-steps that share context and a single gate (an RSM milestone, a PR checklist, an incident response), create a parent and add the sub-steps with `--parent <id>` rather than as flat siblings. The dashboard renders them as a tree. Flat siblings still read better for a normal day's mixed-topic agenda.

**During the day, always use `wpl`** (or `~/.workplanner/bin/wpl`) for task state changes — it handles atomic writes, validation, and re-rendering. During assembly and EOD, write JSON directly with atomic writes (tmp → mv).

## Error Handling

- **Context MCP unavailable:** Skip that data source, note it, continue.
- **Linear MCP unavailable:** Skip focus loading and Linear runbooks. Note: "Linear MCP not available."
- **Gmail MCP unavailable:** Skip Gmail runbook. Note: "Gmail: skipped."
- **Calendar MCP unavailable:** Skip calendar runbook, fall back to "Any meetings today?" prompt.
- **Corrupt session JSON:** Offer: "Session file is corrupt. Start fresh or try to recover?"
- **Not in tmux:** Skip dashboard pane. Note: "Not in tmux — dashboard skipped."
- **Individual runbook failure:** Log warning, continue to next runbook. Assembly never blocks on a single source.
