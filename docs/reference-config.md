# Reference Configuration

This is an annotated example of a fully configured workplanner profile. It shows what a power-user setup looks like — multiple integrations, custom triage weights, protected blocks.

This is **not** a template to copy. Your config is created during the setup interview (`/workplanner:start`) and modified conversationally. This file exists to document what's possible.

## user.json

Located at `~/.workplanner/user.json`. Cross-profile identity and preferences.

```json
{
  "schema_version": 1,
  "display_name": "Alex",
  "timezone": "America/New_York",
  "eod_target": "17:00",
  "default_profile": "work",
  "non_workday_profile": "home",
  "workday_schedule": {
    "monday": true,
    "tuesday": true,
    "wednesday": true,
    "thursday": true,
    "friday": true,
    "saturday": false,
    "sunday": false
  },
  "tmux_recommended": true
}
```

**Fields:**
- `display_name`: Used in sub-issue matching and greetings
- `timezone`: IANA timezone — anchors all date comparisons to your local calendar
- `eod_target`: Default end-of-day, overridable per profile
- `default_profile`: Auto-selected on workdays
- `non_workday_profile`: Auto-selected on weekends (null = use default)
- `workday_schedule`: Which days are workdays
- `tmux_recommended`: Whether tmux was offered during setup

## Profile config.json

Located at `~/.workplanner/profiles/<name>/config.json`. Each profile has its own config.

```json
{
  "schema_version": 2,
  "workspaces": [
    "/Users/alex/work",
    "/Users/alex/projects"
  ],
  "timezone": "America/New_York",
  "eod_target": "17:30",
  "dashboard_pane": "auto",

  "linear_user_id": "uuid-from-linear-settings",
  "linear_teams": ["ENG", "PLATFORM"],
  "github_username": "alexdev",
  "github_skip_orgs": ["personal-org"],
  "slack_handle": "alex",

  "protected_blocks": [
    {
      "label": "Lunch",
      "start": "12:00",
      "end": "13:00",
      "emoji": "lunch"
    },
    {
      "label": "Standup",
      "start": "09:30",
      "end": "09:45"
    }
  ],

  "weekly_focus": {
    "label": "Team weekly focus",
    "team": "ENG",
    "sub_issue_pattern": "weekly check-in: {user_display_name}"
  },
  "coordination_channel": "eng-coordination",
  "focus_secondary_label": "stretch goals",

  "slack_channel_ids": {
    "eng-coordination": "C01EXAMPLE1",
    "platform-team": "C02EXAMPLE2"
  },
  "inbox_slack_team_handles": ["@eng-team", "@platform-oncall"],
  "inbox_slack_announcement_authors": ["ceo"],
  "inbox_p2s": ["team-blog.example.com"],
  "inbox_github_orgs": ["mycompany"],
  "inbox_gmail_priority_domains": ["mycompany.com", "partner.org"],
  "inbox_gmail_enabled": true,
  "inbox_calendar_enabled": true,
  "inbox_fyi_domains": ["announcements.example.com"],

  "integrations": {
    "digest_skill": "context:digest",
    "focus_skill": null,
    "context_mcp": true,
    "linear_mcp": true
  },

  "triage": {
    "source_priority": {
      "carryover": "medium",
      "linear-p1": "critical",
      "linear-p2": "high",
      "linear-p3": "medium",
      "linear-p4": "low",
      "slack-ping": "high",
      "slack-team": "medium",
      "slack-channel": "low",
      "github": "medium",
      "digest-high": "high",
      "digest-medium": "medium",
      "digest-low": "low",
      "p2": "low",
      "gmail": "low",
      "gmail-priority": "medium",
      "backlog": "high",
      "manual": "medium",
      "focus": "medium"
    },
    "estimates": {
      "slack-ping": 5,
      "slack-team": 10,
      "slack-channel": 10,
      "github": 15,
      "linear-high": 30,
      "linear-low": 15,
      "digest-high": 30,
      "digest-low": 15,
      "p2": 10,
      "gmail": 5,
      "backlog": 30,
      "carryover": null,
      "manual": 30,
      "focus": 30
    },
    "ordering": ["critical", "high", "medium", "focus"],
    "filter": {
      "task_cap": 10,
      "always_include_overdue": true,
      "always_include_due_today": true
    },
    "deferrals": {
      "reckoning_threshold": 3
    },
    "pre_work": {
      "scan_from": "06:00",
      "sources": ["slack", "p2"],
      "min_minutes_for_task": 5
    }
  }
}
```

Each section of this config is documented in detail in `docs/state-schema.md`.
Path-based profile resolution (the `workspaces` field) is covered in `docs/profiles.md`.
