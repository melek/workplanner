# Inbox Runbooks

Deterministic sweep procedures for each inbox source. Executed sequentially during the inbox sweep phase (checkpoint: `inbox_swept`). Each runbook appends items to `session.inbox_items[]`. Failures are logged but never block other runbooks.

**Procedure vs Policy:** Runbooks define *what to collect* and *how to tag it* (procedure). Priority assignment and estimate defaults are applied during the triage step using `config.triage.source_priority` and `config.triage.estimates` (policy). See `docs/triage-framework.md` and `docs/state-schema.md` for the config schema.

**Intent, not tool names.** Runbooks describe what to collect in integration-agnostic terms. They do not name specific MCP providers, tool methods, scopes, or API shapes — those vary across users and change over time. Let the assistant map each step's intent onto whichever integration tools the user has available. Config field names (e.g. `config.inbox_slack_channels`) are user-declared shape and are preserved as-is.

---

## Time Horizon

Compute `sweep_since` before running any runbook:

- **If carryover session exists:** use `{previous_session.date}T{previous_session.eod_target}` in `config.timezone`
- **If no previous session:** use `yesterday 08:00` in `config.timezone`
- Store as `session.sweep_since` (ISO 8601) for audit trail

---

## Runbook 1: Digest

**Intent:** Obtain or generate today's digest of cross-source activity and parse its action-item sections.
**Config:** `config.digest_dir`, `config.integrations.digest_skill`
**Source tag:** `digest`

1. Check for today's digest at `{config.digest_dir}/{date}.md`
2. If missing → invoke digest skill to generate it. If skill fails → log warning, continue to Runbook 2
3. If digest exists → parse sections:
   - **Action Items → High Priority**: each becomes an inbox item with `source: "digest"`, `source_tier: "high"`
   - **Action Items → Carryover**: each becomes an inbox item with `source: "digest-carryover"`, `source_tier: "medium"`
   - **Action Items → Consideration**: each becomes an inbox item with `source: "digest"`, `source_tier: "low"`
   - **Top Headlines**: stored as `session.headlines[]` for agenda header (not tasks)
   Priority is assigned during triage from `config.triage.source_priority`.
4. Extract Linear refs (e.g., `PROJ-123`) from items → set as `dedupe_key`

---

## Runbook 2: Project-Management Issues

**Intent:** Surface issues assigned to the user in active states across the teams/projects they participate in.
**Config:** `config.linear_user_id`, `config.linear_teams` (rename to user's PM integration shape if different)
**Source tag:** `linear`

1. List assigned issues in active states (equivalent of `In Progress`, `Todo`, `In Review`) across each team in `config.linear_teams`
2. For each issue, capture:
   - Title, identifier, URL, team, state, priority (1-4), due date
   - Store `linear_priority` as raw value (1-4). Priority tier assigned during triage from `config.triage.source_priority` keys `linear-p1` through `linear-p4`.
3. Flag: `overdue: true` if `dueDate < session.date`, `due_today: true` if `dueDate == session.date`
4. Skip issues with state `Backlog` or `Triage` unless they are P1 or P2
5. Set `dedupe_key` to the issue identifier (e.g., `PROJ-123`)

---

## Runbook 3: Cross-Source Activity Sweep

**Intent:** When an aggregated activity feed is available (one call that returns normalized activity across PM, messaging, code-review, and internal-blog sources), prefer it over running legacy runbooks 3-6 separately.
**Config:** `config.integrations.team_activity` (object with `member` field)
**Source tags:** `linear-inbox`, `slack-ping`, `slack-channel`, `p2`, `github` (mapped from activity origin)
**Replaces:** Legacy runbooks 3-6 (PM inbox, messaging, internal blogs, code review) when `config.integrations.team_activity` is configured.

### Procedure

1. Locate the aggregated-activity provider the user has declared.
2. Compute date range:
   - `start_date`: date portion of `sweep_since` (YYYY-MM-DD)
   - `end_date`: tomorrow's date (end_date is **exclusive** in this API)
3. Fetch activity for `config.integrations.team_activity.member` across the origins the user's integration supports (typically `github`, `linear`, `p2`, `slack`), limited to the computed date range.
4. Map each activity item to an inbox item:

   | Activity origin | Activity type | Source tag | Notes |
   |----------------|---------------|-----------|-------|
   | `linear` | `issue-update`, `project-update` | `linear-inbox` | Set `dedupe_key` from Linear ref in href if present |
   | `slack` | `msg` | `slack-channel` | Items where user is mentioned → `slack-ping` instead |
   | `p2` | `post` | `p2` | Skip posts authored by user (outgoing, already aware) |
   | `p2` | `comment` | `p2` | |
   | `p2` | `mention` | `slack-ping` | Someone mentioned the user — treat as a ping |
   | `github` | any | `github` | Set `dedupe_key` to href |

5. For each mapped item, capture: `title` (from `action` text), `url` (from `href`), `source`, `context` (truncated action text)
6. Set `dedupe_key` to href URL (normalized)

### Coordination channel supplement

The aggregated activity feed typically returns the user's own activity, not incoming channel context. After the activity sweep, still read the coordination channel directly:

- List recent messages in `config.coordination_channel` (via `config.slack_channel_ids`) since `sweep_since`
- Tag as `source: "slack-channel"`, `sweep_layer: "channel"`
- This preserves visibility into team discussions the user hasn't participated in

### Fallback

If `config.integrations.team_activity` is not configured, fall back to legacy runbooks 3-6 (defined in appendix below).

---

## Legacy Runbooks 3-6 (fallback)

Used when `config.integrations.team_activity` is not configured. These are the original per-source runbooks.

<details>
<summary>Runbook 3 (legacy): PM Inbox / Notifications</summary>

**Intent:** Surface items the user is *mentioned in* or *notified about* in the project-management integration, even when not assigned.
**Source tag:** `linear-inbox`

1. Fetch the user's PM notifications/inbox (limit ~50)
2. Filter to items since `sweep_since`
3. Capture: issue assignments, comment mentions, status changes
4. Set `dedupe_key` to the issue identifier if present

</details>

<details>
<summary>Runbook 4 (legacy): Messaging</summary>

**Intent:** Surface incoming messaging activity the user needs to respond to, across four conceptual layers.
**Config:** `config.slack_channel_ids`, `config.inbox_slack_channels`, `config.inbox_slack_team_handles`, `config.inbox_slack_announcement_authors`, `config.user.slack`

Four layers — use whichever methods the user's messaging integration supports. Prefer user-scoped scans (the user's own recent activity and direct pings) over broad searches when the integration's permissions allow both:

- **Layer 1 (ping):** Find messages directed at `config.user.slack` since `sweep_since`
- **Layer 2 (team-ping):** Find mentions of each handle in `config.inbox_slack_team_handles`
- **Layer 3 (channel):** List recent messages in each channel in `config.inbox_slack_channels` since `sweep_since`
- **Layer 4 (announcement):** Find recent posts from each author in `config.inbox_slack_announcement_authors`

</details>

<details>
<summary>Runbook 5 (legacy): Internal Blogs / Feeds</summary>

**Intent:** Surface recent posts on internal blogs/feeds the user follows, excluding ones they authored themselves.
**Config:** `config.inbox_p2s`
**Source tag:** `p2`

1. For each feed/domain in `config.inbox_p2s`, find posts since `sweep_since`
2. Skip posts authored by the user
3. Tag FYI-only feeds with `source_tier: "fyi"`

</details>

<details>
<summary>Runbook 6 (legacy): Code Review</summary>

**Intent:** Surface code-review activity requesting the user's attention.
**Config:** `config.inbox_github_orgs`
**Source tag:** `github`

1. Find pull requests where the user is a requested reviewer
2. Find pull requests that mention the user
3. Set `dedupe_key` to the PR URL

</details>

---

## Runbook 7: Email

**Intent:** Surface recent unread non-promotional email that may contain actionable items.
**Config:** `config.inbox_gmail_enabled` (default: `true`)
**Source tag:** `gmail`

1. If `config.inbox_gmail_enabled` is false → skip
2. Scan for unread messages in the last day, excluding promotional/social/updates categories (use whatever filter syntax the user's email integration supports)
3. If 0 results → done (expected most days)
4. If results → capture subject, sender, snippet as inbox items with `source: "gmail"`
5. If sender domain matches any entry in `config.inbox_gmail_priority_domains`, set `source: "gmail-priority"`. Priority assigned during triage.

---

## Runbook 8: Calendar

**Intent:** List today's calendar events so protected blocks can be inserted into the agenda.
**Config:** `config.inbox_calendar_enabled` (default: `true`)
**Source tag:** `calendar`

1. If `config.inbox_calendar_enabled` is false → skip
2. List events for today (use session date with `config.timezone`)
3. Store events in `session.calendar_events[]` — each with `title`, `start`, `end`, `url`
4. These are inserted as protected blocks during agenda build (Step 3), alongside `config.protected_blocks`
5. This replaces the manual "Any meetings today?" prompt for calendar-synced meetings
6. After calendar load, ask only: "Anything not on your calendar?" as a quick fallback

---

## Inbox Item Schema

Each item appended to `session.inbox_items[]`:

```json
{
  "title": "string — short description",
  "source": "digest | digest-carryover | linear | linear-inbox | slack-ping | slack-team | slack-channel | slack-announcement | p2 | github | gmail | calendar",
  "priority": "critical | high | medium | low | fyi",
  "ref": "PROJ-123 | null — Linear issue identifier",
  "url": "string | null — source URL",
  "sweep_layer": "string | null — for Slack: direct-ping, team-ping, channel, announcement",
  "context": "string — brief excerpt or reason this was captured",
  "due_date": "ISO date string | null",
  "overdue": "boolean — true if due_date < session.date",
  "linear_priority": "1-4 | null — raw Linear priority value",
  "dedupe_key": "string — ref or normalized URL for matching"
}
```

---

## Failure Handling

Each runbook is wrapped in a try/catch pattern:

1. Log: `"Sweeping {source}..."`
2. Execute runbook
3. On success: log `"{source}: {N} items captured"`
4. On failure: log `"{source}: failed — {reason}. Continuing."` and proceed to next runbook

The inbox sweep checkpoint (`inbox_swept`) is set after ALL runbooks have been attempted, regardless of individual failures.
