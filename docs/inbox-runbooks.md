# Inbox Runbooks

Deterministic sweep procedures for each inbox source. Executed sequentially during the inbox sweep phase (checkpoint: `inbox_swept`). Each runbook appends items to `session.inbox_items[]`. Failures are logged but never block other runbooks.

**Procedure vs Policy:** Runbooks define *what to collect* and *how to tag it* (procedure). Priority assignment and estimate defaults are applied during the triage step using `config.triage.source_priority` and `config.triage.estimates` (policy). See `docs/triage-framework.md` and `docs/state-schema.md` for the config schema.

---

## Time Horizon

Compute `sweep_since` before running any runbook:

- **If carryover session exists:** use `{previous_session.date}T{previous_session.eod_target}` in `config.timezone`
- **If no previous session:** use `yesterday 08:00` in `config.timezone`
- Store as `session.sweep_since` (ISO 8601) for audit trail

---

## Runbook 1: Digest

**Tool:** Skill tool → `config.integrations.digest_skill` (or lightweight fallback)
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

## Runbook 2: Linear Issues

**Tool:** Linear MCP → `list_issues`
**Config:** `config.linear_user_id`, `config.linear_teams`
**Source tag:** `linear`

1. Query assigned issues in active states (`In Progress`, `Todo`, `In Review`) across each team in `config.linear_teams`
2. For each issue, capture:
   - Title, identifier, URL, team, state, priority (1-4), due date
   - Store `linear_priority` as raw value (1-4). Priority tier assigned during triage from `config.triage.source_priority` keys `linear-p1` through `linear-p4`.
3. Flag: `overdue: true` if `dueDate < session.date`, `due_today: true` if `dueDate == session.date`
4. Skip issues with state `Backlog` or `Triage` unless they are P1 or P2
5. Set `dedupe_key` to the issue identifier (e.g., `PROJ-123`)

---

## Runbook 3: Linear Inbox/Notifications

**Tool:** organizational context MCP → linear provider → `inbox`
**Config:** none (uses authenticated user)
**Source tag:** `linear-inbox`

1. Load `linear` provider via context MCP
2. Execute `inbox` tool (limit: 50)
3. Filter to items since `sweep_since`
4. Capture: issue assignments, comment mentions, status changes
5. These surface items the user is *mentioned in* but not necessarily assigned to
6. Set `dedupe_key` to the Linear issue identifier if present

---

## Runbook 4: Slack

**Tool:** organizational context MCP → slack provider → `search`, `messages`
**Config:** `config.slack_channel_ids`, `config.inbox_slack_channels`, `config.inbox_slack_team_handles`, `config.inbox_slack_announcement_authors`, `config.user.slack`

Four layers, each producing inbox items tagged with its layer:

### Layer 1: Direct pings (highest signal)

**Source tag:** `slack-ping`

- `search` query: `@{config.user.slack}` filtered to since `sweep_since` (use `days: 1`)
- Each result becomes an inbox item with `source: "slack-ping"`, `sweep_layer: "direct-ping"`

### Layer 2: Team/group pings

**Source tag:** `slack-team`

- For each handle in `config.inbox_slack_team_handles` (e.g., `@team-handle`, `@team-name`):
  - `search` query: `{handle}` with `days: 1`
- Each result: `source: "slack-team"`, `sweep_layer: "team-ping"`

### Layer 3: Key channel activity

**Source tag:** `slack-channel`

- For each channel in `config.inbox_slack_channels`:
  - `messages` with `oldest` = unix timestamp of `sweep_since`
- Capture threads with recent replies that may need attention
- Each result: `source: "slack-channel"`, `sweep_layer: "channel"`

### Layer 4: Announcement action items

**Source tag:** `slack-announcement`

- For each author in `config.inbox_slack_announcement_authors` (e.g., `matt`):
  - `search` query: `from:@{author} in:#announcements` with `days: 1`
- Each result: `source: "slack-announcement"`, `sweep_layer: "announcement"`

---

## Runbook 5: Blog/Feed Activity

**Tool:** organizational context MCP → blog/content provider → `search`
**Config:** `config.inbox_p2s`
**Source tag:** `p2`

1. Load blog/content provider via context MCP
2. For each blog domain in `config.inbox_p2s`:
   - Provider `search` with `sites` param, `date_from: sweep_since` (date portion), `sort: date_desc`
3. Capture: new posts since `sweep_since` — title, author, URL, excerpt
4. Skip posts authored by the user (already aware)
5. Tag items from announcement-only blogs (per `config.inbox_fyi_domains`) with `source_tier: "fyi"` unless they mention the user or team
6. All other blog posts: `source: "p2"`. Priority assigned during triage.

**Note:** Provider `sites` param may need numeric site IDs or domain strings — verify shape at runtime and adapt.

---

## Runbook 6: GitHub

**Tool:** organizational context MCP → github provider → `search-pull-requests`
**Config:** `config.inbox_github_orgs` (default: `[]`)
**Source tag:** `github`

1. Load `github` provider via context MCP
2. Search PRs where user is requested reviewer: `review-requested:{config.user.github} state:open` scoped to configured orgs
3. Search PRs mentioning user: `mentions:{config.user.github} state:open` scoped to configured orgs
4. Capture: PR title, repo, URL, author, state
5. Tag as `source: "github"`. Priority assigned during triage.
6. Skip repos in `config.inbox_github_skip_orgs` entirely
7. Set `dedupe_key` to PR URL

---

## Runbook 7: Gmail

**Tool:** Gmail MCP → `gmail_search_messages`
**Config:** `config.inbox_gmail_enabled` (default: `true`)
**Source tag:** `gmail`

1. If `config.inbox_gmail_enabled` is false → skip
2. Search: `is:unread newer_than:1d -category:promotions -category:social -category:updates`
3. If 0 results → done (expected most days)
4. If results → capture subject, sender, snippet as inbox items with `source: "gmail"`
5. If sender domain matches any entry in `config.inbox_gmail_priority_domains`, set `source: "gmail-priority"`. Priority assigned during triage.

---

## Runbook 8: Google Calendar

**Tool:** Google Calendar MCP → `gcal_list_events`
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
