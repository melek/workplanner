# Changelog

## Unreleased

### Added

- **Path-based profile resolution** (issue #10) — Profiles declare which filesystem paths they serve via a `workspaces: [...]` field in `config.json`. Each `wpl` invocation picks a profile by longest-prefix match of cwd against those workspaces, eliminating the race where concurrent sessions in different profiles flipped the `active` symlink out from under each other. Escape hatches: `--profile NAME` CLI flag and `$WPL_PROFILE` env var. Single-profile setups without workspaces keep working via fallback. See `docs/profiles.md`.
- **New profile subcommands:** `associate`, `disassociate`, `whoami`, `validate`, `migrate`.
- **Verification script** `bin/test_profile_resolution.py` covering scenarios T1–T10 from the issue brief.

### Changed

- `wpl profile switch` now prints a deprecation note. It still updates the `active` symlink for backward compatibility, but profile resolution no longer consults the symlink.
- `wpl profile list` shows each profile's workspaces and marks the one matching cwd.

## 1.0.0-beta.2

Profile-based architecture and public documentation rewrite.

### Added

- **Profile system** — Multi-context support via `~/.workplanner/profiles/`. Each profile has independent config, session state, and backlog. Switch with `wpl profile create/switch/list/delete`.
- **Decision log** — Tracks methodology deviations with timestamps and rationale. Query with `wpl decision list` and `wpl config diff`.
- **GTD-principled setup interview** — First-run creates `user.json` and initial profile through guided questions.
- **Engine/methodology separation** — Engine (`bin/`) is methodology-agnostic; productivity philosophy lives in `docs/methodology.md` with overridable defaults.
- **Methodology document** — Seven principles codified in `docs/methodology.md`.
- **Reference config** — Annotated defaults in `docs/reference-config.md`.

### Changed

- **Data root moved** from `~/work-planning/` to `~/.workplanner/` with profile subdirectories.
- **State schema** updated for profile-scoped paths (`profiles/<name>/session/current-session.json`).
- **All documentation rewritten** — CLAUDE.md, SPECIFICATION.md, README.md updated for new architecture.
- **Inbox runbooks** are now config-driven — no hardcoded MCP server names or usernames.

### Removed

- All organization-specific references (hardcoded MCP names, email domain filtering, org defaults).
- ISSUES.md (tracked on [GitHub Issues](https://github.com/melek/workplanner/issues)).

## 1.0.0-beta.1

Initial public release. Generalized from internal predecessor.

### What's included

- **Morning assembly** (`/start`) — inbox sweep across configurable MCP sources, triage, agenda build
- **EOD consolidation** (`/eod`) — task finalization, team check-in, session close
- **Task transitions** (`wpl` CLI) — done, blocked, defer, add, switch, move, backlog, undo
- **Pre-planning** (`/pre-plan`) — parallel briefing generation with workplan revision signals
- **Task dispatch** (`/dispatch`) — hand off tasks to new Claude Code sessions in tmux
- **Task pickup** (`/pickup`) — resume tasks with pre-planned context
- **Backlog management** (`/horizon`) — future-scoped work with target dates and deadlines
- **Session persistence** (`/freeze`) — save/restore tmux sessions across reboots
- **Dashboard** — real-time tmux pane with task progress, budget, EOD countdown

### Changes from predecessor

- Removed org-specific references (hardcoded MCP names, email domain filtering, org defaults)
- Context MCP integrations are now optional and configurable
- Gmail priority domains configurable via config
- GitHub org scanning defaults to empty (configure your orgs)
- First-run setup no longer assumes any specific MCP configuration
