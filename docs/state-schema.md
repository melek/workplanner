# State Schema Reference

Internal reference for the JSON files that hold workplanner state.

---

## current-session.json

**Location:** `~/.workplanner/profiles/<name>/current-session.json`

Source of truth for all session state. If the JSON and any derived artifacts (markdown agenda, dashboard) disagree, the JSON wins.

### Top-level fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `schema_version` | `integer` | yes | Schema version for forward compatibility. Current: `1`. |
| `date` | `string` (ISO date) | yes | Session date, e.g. `"2026-03-03"`. Set using `config.timezone` (not naive UTC). Used to detect stale sessions on resume. |
| `week` | `string` | yes | ISO week label, e.g. `"W10"`. |
| `focus_issue` | `object \| null` | yes | The team's weekly focus issue from Linear. |
| `personal_sub_issue` | `object \| null` | yes | The user's personal sub-issue under the focus issue. |
| `eod_target` | `string` (HH:MM) | yes | Target end-of-day time in local timezone. |
| `started_at` | `string` (HH:MM) | yes | Time the session was started. |
| `checkpoint` | `string` | yes | Last completed assembly phase. See checkpoint values below. |
| `current_task_index` | `integer \| null` | yes | 0-based index of the active task in the `tasks` array, or `null` if no task is active. |
| `tasks` | `array` | yes | Ordered list of task objects. |
| `coordination_thread` | `object \| null` | no | Captured Slack coordination thread, if any. |
| `eod_handoff_written` | `boolean` | no | Canonical EOD-completion marker. Set to `true` by `/eod` Step 2 after the local handoff doc is written successfully; session close (Step 4) gates on this being true. Absent or `false` in a past-dated session signals that EOD did not complete and triggers the stale-session handler in `/start`. |
| `sweep_since` | `string` (ISO 8601) \| null | no | Cutoff timestamp for inbox sweep. Computed from previous session's EOD or yesterday 08:00. |
| `inbox_items` | `array` | no | Raw captured items from inbox sweep. Cleared after `agenda_built`. See inbox item schema. |
| `headlines` | `array` of `string` | no | FYI items for agenda header (from digest + low-signal sources). |
| `calendar_events` | `array` | no | Today's calendar events (inserted as protected blocks during agenda build). |

### Checkpoint values

| Value | Description |
|-------|-------------|
| `initialized` | Session created, carryover extracted from stale session. |
| `inbox_swept` | All inbox runbooks executed, raw items stored in `inbox_items[]`. |
| `focus_loaded` | Weekly focus issue and personal sub-issue loaded from Linear. |
| `agenda_built` | Items triaged, deduplicated, estimated, ordered into `tasks[]`. `inbox_items` cleared. |
| `assembly_complete` | Dashboard rendered, first task set to `in_progress`, agenda output displayed. |
| `closed` | EOD consolidation complete. |

### Calendar event object

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `title` | `string` | yes | Event title. |
| `start` | `string` (HH:MM) | yes | Event start time in local timezone. |
| `end` | `string` (HH:MM) | yes | Event end time in local timezone. |
| `url` | `string \| null` | no | Calendar event URL or meeting link. |

### Inbox item object

Transient — only present between `inbox_swept` and `agenda_built` checkpoints.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `title` | `string` | yes | Short description. |
| `source` | `string` | yes | Origin tag: `digest`, `digest-carryover`, `linear`, `linear-inbox`, `slack-ping`, `slack-team`, `slack-channel`, `slack-announcement`, `p2`, `github`, `gmail`, `calendar`. |
| `priority` | `string` | yes | `critical`, `high`, `medium`, `low`, or `fyi`. |
| `ref` | `string \| null` | no | Linear issue identifier (e.g. `"PROJ-123"`). |
| `url` | `string \| null` | no | Source URL. |
| `sweep_layer` | `string \| null` | no | For Slack items: `direct-ping`, `team-ping`, `channel`, `announcement`. |
| `context` | `string` | yes | Brief excerpt or reason this was captured. |
| `due_date` | `string` (ISO date) \| null | no | Due date if applicable. |
| `overdue` | `boolean` | no | `true` if `due_date < session.date`. |
| `linear_priority` | `integer` (1-4) \| null | no | Raw Linear priority value. |
| `dedupe_key` | `string` | yes | Linear ref or normalized URL, used for deduplication across sources. |

### Task object

Tasks do NOT have an `id` field in JSON. Display IDs (`t1`, `t2`, ...) are derived from array position by the renderer and CLI tools.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `title` | `string` | yes | Short description of the task. May be revised via `wpl rename` to reflect work-as-completed; the original is preserved in `original_title` on first rename. |
| `original_title` | `string` | no | First-renamed title. Set automatically by `wpl rename` on the first rename only — repeat renames update `title` only, leaving `original_title` as the planning-phase receipt. Surfaced by `bin/handoff.py` as `(was: <original_title>)` in the deferred-with-reasons section when it differs from the current `title`. Absent on tasks that were never renamed — migration-safe. |
| `status` | `string` | yes | Current task state: `pending`, `in_progress`, `done`, `blocked`, `deferred`. |
| `estimate_min` | `integer` | yes | Estimated duration in minutes. |
| `actual_min` | `integer \| null` | no | Actual duration in minutes. Set when task reaches `done`. |
| `source` | `string` | yes | Where the task originated: `carryover`, `linear`, `digest`, `slack`, `focus`, `manual`. |
| `ref` | `string \| null` | no | Linear issue identifier (e.g. `"PROJ-456"`). |
| `url` | `string \| null` | no | URL for the source reference. |
| `started_at` | `string (HH:MM) \| null` | no | Time the task was started. |
| `finished_at` | `string (HH:MM) \| null` | no | Time the task was completed. |
| `notes` | `string \| null` | no | Free-text notes. |
| `briefed_at` | `string (ISO 8601) \| null` | no | Timestamp at which the principal acknowledged the task's briefing. Required before any advance mutation (`done`, `blocked`, `defer`, `reckon keep|break|delegate`); the CLI refuses unbriefed advances with exit 1. Set by `wpl brief` (called by `/workplanner:pickup` after Step 6's gate, or by `/workplanner:pre-plan`'s auto-apply lane with `--rationale`). Issue #44. **Migration:** sessions predating this field are auto-briefed on first invocation after upgrade with `brief_rationale: "auto-migrated: predates briefing precondition"`; migration is recorded once via `$PROFILE_ROOT/.briefing-precondition-migrated`. |
| `brief_rationale` | `string` | no | Optional rationale recorded with the briefing. Used by `/workplanner:pre-plan`'s auto-apply lane to record evidence (e.g. `"auto-apply: Linear issue PROJ-12 closed at 2026-04-25T08:50:14"`), and by inline-briefing flows where the artifact lives in conversation rather than on disk. Surfaced in the undo log so the chain of authorizations is auditable. |
| `briefing_path` | `string` | no | Optional path to a briefing markdown file (typically `$PROFILE_ROOT/briefings/{date}/...md` produced by `/workplanner:pre-plan`). Set by `wpl brief --artifact-path`. The CLI emits a warning if the path doesn't exist on disk but does not refuse — the principal might be acknowledging from memory or from a briefing that lives outside the standard tree. |
| `parent` | `integer \| null` | no | 0-based index of parent task for sub-task nesting. |
| `deferral_count` | `integer` | no | Number of times this task has been deferred (persists through carryover). Default: 0. When this reaches `config.triage.deferrals.reckoning_threshold`, the system triggers a forced reckoning prompt instead of silently deferring. |
| `defer_reason` | `string` | no | Optional human-readable explanation of *why* the task was deferred. Set via `wpl defer --reason "..."` (or `wpl reckon <choice> --reason "..."`). Persists on the task, travels across carryover (session → backlog → session), and is surfaced in reckoning prompts, `/eod` handoff doc, and `/start` carryover mini-triage. Absent on older state files — migration-safe (any consumer uses `.get()`). |

### State mutations

**During the day** (task transitions): always use `bin/transition.py`. It handles atomic writes, re-rendering, and validation.

**During assembly** (building the session) and **during EOD** (closing the session): write JSON directly using atomic writes (tmp file → mv). These are bulk operations where the skill owns the full state lifecycle.

```bash
python3 bin/transition.py <command> [args]
# done, blocked [reason], defer, add <title> [--est N], switch <t-id|index>, status
```

---

## config.json

**Location:** `~/.workplanner/profiles/<name>/config.json`

Created during first-run setup. Persists across sessions.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `schema_version` | `integer` | yes | Current: `2`. |
| `workspaces` | `array` of `string` | no | Absolute filesystem paths this profile serves. Drives path-based profile resolution (see `docs/profiles.md`). Entries are normalized via `~` expansion + `os.path.realpath` on write. Profiles with no `workspaces` declared fall back to single-profile resolution (if they're the only profile) or require `--profile`/`$WPL_PROFILE` override. Overlaps between profiles (identical paths) are rejected at write time; proper-prefix overlaps are allowed and resolved by longest-match-wins. |
| `timezone` | `string` | yes | IANA timezone identifier, e.g. `"Europe/Paris"`. Used by `transition.py` (`local_today()`) and morning assembly to anchor all date comparisons to the user's local calendar date. |
| `eod_target` | `string` (HH:MM) | yes | Default end-of-day target time. |
| `dashboard_pane` | `string` enum | no | `"auto"` \| `"always"` \| `"never"`. Controls whether `/start` spawns a tmux dashboard pane. `"auto"` (default): spawn when `$TMUX` is set. `"never"`: never spawn. `"always"`: attempt spawn regardless of `$TMUX`. |
| `protected_blocks` | `array` | no | Time blocks that should not be scheduled over. |
| `protected_blocks[].label` | `string` | yes | Display name for the block. |
| `protected_blocks[].start` | `string` (HH:MM) | yes | Block start time. |
| `protected_blocks[].end` | `string` (HH:MM) | yes | Block end time. |
| `protected_blocks[].emoji` | `string` | no | Emoji displayed alongside the block. |
| `linear_user_id` | `string` (UUID) | yes | The user's Linear account UUID. |
| `linear_teams` | `array` of `string` | yes | Linear team identifiers, e.g. `["PROJ", "TEAM"]`. |
| `slack_channel_ids` | `object` | no | Map of channel names to Slack channel IDs. |
| `digest_dir` | `string` (path) | yes | Absolute path to the daily digest directory. |
| `user_display_name` | `string` | no | User's first name, used in sub-issue matching patterns. |
| `weekly_focus` | `object` | no | Parameters for finding the team's weekly focus issue in Linear. |
| `weekly_focus.label` | `string` | yes | Linear issue label to filter on (e.g. `"Team weekly focus"`). |
| `weekly_focus.team` | `string` | yes | Linear team key for focus issue queries (e.g. `"PROJ"`). |
| `weekly_focus.sub_issue_pattern` | `string` | yes | Title pattern for the user's personal sub-issue. Use `{user_display_name}` as a placeholder (e.g. `"weekly check-in: {user_display_name}"`). |
| `coordination_channel` | `string` | no | Name of the primary Slack coordination channel (key into `slack_channel_ids`). |
| `focus_secondary_label` | `string` | no | What the team calls secondary items from the focus issue (e.g. `"side dishes"`). |
| `integrations` | `object` | no | External skill and MCP dependencies. |
| `integrations.digest_skill` | `string \| null` | no | Skill to invoke for digest generation. Null = use lightweight fallback. |
| `integrations.focus_skill` | `string \| null` | no | Skill to invoke for focus issue loading (e.g. `"/focus"`). Null = query Linear directly. |
| `integrations.context_mcp` | `boolean` | no | Whether an organizational context MCP server is available. Default: `true`. |
| `integrations.linear_mcp` | `boolean` | no | Whether the Linear MCP server is available. Default: `true`. |
| `integrations.team_activity` | `object \| null` | no | If set, enables the cross-source activity sweep (Runbook 3) which replaces legacy runbooks 3-6. Requires the `team-activity` provider in the organizational context MCP. |
| `integrations.team_activity.member` | `string` | yes (if parent set) | The user's identifier for the activity-aggregation provider — typically the username the provider uses to scope activity (e.g. `"yourusername"`). |
| `inbox_slack_channels` | `object` | deprecated | Legacy duplicate of `slack_channel_ids`. `load_config()` merges entries into `slack_channel_ids` on read (canonical wins on conflict) and emits a one-time stderr warning. Remove this key when convenient; see [#27](https://github.com/melek/workplanner/issues/27). |
| `inbox_slack_team_handles` | `array` of `string` | no | Team handles to search for pings. E.g. `["@team-handle", "@team-name"]`. |
| `inbox_slack_announcement_authors` | `array` of `string` | no | Slack usernames whose announcements to monitor. E.g. `["matt"]`. |
| `inbox_p2s` | `array` of `string` | no | Blog/feed domains to sweep. E.g. `["team-blog.example.com", "project-blog.example.com"]`. |
| `inbox_github_orgs` | `array` of `string` | no | GitHub orgs to scan for PR reviews. Default: `[]`. |
| `inbox_gmail_priority_domains` | `array` of `string` | no | Email domains to tag as `gmail-priority` source (higher triage tier). Default: `[]`. |
| `inbox_gmail_enabled` | `boolean` | no | Enable Gmail inbox sweep. Default: `true`. |
| `inbox_calendar_enabled` | `boolean` | no | Enable Google Calendar sweep. Default: `true`. |

Version 1 and 2 configs are compatible — the new inbox fields are optional and runbooks skip gracefully when absent.

### Triage configuration (`config.triage`)

Optional. Controls priority assignment, estimate defaults, ordering, and deferral behavior. If absent, sensible defaults apply (documented in `docs/triage-framework.md`).

**Design principle:** Runbook docs define *procedure* (what to collect). This config defines *policy* (how to prioritize and order). Separating them means users can tune their priorities without editing runbook procedures.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `triage.source_priority` | `object` | (see below) | Map of source type → priority tier (`critical`, `high`, `medium`, `low`, `fyi`). |
| `triage.estimates` | `object` | (see below) | Map of source type → default estimate in minutes. `null` = reuse previous (for carryover). |
| `triage.ordering` | `array` of `string` | `["critical", "high", "medium", "focus"]` | Priority tier ordering for agenda. |
| `triage.filter.task_cap` | `integer` | `10` | Maximum tasks on the agenda. Excess goes to backlog awareness footer. |
| `triage.filter.always_include_overdue` | `boolean` | `true` | Overdue items always make the agenda regardless of cap. |
| `triage.filter.always_include_due_today` | `boolean` | `true` | Due-today items always make the agenda. |
| `triage.deferrals.reckoning_threshold` | `integer` | `3` | After this many deferrals, trigger a forced reckoning prompt (break down, delegate, drop, timebox, or keep). |
| `triage.pre_work.scan_from` | `string` (HH:MM) | `"06:00"` | Start of pre-work scan window. Activity by the user between this time and `/start` invocation is captured. |
| `triage.pre_work.sources` | `array` of `string` | `["slack", "p2"]` | Sources to scan for outgoing user activity (not incoming items). |
| `triage.pre_work.min_minutes_for_task` | `integer` | `5` | Minimum estimated pre-work time to insert a completed "Morning communication work" task. Below threshold → headline note only. |

**Default `source_priority`:**
```json
{
  "carryover": "medium", "linear-p1": "critical", "linear-p2": "high",
  "linear-p3": "medium", "linear-p4": "low", "slack-ping": "high",
  "slack-team": "medium", "slack-channel": "low", "github": "medium",
  "digest-high": "high", "digest-medium": "medium", "digest-low": "low",
  "p2": "low", "gmail": "low", "gmail-priority": "medium",
  "backlog": "high", "manual": "medium", "focus": "medium"
}
```

**Default `estimates`:**
```json
{
  "slack-ping": 5, "slack-team": 10, "slack-channel": 10, "github": 15,
  "linear-high": 30, "linear-low": 15, "digest-high": 30, "digest-low": 15,
  "p2": 10, "gmail": 5, "backlog": 30, "carryover": null,
  "manual": 30, "focus": 30
}
```

---

## Handoff docs

**Location:** `~/.workplanner/profiles/<name>/handoffs/{YYYY-MM-DD}.md`

Workspace-local markdown files written by `/eod` and read by the next day's `/start`. One file per date. The path uses the *concrete* profile name (not the `active` alias) so concurrent sessions don't race the symlink.

### Structure

Merge-by-section format. Top-level `## Heading` sections, each further split into `### <session-id>` sub-sections so concurrent sessions contribute without clobbering each other.

Recognised sections (writers only touch these; anything else passes through verbatim):

| Section | Purpose |
|---------|---------|
| `## Session trajectory` | High-level narrative of what got done, deferred, blocked. |
| `## Deferred with reasons` | Every task ending the day `deferred` or `blocked`, with `defer_reason` inline. Format: `- **<title>** (<uid>): <reason>`. |
| `## Open questions` | Things the LLM or user noticed that need decision/research/escalation. |
| `## Context for tomorrow` | Short concrete pointers for the next morning. |

### Session identifier

`### <session-id>` sub-headings disambiguate concurrent writers. The identifier is picked by `bin/handoff.py` in priority order:

1. `$CLAUDE_SESSION_ID` if set
2. `$TMUX_PANE` if running inside tmux (e.g., `pane-%3`)
3. Hash of the python process start time (fallback — stable within a process, not across re-invocations)

### Read/write API

The file is read and written through `bin/handoff.py`. Skills never edit it directly.

```bash
python3 bin/handoff.py write --trajectory "..." --deferred-json '[{...}]' --open-questions "..." --context "..."
python3 bin/handoff.py read [--date YYYY-MM-DD]    # prints JSON with {path, date, exists, sections, deferred, raw}
python3 bin/handoff.py path [--date YYYY-MM-DD]    # prints the resolved path
```

`write` is idempotent within a session-id: re-running overwrites that session's sub-sections only. Other sessions' sub-sections and unrecognised `## ...` sections (e.g., human free-text additions) are preserved verbatim.

### Stale-session recovery

When `/start` finds a stale session (`current-session.json` date ≠ today) whose `eod_handoff_written` is absent or `false`, it backfills a handoff for the stale date at the **same path** (`~/.workplanner/profiles/<name>/handoffs/{stale_date}.md`) using a distinct session-id of the form `stale-recovery-{stale_date}`. The next morning's `/start` Step 0.25 reads this file the same way it reads a normal-path handoff — aggregation across sub-sections is session-id-agnostic. See `skills/start/SKILL.md` → "Stale Session Handler".

---

## Briefings

**Location:** `~/.workplanner/profiles/<name>/briefings/{date}/`

Pre-generated task briefings, organized by date. Created by `/pre-plan`, consumed by `/pickup`.

### Directory structure

```
~/.workplanner/profiles/<name>/briefings/
  2026-03-16/
    README.md                           # Index with status of each briefing
    t01-a1b2c3d4-fix-email-bot.md      # Individual task briefing
    t02-e8a45522-ciab-docs.md
```

### Filename convention

`t{NN}-{uid}-{slug}.md` where:
- `{NN}` — zero-padded display index at time of generation
- `{uid}` — task's 8-char stable UID (primary match key)
- `{slug}` — kebab-case title excerpt (max 30 chars, truncated at word boundary)

### Briefing frontmatter

```yaml
---
task: t2
uid: e8a45522
title: API docs update
ref: PROJ-789
archetype: draft
generated: 2026-03-16T09:30:00+01:00
---
```

### Staleness rules

- Briefings are scoped to a date directory matching `session.date`
- A briefing in a directory for a different date than the current session is stale by definition
- Within the same day, a briefing whose `generated` timestamp predates a Linear issue's `updatedAt` gets a freshness warning
- `/pickup` matches briefings by task UID in the filename

---

## Backlog

**Location:** `~/.workplanner/profiles/<name>/backlog.json`

Persistent holding area for work items that don't belong on today's agenda. Items surface automatically when their target date arrives or deadline approaches. Managed via `transition.py backlog` and the `/horizon` skill.

### Schema

```json
{
  "schema_version": 1,
  "last_reviewed": "2026-03-16",
  "items": [...]
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `schema_version` | `integer` | yes | Current: `1`. |
| `last_reviewed` | `string` (ISO date) \| null | no | Date of last `/horizon` review. Used for staleness nudges. |
| `items` | `array` | yes | Backlog items (see below). |

### Backlog item object

Uses the same base fields as a session task, plus temporal targeting fields.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `uid` | `string` (8-char) | yes | Stable UID, same format as session tasks. |
| `title` | `string` | yes | Short description. |
| `estimate_min` | `integer` | no | Estimated duration in minutes. Default: 30. |
| `source` | `string` | no | Origin: `manual`, `backlog`, `carryover`, etc. |
| `status` | `string` | yes | Always `"backlog"` while in backlog.json. |
| `ref` | `string` \| null | no | Linear issue identifier. |
| `url` | `string` \| null | no | Source URL. |
| `notes` | `string` \| null | no | Free-text notes. |
| `created_at` | `string` (ISO date) | yes | Date the item was added. Drives staleness detection. |
| `target_date` | `string` (ISO date) \| null | no | "Surface on this date." Auto-promotes to inbox during morning assembly. |
| `not_before` | `string` (ISO date) \| null | no | "Don't surface until this date." Suppresses auto-promotion. |
| `deadline` | `string` (ISO date) \| null | no | Hard deadline. Drives urgency warnings and auto-promotion when ≤2 days away. |
| `tags` | `array` of `string` | no | Lightweight categorization for filtering in `/horizon`. |
| `defer_reason` | `string` | no | Preserved when a deferred session task is sent to the backlog; carried back to the promoted session task on `backlog --promote`. |
| `deferral_count` | `integer` | no | Preserved alongside `defer_reason` so reckoning signal survives the backlog round-trip. |

### Surfacing rules

| Tier | Condition | Action |
|------|-----------|--------|
| Auto-promote | `target_date <= today` (and `not_before` satisfied) | Added to `inbox_items[]` with `source: "backlog"`, `priority: "high"`. Removed from backlog. |
| Urgency auto-promote | `deadline` ≤2 days away, no `target_date` | Added with `priority: "critical"`. Removed from backlog. |
| Awareness | `deadline` 3-7 days away | Shown in agenda "Backlog awareness" footer. |
| Passive | No dates | Only visible via `backlog --list` or `/horizon`. |

### CLI reference

```bash
transition.py backlog "title" [--est N] [--target DATE] [--deadline DATE] [--not-before DATE] [--ref REF] [--tag TAG]
transition.py backlog --from t5 [--target DATE]     # move session task to backlog
transition.py backlog --from-current                 # move current task
transition.py backlog --list [--tag TAG]             # list items
transition.py backlog --promote <uid>                # move to today's session
transition.py backlog --drop <uid>                   # remove
transition.py backlog --edit <uid> [--target DATE] [--deadline DATE] [--tag TAG]
transition.py defer --until <date>                   # defer to backlog with target_date
```

Date arguments accept: `YYYY-MM-DD`, `tomorrow`, weekday names (`monday`–`sunday`), `next-week`.

---

## user.json

**Location:** `~/.workplanner/user.json`

Cross-profile identity and preferences. Created during first-run setup.

### Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `schema_version` | integer | yes | Current: 1 |
| `display_name` | string | yes | User's preferred first name |
| `timezone` | string | yes | IANA timezone (e.g., `"America/New_York"`) |
| `eod_target` | string (HH:MM) | yes | Default EOD, overridable per profile |
| `default_profile` | string | yes | Profile name used when the `active` symlink is missing or broken, and selected on workdays by auto-selection. Manual switches via `wpl profile switch` persist until the next day's `/start`. |
| `non_workday_profile` | string \| null | no | Profile to auto-select on non-workdays. If null, `default_profile` is used. |
| `workday_schedule` | object | no | Day-of-week to boolean. Used by `/start` for automatic profile selection when multiple profiles exist. |
| `tmux_recommended` | boolean | no | Whether tmux setup was offered/accepted during setup |

Profile configs inherit `timezone` and `eod_target` from user.json unless they define their own.

---

## decision-log.json

**Location:** `~/.workplanner/decision-log.json`

Records every methodology deviation from defaults. User-level (not profile-level).

### Entry Schema

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `id` | string | yes | Stable identifier, format `d-{8char}` |
| `date` | string (ISO date) | yes | When the decision was made |
| `scope` | string | yes | Category: `triage`, `inbox`, `profile`, `engine`, `general` |
| `key` | string | yes | Config key path affected (e.g., `triage.filter.task_cap`) |
| `default` | any | no | The methodology default value |
| `value` | any | yes | The user's chosen value |
| `rationale` | string | yes | Why this value was chosen. Required — never omitted. |
| `source` | string | yes | `user-requested` or `system-suggested` |
| `profile` | string \| null | no | Profile name if profile-specific, null if user-level |

### CLI

```bash
wpl decision add --key <key> --value <value> --rationale "..."
wpl decision list [--scope <scope>]
wpl decision remove <id>
wpl decision explain <key>
```

`wpl config set` always creates a corresponding decision log entry.

---

## Deprecated

### `config.handoffs.*` (removed in effect as of issue #13)

The following keys are no longer read or honoured by any skill or CLI:

- `config.handoffs.dir`
- `config.handoffs.filename_pattern`
- `config.handoffs.carryover_from_handoff`

They previously configured a user-owned ad-hoc handoff file used only by `/start`'s stale-session handler. Handoffs now live exclusively at `~/.workplanner/profiles/<name>/handoffs/YYYY-MM-DD.md` and are managed by `bin/handoff.py` (see **Handoff docs** above). Stale-session recovery writes to the same path, so there is no longer a second mechanism to configure.

If any of these keys is present in a profile's `config.json`, `load_config()` emits a one-line deprecation warning on stderr (at most once per process). The keys themselves are left untouched — remove them from `config.json` when convenient to silence the warning.
