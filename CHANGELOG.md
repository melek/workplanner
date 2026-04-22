# Changelog

## Unreleased

### Added

- **Path-based profile resolution** (issue #10) — Profiles declare which filesystem paths they serve via a `workspaces: [...]` field in `config.json`. Each `wpl` invocation picks a profile by longest-prefix match of cwd against those workspaces, eliminating the race where concurrent sessions in different profiles flipped the `active` symlink out from under each other. Escape hatches: `--profile NAME` CLI flag and `$WPL_PROFILE` env var. Single-profile setups without workspaces keep working via fallback. See `docs/profiles.md`.
- **New profile subcommands:** `associate`, `disassociate`, `whoami`, `validate`, `migrate`.
- **Verification script** `bin/test_profile_resolution.py` covering scenarios T1–T10 from the issue brief.

### Changed

- **Don't prompt when the mechanical sweep already answered** (issue #25). Three `/start` interactive prompts that re-asked questions the sweep had already resolved are gated or removed: (a) the carryover mini-triage now splits on `deferral_count` per methodology principles #4 (light-touch) / #5 (reckoning at threshold) — below `reckoning_threshold`, carryover tasks and their `defer_reason`s are surfaced read-only and the 5-way keep/defer/drop/backlog/re-scope prompt fires only at or above threshold; (b) the pre-work scan no longer inserts a synthetic "Morning communication work" completed task into the agenda — a single headline line summarizes the detected activity instead; (c) "Anything not on your calendar?" fires only when the calendar sweep failed or the integration was unavailable. Relates #4 (origin of the mini-triage feature).
- **Private-by-default EOD** (issue #23). The end-of-day flow is reordered to write the local handoff doc **before** drafting any external posts, and the session-close step is gated on handoff-write success. External drafts (project-management check-in comment, team messaging handoff) are display-only — the `Post / Edit first / Skip` prompt is removed, no MCP write method is auto-invoked, and either sub-draft is skipped silently if its prerequisite config (`personal_sub_issue` / `config.coordination_channel`) is unset. The stale-session handler no longer offers a retroactive external post; the backfilled handoff is the recovery artifact. Rationale: the local handoff is the load-bearing artifact for tomorrow's `/start` Step 0.25; principle #7 Graceful Degradation says the local-only path is primary and every integration is optional.
- **Remove residual setup-shape assumptions from start skill and inbox runbooks** (issue #20). The plugin no longer hardcodes specific MCP tool IDs, method names, or scopes in user-facing skill bodies and runbook documentation. `skills/start/SKILL.md` pre-flight now derives its check surface from the active profile's declared inbox sources rather than preloading a fixed list of MCP tools. `docs/inbox-runbooks.md` runbooks describe intent (what to collect, using user-declared config fields) rather than naming specific provider tools and methods. Skill frontmatter `allowed-tools` in `start`, `eod`, `pickup`, and `pre-plan` no longer lists integration-specific MCP tools — those are loaded dynamically via `ToolSearch` when a runbook needs them. `docs/state-schema.md` username example replaced with a generic placeholder. Body text in `skills/eod/`, `skills/pickup/`, `skills/start/` First-Run / Focus-loading sections, and `docs/morning-assembly.md` still references "Linear MCP" / "Gmail MCP" / specific tool-method names in descriptive (not executable) contexts — those are follow-up scope.
- `wpl profile switch` now prints a deprecation note. It still updates the `active` symlink for backward compatibility, but profile resolution no longer consults the symlink.
- `wpl profile list` shows each profile's workspaces and marks the one matching cwd.
- **Stale-session recovery unified with normal EOD handoff** (issue #13). `/start`'s stale-session handler now backfills a handoff at `~/.workplanner/profiles/<name>/handoffs/{stale_date}.md` using `bin/handoff.py write` — the same path `/eod` writes to on the normal path. A distinct session-id of the form `stale-recovery-{stale_date}` makes backfills visible in the file. Step 0.25 reads backfilled and normal-path handoffs identically. Re-running `/start` on a still-stale session is idempotent: if a `stale-recovery-*` sub-section is already present for the date, the handler logs "Handoff already written" and continues.
- **External Linear posting is now orthogonal to local handoff.** The stale-session handler no longer forks between "local-handoff mode" and "external-posting mode". The local handoff is always written; a retroactive Linear post is a separate, optional decision gated on Linear MCP availability and `personal_sub_issue`.

### Removed

- **`config.handoffs.*` deprecation warning** (issue #21). The stderr warning for legacy `config.handoffs.{dir,filename_pattern,carryover_from_handoff}` keys has served its grace period and is removed. `load_config()` is silent on load. Keys present in user config are ignored without comment; remove them at your convenience.

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
