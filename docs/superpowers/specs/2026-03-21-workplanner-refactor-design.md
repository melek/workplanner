# Workplanner Refactor: Engine/Methodology Separation & Genericization

**Date:** 2026-03-21
**Status:** Draft
**Scope:** Architecture, methodology documentation, private context removal, profile system, setup redesign, decision log

---

## Definitions

- **Engine:** The CLI and state management layer (`bin/`). Methodology-agnostic. Handles task CRUD, profiles, atomic writes, dashboard rendering.
- **Methodology document:** `docs/methodology.md` — the philosophy, principles, and design rationale. The "why."
- **Methodology defaults:** The config values that implement the reference methodology (e.g., `triage.filter.task_cap: 10`). The "what."
- **Customization:** A user's deviation from methodology defaults, recorded in the decision log with rationale.
- **Profile:** An artifact container with its own config, session, backlog, and briefings. Scoped to a life context (work, home, etc.).
- **Session:** The daily schedule. Lives in the active profile's directory. One session per day per profile.
- **Inbox runbook:** A documented procedure for sweeping one data source. Parameterized by profile config.
- **Integration:** An inbox runbook plus its MCP binding. "Runbook" is the procedure; "integration" is runbook + config + MCP.

---

## Problem Statement

Workplanner was extracted from a private Automattic-internal repo (`a8c-workplanner`). The current codebase contains:

- Hardcoded usernames, team handles, and org-specific references throughout skills, runbooks, and docs
- An implicit productivity methodology scattered across multiple files with no unified articulation
- A flat data directory (`~/work-planning/`) with no support for multiple life contexts (work, home, side projects)
- A setup flow that assumes specific integrations (Linear, Slack via context-a8c, P2 blogs) rather than discovering what's available

The goal is a clean, generic, opinionated-by-default productivity tool that any Claude Code user can install and benefit from immediately.

---

## Architecture: Engine / Methodology / Integration

Three concerns, cleanly separated:

### Engine (bin/)

The CLI and state management layer. Methodology-agnostic. Handles:

- Task CRUD and status transitions (pending → in_progress → done | blocked | deferred)
- Backlog management with temporal targeting (target dates, deadlines, not-before dates)
- Profile management (create, switch, list, active symlink)
- Decision log CRUD (add, list, remove, explain)
- Config get/set with mandatory decision logging
- Dashboard rendering from session state
- Event queue for dashboard alerts
- Atomic writes (tmp → mv) for all state mutations
- Undo log (last 20 mutations)
- Session freeze/restore for tmux persistence

The engine enforces structural rules mechanically: one task in_progress at a time, valid status transitions, atomic file operations. It does not reason about priorities, triage policy, or what tasks mean. Verbose error messages guide the LLM back on track when rules are violated.

**Profile resolution:** The engine resolves all state paths through the active profile symlink. The current hardcoded path constants (`SESSION`, `CONFIG`, `BACKLOG`, `UNDO_LOG`, `ARCHIVE_DIR`) are replaced by a `resolve_root()` function that follows `~/.workplanner/profiles/active/` to find the profile directory, then derives paths relative to it. This is the most significant structural change to the engine — not a rewrite, but a refactor of the foundation layer (~300-400 lines of additions to the current ~1265).

### Methodology (docs/methodology.md + config defaults)

The reference methodology — a documented productivity philosophy plus default config values that implement it. Skills are written to follow the reference methodology. Users modify it conversationally through the workplanner, which records changes in the decision log.

The methodology is the "reference runbook" — opinionated defaults that work well for busy and neurodivergent knowledge workers. A user who prefers bullet journal productivity, strict Pomodoro, or a different framework can adjust the methodology through config changes and skill modifications.

See [Section: Methodology Document](#methodology-document) for full content.

### Integrations (inbox runbooks + profile config)

Fully config-driven. No hardcoded usernames, MCP server names, or org-specific assumptions. Each inbox runbook is a procedure template parameterized by profile config. Adding a new data source means writing a runbook doc and mapping it in config — no Python changes.

---

## Data Root & Profile System

### Data Root

All workplanner state lives at `~/.workplanner/`. This is user data, not plugin cache — it persists independently of Claude Code installation. The plugin's `${CLAUDE_PLUGIN_DATA}` directory is reserved for transient plugin state (dependency caches, etc.), not user work artifacts.

### Directory Structure

```
~/.workplanner/
  user.json                          # Cross-profile identity & preferences
  decision-log.json                  # Methodology deviations with rationale
  profiles/
    active -> work/                  # Symlink to default profile
    work/
      config.json                    # MCP bindings, inbox sources, triage config
      session/
        current-session.json         # Today's schedule
        dashboard-view.txt           # Rendered dashboard (derived)
        events.json                  # Dashboard alert queue
        agendas/
          archive/                   # Archived sessions
      backlog.json                   # Future-scoped items
      undo.jsonl                     # Mutation undo log (profile-scoped)
      briefings/
        2026-03-21/                  # Pre-generated task briefings by date
    home/
      config.json
      session/
        ...
      backlog.json
      briefings/
  bin/
    wpl                              # CLI wrapper (resolved plugin path)
    wpl-render                       # Dashboard render wrapper
  sessions.json                      # Freeze/restore state (user-level)
```

### Profile Semantics

**Profiles are artifact containers.** Each profile has its own config (MCP bindings, inbox sources, triage settings), session state, backlog, and briefings. A "work" profile connects to Linear and Slack; a "home" profile connects to a personal calendar and todoist.

**Sessions are scoped to one profile.** The daily session lives in the active profile's directory. All tasks in a session belong to that profile. To work on a different context, switch profiles. This keeps state management simple — no cross-profile atomic writes, no ambiguous artifact ownership.

**Future consideration:** Cross-profile task references (e.g., "call plumber" from home backlog surfacing in a work session) may be added later (see `docs/future-work.md`). For now, switching profiles is the mechanism for switching contexts.

**Profile switching:** The `active` symlink points to the current profile. The user switches conversationally ("switch to home") or via `wpl profile switch home`. When multiple profiles exist, `/start` checks `user.json` workday schedule to auto-select the appropriate profile for the day. Manual switches persist until the next day's `/start`.

**What happens when switching profiles mid-day:** The current session (if any) remains in its profile directory, untouched. The `active` symlink updates to the new profile. `wpl status` now shows the new profile's session (which may be empty or stale). The user can switch back at any time — the previous session is still there. Sessions are never suspended or migrated.

**Profile config inherits from user.json.** Timezone, display name, and EOD preferences can be set at user level and overridden per profile. This means `home` could have EOD at 21:00 while `work` has 18:00.

**First-run creates one profile.** Additional profiles are created conversationally later.

**Profile deletion:** `wpl profile delete <name>` removes a profile directory after confirmation. Refuses to delete the active profile (switch first) and refuses to delete the last remaining profile. If the deleted profile is `default_profile` in `user.json`, the system prompts for a new default.

**Auto-selection algorithm** (in `/start`, not the engine):
1. If only one profile exists, use it. Skip schedule check.
2. Read `user.json.workday_schedule` for today's day-of-week.
3. If workday=true, select `default_profile`.
4. If workday=false, select `non_workday_profile` (if configured, otherwise `default_profile`).
5. If the selected profile differs from the current `active` symlink, update it and note: "Switching to {name} profile for today."
6. Manual switches via `wpl profile switch` override until the next day's `/start`.

### user.json Schema

Cross-profile identity and preferences. Created during first-run setup.

```json
{
  "schema_version": 1,
  "display_name": "Lionel",
  "timezone": "Europe/Paris",
  "eod_target": "18:00",
  "default_profile": "work",
  "workday_schedule": {
    "monday": true, "tuesday": true, "wednesday": true,
    "thursday": true, "friday": true, "saturday": false, "sunday": false
  },
  "tmux_recommended": true
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `schema_version` | integer | yes | Current: 1 |
| `display_name` | string | yes | User's preferred first name |
| `timezone` | string | yes | IANA timezone (e.g., `"Europe/Paris"`) |
| `eod_target` | string (HH:MM) | yes | Default EOD, overridable per profile |
| `default_profile` | string | yes | Profile name used when the `active` symlink is missing or broken, and selected on workdays by auto-selection. Manual switches via `wpl profile switch` persist until the next day's `/start`. |
| `non_workday_profile` | string \| null | no | Profile to auto-select on non-workdays. If null, `default_profile` is used. |
| `workday_schedule` | object | no | Day-of-week → boolean. Used by `/start` for automatic profile selection when multiple profiles exist. |
| `tmux_recommended` | boolean | no | Whether tmux setup was offered/accepted |

Profile configs inherit `timezone` and `eod_target` from user.json unless they define their own.

---

## Methodology Document

Located at `docs/methodology.md`. This is the philosophical anchor — the "why" behind every design decision.

### Core Principles

#### 1. Capture Exhaustively, Decide Once

Sweep all inboxes automatically. The human never manually checks sources — the system does it and presents a unified view. This eliminates the "did I miss something?" anxiety loop that drains executive function before real work begins.

**Lineage:** GTD's capture/collect phase. The key insight is that *incomplete capture* is more stressful than *a long list* — the brain keeps cycling on what it might have missed.

#### 2. Two Bookends, Nothing Between

`/start` and `/eod` are the only user-facing ceremonies. During the day, task transitions happen through atomic CLI commands invoked by the LLM. The user's cognitive overhead is: look at the agenda, do the work, say "done" or "next."

**Lineage:** Time-blocking's boundary rituals, with mid-day friction eliminated. The LLM handles `/pickup` transitions, dashboard updates, and state management transparently.

#### 3. Timebox, Don't Estimate

"30 minutes" means "spend 30 minutes advancing this," not "finish in 30 minutes." This reframe eliminates estimation anxiety and the paralysis of "this is too big to start." Any task can be timeboxed to 15-30 minutes regardless of total scope.

**Lineage:** Pomodoro's fixed-interval philosophy applied to task estimation. The psychological barrier to starting drops dramatically when the commitment is bounded.

#### 4. Carryover Earns Its Place

Deferred tasks return at medium priority, not top-of-list. They compete for the agenda on merit alongside fresh items. Data shows carryover tasks complete at ~41% vs ~81% for manually-added tasks — forcing them first doesn't improve this. They were deferred for a reason.

**Lineage:** Bullet journal's migration concept — the act of re-evaluating whether to carry something forward is itself valuable signal.

#### 5. Force the Reckoning

After N deferrals (configurable, default 3), the system demands a decision: break it down, delegate, drop, timebox to backlog, or consciously keep deferring. No silent accumulation. This prevents zombie tasks from haunting the agenda indefinitely.

**Lineage:** GTD's "someday/maybe" review, made automatic and threshold-triggered rather than relying on weekly review discipline.

#### 6. Deterministic Plumbing, Flexible Policy

The engine (CLI, state machine, atomic writes) enforces structural rules mechanically. The LLM handles judgment calls (triage, context gathering, briefings). Neither does the other's job.

This is the **dual-ergonomics principle**: the LLM doesn't drift on state management (no hallucinated task IDs, no invalid transitions), and the CLI doesn't try to reason about what work means. Verbose CLI errors guide the LLM back on track. The result is reliable for the LLM *and* trustworthy for the human.

**Lineage:** Unix philosophy (do one thing well) applied to human-AI collaboration. The constraint makes both parties better.

#### 7. Graceful Degradation Everywhere

Any data source can fail. Any MCP can be absent. The system always produces *something* — a shorter agenda, a manual fallback, a note about what's missing. Assembly never blocks on a single source failure.

**Lineage:** Resilience engineering. For a daily planning tool, "no plan" is worse than "partial plan." The system is designed to be useful even with only a calendar and manual task entry.

### Neurodivergent Design Rationale

The methodology specifically addresses executive function challenges:

- **Decision fatigue:** Automated triage and prioritization reduce the number of decisions before work starts. The agenda is presented as "here's what to do," not "here's everything — you decide."
- **Task initiation:** Pre-plan briefings reduce the activation energy to start a task. Context is pre-gathered, approach is pre-drafted. The user's job is to validate and begin, not to figure out where to start.
- **Working memory:** The dashboard externalizes the full work state. No need to remember what's pending, what's blocked, or how much time is left.
- **Time blindness:** Budget calculations and EOD targets make time visible. Protected blocks prevent over-scheduling.
- **Completion momentum:** Small tasks (5-15m) are included early in the agenda to build momentum. The timebox philosophy means even large tasks have a "completable" increment.
- **Shame-free deferral:** Deferring is a first-class action, not a failure. The reckoning mechanism is constructive ("let's figure out what to do with this") not punitive.

### Customization Path

The methodology is modified conversationally through the workplanner. The user says what they want; the workplanner changes config and records the decision.

Common adjustments and their config surface:

| Want | Config change |
|------|--------------|
| Shorter days | `triage.filter.task_cap` (default: 10) |
| Different priority weights | `triage.source_priority` mapping |
| Longer/shorter timeboxes | `triage.estimates` mapping |
| More/fewer deferrals before reckoning | `triage.deferrals.reckoning_threshold` |
| Strict time-blocking | Add protected blocks, reduce task cap |
| No carryover priority penalty | Set `triage.source_priority.carryover` to `"high"` |

All changes are recorded in `decision-log.json` with rationale. The system can explain any deviation from defaults and suggest reversions when patterns change.

---

## Setup Interview

### Philosophy

The setup follows GTD's capture principle: identify every "inbox" the user checks for actionable items, then configure automated sweeps for each. The interview is conversational and inferential — it probes the environment first, asks principled questions, and derives config from the answers.

### Pre-Interview: Environment Probe

Before asking anything, the system inventories what's available. The probe has two tiers:

**v1 (essential):** Test connected MCP servers with minimal queries. Report results: "I can see Linear, Gmail, and Google Calendar. I don't see a Slack connection." This is the most useful probe and directly maps to inbox configuration.

**Enhanced (best-effort):** Also scan current directory context, shell environment, and Claude Code settings. Failures are silent — the interview questions fill any gaps.

| Signal | Where | Inferred default |
|--------|-------|-----------------|
| `.github/` directory exists | cwd | Pre-fill `github_username` from git config, enable GitHub runbook |
| Linear issue refs in recent commits (e.g., `PROJ-123`) | `git log -20` | Pre-fill `linear_teams` with detected project keys |
| `.mcp.json` with server entries | cwd | Map server names to integration flags (e.g., linear entry → `linear_mcp: true`) |
| `SLACK_TOKEN` or similar env var | shell env | Note Slack availability, ask for workspace details |
| MCP servers in `~/.claude/settings.json` | Claude settings | Same as MCP probe — detect available integrations |
| `CLAUDE.md` mentions tools/channels | cwd | Extract team names, channel names as suggestions (not defaults) |

The probe results power intelligent defaults in the interview questions.

### Interview Phases

**Phase 1: Identity & Environment**
- "What should I call you?"
- Timezone: auto-detect from system, confirm
- "When does your workday typically end?"
- Report MCP probe results: "I can see Linear, Gmail, and Google Calendar. I don't see a Slack connection."

**Phase 2: Inboxes (GTD capture)**
- "Where does work get assigned to you?" → project management tools
- "Where do people reach you for quick requests?" → messaging platforms
- "Do you check email for work items, or is it mostly noise?" → email triage
- "Any feeds or forums you scan for relevant activity?" → blogs, RSS, forums
- "Do you use a shared calendar?" → calendar integration

Each answer maps to available MCPs. Gaps are noted for later setup.

**Phase 3: Work Rhythms**
- "Any time blocks I should protect every day?" → protected blocks
- "Do you do a team check-in or standup? Where does that get posted?" → EOD posting target
- Profile naming: "What should we call this work context?"

**Phase 4: Terminal Environment**

If not in tmux:
> "I see you're using a bare terminal. If we get interrupted, you can use `claude --resume` to restore the session, or `/workplanner:start` to pick up where we left off. But I recommend tmux — if your terminal crashes, you can reopen your session with `tmux attach`, and with the `tmux-resurrect` plugin you can even recover after a computer restart. It also lets me show a live progress pane with your workplan, and I can configure mouse support so you can click between panes. Want me to help set that up?"

If in tmux: note it and proceed.

**Phase 5: Confirmation**
- Show the derived config in plain language
- "Anything to adjust?"
- Write config, create profile, confirm

### Reference Config

A fully annotated example lives at `docs/reference-config.md`. It shows a power-user setup: multiple messaging platforms, several project management integrations, custom triage weights, protected blocks. It's documentation — not a default template.

---

## Decision Log

### Purpose

The decision log records every methodology deviation from defaults — why it was changed, when, and by whom (user-requested or system-suggested). This enables:

- **Explainability:** "Why am I only seeing 6 tasks?" → look up the decision with context
- **Pattern reckoning:** The system notices "you've been over-budget 4 of the last 5 days" and suggests adjustments
- **Continuity:** When migrating profiles or reinstalling, past decisions provide context

### Location

`~/.workplanner/decision-log.json` — user-level, not profile-level, since methodology preferences tend to be personal. Profile-level overrides are stored in the profile's `config.json` and reference the decision log entry.

### Schema

```json
[
  {
    "id": "d-a1b2c3d4",
    "date": "2026-03-21",
    "scope": "triage",
    "key": "triage.filter.task_cap",
    "default": 10,
    "value": 6,
    "rationale": "User prefers shorter focused days. Over-budget 4/5 days at cap 10.",
    "source": "system-suggested",
    "profile": null
  }
]
```

**Fields:**
- `id`: Stable 8-char identifier (same pattern as task UIDs)
- `date`: When the decision was made
- `scope`: Category (triage, inbox, profile, engine)
- `key`: Config key path affected
- `default`: The reference methodology default
- `value`: The user's chosen value
- `rationale`: Why — required, never omitted
- `source`: `user-requested` | `system-suggested`
- `profile`: Profile name if profile-specific, null if user-level. When `wpl config set` is used (without `--user`), the active profile name is automatically populated.

### CLI Surface

```bash
wpl decision add --key <key> --value <value> --rationale "..."
wpl decision list [--scope <scope>]
wpl decision remove <id>
wpl decision explain <key>    # shows current value, default, rationale, and date
```

The `wpl config set` command always creates a corresponding decision log entry. Direct config editing is a fallback — the expected path is conversational changes mediated by the workplanner.

### Pattern Reckoning (future)

The decision log enables pattern-based suggestions ("you've been over-budget 4/5 days"), but this requires historical analysis of archived sessions — data that won't exist until the tool has been used for a while. Initially the decision log is a passive record. Pattern detection is documented in `docs/future-work.md`.

---

## CLI Surface

The `wpl` CLI expands to cover profiles and decisions while keeping the "light TaskWarrior" feel.

### Command Groups

```bash
# Task transitions (existing)
wpl done [notes]
wpl blocked [reason]
wpl defer [--until <date>]
wpl switch <task-id>
wpl move <task-id> --to <position>
wpl add "title" [--est N] [--at top|tN] [--done] [--started HH:MM] [--finished HH:MM]
wpl status
wpl undo

# Backlog (existing)
wpl backlog "title" [--est N] [--target DATE] [--deadline DATE] [--not-before DATE]
wpl backlog --from <task-id> [--target DATE]
wpl backlog --list [--tag TAG]
wpl backlog --promote <uid>
wpl backlog --drop <uid>
wpl backlog --edit <uid> [--target DATE] [--deadline DATE] [--tag TAG]

# Profiles (new)
wpl profile list
wpl profile create <name>
wpl profile switch <name>
wpl profile active
wpl profile delete <name>      # refuses active profile or last profile

# Decision log (new)
wpl decision add --key <key> --value <value> --rationale "..."
wpl decision list [--scope <scope>]
wpl decision remove <id>
wpl decision explain <key>

# Config (new)
wpl config get <key>           # reads from active profile config; --user for user.json
wpl config set <key> <value> --rationale "..."   # writes to active profile; --user for user.json
wpl config diff                # compare current values vs methodology defaults with rationale
```

**Config scope rules:** `wpl config get/set` operates on the active profile's `config.json` by default. Use `--user` to read/write `user.json` instead. Profile config values override user.json values. `wpl config diff` compares current values against methodology defaults and shows rationale from the decision log.

### Implementation

Single Python file (`bin/transition.py`, potentially renamed to `bin/engine.py`). Python 3.9+ stdlib only. Atomic writes for all mutations. Verbose error messages that guide the LLM.

The five hardcoded path constants (`SESSION`, `CONFIG`, `BACKLOG`, `UNDO_LOG`, `ARCHIVE_DIR`) are replaced by a `resolve_root()` function that follows the `active` symlink to determine the profile directory. All path derivation flows from this single resolution point.

The `wpl` wrapper lives at `~/.workplanner/bin/wpl`. Setup adds `~/.workplanner/bin` to PATH.

---

## Private Context Scrub

All Automattic-specific, username-specific, and workflow-specific content is removed or genericized.

### Skills

| Current | Change |
|---------|--------|
| `@lioneld` in Slack queries | `config.slack_handle` |
| `lioneldaniel` in GitHub queries | `config.github_username` |
| `@ceres-porter`, `@ceres` team handles | `config.inbox_slack_team_handles` (from profile config) |
| `updateomattic.wordpress.com` special-casing | Removed; announcement filtering is config-driven |
| P2-specific blog sweeping | Generalized to "feed/blog activity" with P2 as one source type |
| "Ceres weekly focus" label | Example values in docs only, config-driven in code |
| Hardcoded Linear team keys (HAPAI, WOOPUBR) | `config.linear_teams` from profile config |

### Docs

| Current | Change |
|---------|--------|
| Example agendas with "Odie classifier", "CIAB docs" | Generic task names ("API integration", "docs update") |
| Linear refs like HAPAI-2660 | Placeholder refs (PROJ-123) |
| Slack channels like #ceres | Generic channels (#team-general) |
| "context-a8c" MCP references | "organizational context MCP" or "workplace data provider" |
| "mgs provider" references | "content/blog provider" |

### Inbox Runbooks

- All runbooks parameterized by profile config — no hardcoded values
- Source-specific MCP tool names referenced by config binding, not literal MCP server name
- GitHub username, skip-orgs from config

**Note:** The scrub tables above list categories, not exhaustive file locations. Implementation requires a comprehensive grep pass for private references (`lioneld`, `lioneldaniel`, `ceres`, `HAPAI`, `WOOPUBR`, `WOODOCS`, `context-a8c`, `mgs`, `updateomattic`, `ceresp2`, `aihappy`, `melek/`). Known locations include: `skills/start/SKILL.md`, `docs/inbox-runbooks.md`, `docs/triage-framework.md`, `docs/morning-assembly.md`, `skills/horizon/SKILL.md`, `skills/pre-plan/SKILL.md`, `docs/eod-consolidation.md`.

### First-Run Setup

- No default values that assume any particular workplace
- Setup interview discovers what's available (see Setup Interview section)

### Engine

No changes needed — `transition.py`, `render_dashboard.py`, `dashboard_tui.py` are already context-free.

---

## Path Migration

`~/work-planning/` → `~/.workplanner/`

- `session-hook.sh` updated to check `~/.workplanner/profiles/active/session/current-session.json`
- Backward compatibility: if `~/work-planning/` exists and `~/.workplanner/` doesn't, offer migration
- `wpl` wrapper location: `~/.workplanner/bin/wpl`
- PATH setup during install: add `~/.workplanner/bin` to shell profile

---

## Scope

### Included in this refactor
- Methodology document (`docs/methodology.md`)
- Private context scrub (comprehensive grep pass + replacement)
- Path migration (`~/work-planning/` → `~/.workplanner/`)
- Profile directory structure (single profile on first run)
- Engine profile resolution (`resolve_root()` replacing hardcoded paths)
- `wpl profile list/create/switch/active/delete` commands
- `wpl decision add/list/remove/explain` commands
- `wpl config get/set/diff` (with mandatory decision logging)
- `user.json` schema and inheritance
- Decision log CRUD utility
- Automatic profile selection by day-of-week (from `user.json` workday schedule)
- Setup interview redesign (conversational, GTD-principled, MCP + environment probing)
- tmux recommendation during setup
- Dashboard profile name in header
- Reference config doc (`docs/reference-config.md`)
- Updated CLAUDE.md, SPECIFICATION.md, README

### Not included (see `docs/future-work.md`)
- Cross-profile task references
- Pattern reckoning from archived sessions
- Pre-plan auto-integration into `/start`

## What We Are NOT Doing

- **No engine rewrite.** The engine gains commands and profile resolution, but keeps its single-file architecture.
- **No new MCP servers.** The engine stays CLI-based.
- **No swappable methodology directory.** Users customize via config and skill edits.
- **No multi-user support.** Single-user tool.

---

## Implementation Order

Steps are ordered to minimize conflicts between concurrent changes. Path migration before scrub ensures the scrub writes the correct paths.

1. **Path migration + profile structure** — New directory layout, `resolve_root()` in engine, updated paths in all scripts and skills (including `skills/start/SKILL.md` which has ~15 `~/work-planning/` references). This is the foundation — everything else builds on the new paths.
2. **Engine expansion** — Profile subcommands (list/create/switch/active/delete), decision log CRUD, `config get/set/diff`, `user.json` schema and inheritance, auto-profile selection by workday schedule.
3. **Private context scrub** — Comprehensive grep pass for: `lioneld`, `lioneldaniel`, `ceres`, `HAPAI`, `WOOPUBR`, `WOODOCS`, `context-a8c`, `mgs`, `updateomattic`, `ceresp2`, `aihappy`, `melek/`. Known locations: `skills/start/SKILL.md`, `docs/inbox-runbooks.md`, `docs/triage-framework.md`, `docs/morning-assembly.md`, `docs/state-schema.md`, `docs/eod-consolidation.md`, `skills/horizon/SKILL.md`, `skills/pre-plan/SKILL.md`. Replace with config-driven equivalents.
4. **Methodology document** — `docs/methodology.md`. The seven principles, neurodivergent rationale, lineage, customization guide.
5. **Setup interview redesign** — Rewrite `/start` first-run flow. GTD-principled inbox discovery, MCP + environment probing, tmux recommendation.
6. **Dashboard updates** — Add profile indicator to the header line in `render_dashboard.py` and `dashboard_tui.py`: `WORKPLAN  Mon 03 Mar -- W10 [work]`. Update all hardcoded paths.
7. **Reference config doc** — `docs/reference-config.md`. Annotated power-user example.
8. **Future work doc** — `docs/future-work.md`. Cross-profile tasks, pattern reckoning, pre-plan auto-integration.
9. **CLAUDE.md + SPECIFICATION.md + README** — Reflect new architecture, clean public-facing docs.
