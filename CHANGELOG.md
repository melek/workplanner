# Changelog

## 1.0.0-beta.7

Task-outcome and handoff release — issues #59–#62.

### Added

- **`wpl drop <task> --reason "..."`** (issue #61). Closes a task without doing it — handled elsewhere, judged optional, covered by someone else — as a new `dropped` status with a required `drop_reason`. Dropped tasks stay in the session and in history, count separately from done (`Done: 7/11 · 2 dropped`), are not carried over, and show as `✗`. "All tasks complete" now means every task is done or dropped. `/eod` offers drop alongside done and defer and lists dropped tasks, with reasons, in the trajectory.
- **`wpl edit <task>`** (issue #60). Sets `--notes`, `--append-notes`, `--url`, `--ref` or `--est` after `add`, at any status, through the lock, undo snapshot and mutation log — no more hand edits of the session JSON mid-day. Titles stay with `rename`.
- **`handoff.py write --replace`** (issue #59). A full rewrite of this session's contribution; `/eod` and stale-session recovery use it.

### Changed

- **`handoff.py write` keeps sections you don't pass** (issue #59). A second write for the same session with only `--open-questions` used to delete its trajectory, deferred and context sections. Omitted now means kept; an explicit empty value (`--context ""`) clears a section; `--replace` restores the old full-rewrite behavior. Regression suite: `bin/test_issue_59.py`.
- **`reckon x` keeps the task as `dropped`** (issue #61). It used to delete the task, so the decision vanished from the session and history. The drop reason is the task's `defer_reason`, or "dropped at reckoning".

### Fixed

- **`actual_min` is `null` when nothing was measured** (issue #61). `done` (or `drop`) on a task that was never started recorded 0 minutes, which skewed estimate-accuracy data. It now stays `null` and displays as `—`; `history` shows a measured 0 as `0m` instead of `?`. Regression suite: `bin/test_issue_60_61.py`.
- **Spec drift** (issue #62). The task-object table in `docs/state-schema.md` now lists `uid`, `priority`, `dispatched` and `drop_reason`; a closed session started again the same day has an explicit Closed Session Handler (archive, carry over, assemble) in the start skill; `docs/morning-assembly.md` now agrees with the start skill that pre-work is a headline only, never a synthetic task.

## 1.0.0-beta.6

Task-integrity, multi-agent-safety and dashboard release — PR #56 across issues #2, #47–#55.

### Added

- **`dispatch.permission_mode` config and `--mode` argument for `/workplanner:dispatch`** (issue #51). Plan mode stays the default; a profile or a single dispatch can choose `default`, `acceptEdits` or `bypassPermissions`. The parent's mode is deliberately not inherited. The confirmation line names the mode used.
- **`runbook_addenda` config field** (issue #49). A profile can point at a markdown file of overrides to the base inbox runbooks; `/start` reads it during pre-flight. The start skill also opens with a short "What workplanner is" section that pins down what *profile* means and where the extension points are.

### Changed

- **EOD drafts from task state, not memory** (issue #52). The handoff trajectory and the check-in draft now read each task's `notes`, `url`, `ref` and `original_title` from `wpl --format json status`, so the report keeps its detail after context compaction or when another session runs EOD. `original_title` is now included in the JSON task record when set.
- **"Budget" replaces "left"** (issue #50). The dashboard summary, the `wpl` status line and mutation confirmations said `~90m left` next to an EOD clock time, which reads as wall-clock time. It is the summed estimate of unfinished tasks, now labelled `Budget: ~90m`. The budget-shortfall alert reads `~2h of tasks, 1h30m until EOD`.
- **Closed sessions render as between-days** (issue #48). After `/eod`, the dashboard header shows `CLOSED`, and the EOD/buffer line and alerts give way to a carry-over count and `Session closed [N days ago] — next: /workplanner:start`.

- **`done`, `blocked`, `defer` and `reckon` take an explicit target** (issue #54). `wpl done t3` acts on t3 and leaves the current-task pointer alone. Finishing a side task used to take switch → done → switch, and two agents switching took the pointer from each other. Reverses the "current task only" redirect from #18 (finding 5): `wpl done t3` now does what it says instead of erroring. For `blocked`, a leading `tN`/uid is the target and the remaining words are the reason. When the current task leaves `in_progress`, the pointer falls back to the one other in-progress, non-dispatched task, if there is exactly one.

### Fixed

- **Protected blocks stay where they happened** (issue #2). A block that has ended is now placed by recorded task start times instead of a projection from the current time, so it no longer drifts down the list as the day goes on. Several blocks landing on the same row all render (one used to overwrite the other), and a block landing on a sub-task moves to the next top-level row instead of disappearing. Regression suite: `bin/test_dashboard.py`.
- **The `wpl` wrapper survives plugin upgrades.** `~/.workplanner/bin/wpl` hardcodes the versioned plugin path, and Claude Code prunes old version directories, so after an upgrade the wrapper pointed at a deleted file until the next `/start`. The SessionStart hook now runs the current version's `transition.py` directly, which rewrites the wrapper, so the first session after an upgrade repairs it. Regression check: `bin/test_upgrade_wrapper.py`.
- **Tests no longer touch machine-global state.** With `WPL_ROOT` overridden, `transition.py` used to repoint the global `/tmp/wp` legacy symlinks at the throwaway root, and on machines with a leftover `~/work-planning/` it ran the one-time legacy migration into every fresh root (why `test_profile_resolution` T9 failed on some machines). Both now run only for the default root.
- **Concurrent `wpl` processes no longer lose updates** (issue #54). Each invocation holds an exclusive per-profile `flock` (`.wpl.lock`) across load → mutate → save. Before, 12 parallel `wpl add` calls kept 6 tasks; now they keep all 12. Every mutation is also appended to `mutations.jsonl` (argv, pid/ppid, optional `$WPL_AGENT`, affected tasks) so contention can be traced. `undo` is still a global snapshot stack; per-agent undo is left for a follow-up. Regression suite: `bin/test_issue_54.py`.

- **Sub-tasks stay attached to their parent across list mutations** (issues #53, #55). `parent` is a positional index, and only `add` tried to maintain it — with a comparison that crashed on an explicit `parent: null`. `move`, `remove`, `backlog --from`, `backlog --promote` and `reckon drop` left it stale, so children pointed at the wrong task or at themselves. All six now share `parent_refs()` / `reindex_parents()`, which capture parents as object references before the mutation and re-derive indices after. Removing a parent makes its children top-level. Regression suite: `bin/test_issue_55.py`.
- **`switch` always reclaims a dispatched task** (issue #47). `--no-pause` used to mean both "keep the previous task running" and "leave the target dispatched", so there was no way to reclaim a dispatched task while keeping another one in progress. It now governs only the previous task.

## 1.0.0-beta.5

Methodology-reach release — four merged PRs (#34, #36/#37/#38, this) across issues #33, #35, #40, focused on closing the gap between the plugin's engine capabilities and the skill prose that teaches them to the LLM.

### Changed

- **Document the graceful-degradation misapplication trap** (issue #40). A cold-session retest of the methodology reach surfaced one concrete misapplication: principle #7 (Graceful Degradation) was cited in a prioritization audit to justify criticality-based task sequencing. That's a priority-tier argument dressed in principle language. Added a "Not an argument for..." callout to principle #7 in `docs/methodology.md` explicitly naming the criticality-sequencing trap and pointing at `docs/triage-framework.md` as the right surface for that class of question. Single observed trap, single callout; no generalized framework. Follow-up traps get their own one-line callouts as and when cold-session tests surface them. (Issues #39 was filed for a broader "reasoning scaffolds + decision trees + auditor patterns" program but closed as over-scoped — the trust goal is better served by periodic cold-session retests catching drift than by bureaucratizing every EA-shape response.)
- **Make the EA/PA methodology reach and shape ad-hoc work-shape questions** (issue #35). Two attempts and two verification tests:
  - **Attempt 1 (PR #36):** Pointer added to the plugin's root `CLAUDE.md` on the assumption that plugin CLAUDE.md loads ambiently. Real-session test contradicted that assumption — the session confirmed it had read no plugin documentation.
  - **Attempt 2 (PR #37):** Pointer moved to the SessionStart hook (`bin/session-hook.sh`), which is already proven to reach sessions via its workplan-status output. Retest: the agent did read `docs/methodology.md`, but reasoned in generic PM vocabulary ("critical path", "budget", "risk") without applying any of the seven principles by name.
  - **Attempt 3 (this change):** Strengthened the hook directive from "consult" to "apply + cite at least one principle by name; if you deviate from a principle, name it and say why." Made generic PM vocabulary explicit as the failure mode to prevent. The observation from attempt 2 — that discoverability alone doesn't change reasoning behavior — is the reason. Plugin CLAUDE.md retains the pointer as source-reader documentation. `bin/test_start_prompts.py` asserts the stronger directive phrases are present.
- **Surface the sub-task (`--parent`) feature in skill prose and task-transition docs** (issue #33). The engine has supported `wpl add --parent <id>` (and the dashboard renders parent/child trees) for several versions, but the feature was invisible in `docs/task-transitions.md`, `skills/start/SKILL.md`, `docs/morning-assembly.md`, and `skills/pickup/SKILL.md` — so LLMs defaulted to flat sibling lists and project-scoped work was never grouped. Drift closed: the `add` flags table now lists `--parent`, there's a new "Sub-tasks" section in task-transitions with the CLI pattern and when-to-use guidance, the start-skill task-transition example includes a `--parent` line with a "parent/child for project work" explainer, Step 3 of morning-assembly adds a "Parent/child for project work" paragraph, and pickup notes that a parent's children should be inspected as a group. `bin/test_start_prompts.py` gains four regression assertions so the drift can't silently re-open.

## 1.0.0-beta.4

Session-tidy release — each change is a small, self-contained PR guided by the plugin's seven methodology principles and the three-layer architecture. Six merged PRs (#22, #24, #26, #28, #30, #32) over issues #21, #23, #25, #27, #29, #31.

### Added

- **Path-based profile resolution** (issue #10) — Profiles declare which filesystem paths they serve via a `workspaces: [...]` field in `config.json`. Each `wpl` invocation picks a profile by longest-prefix match of cwd against those workspaces, eliminating the race where concurrent sessions in different profiles flipped the `active` symlink out from under each other. Escape hatches: `--profile NAME` CLI flag and `$WPL_PROFILE` env var. Single-profile setups without workspaces keep working via fallback. See `docs/profiles.md`.
- **New profile subcommands:** `associate`, `disassociate`, `whoami`, `validate`, `migrate`.
- **Verification script** `bin/test_profile_resolution.py` covering scenarios T1–T10 from the issue brief.

### Changed

- **Make tmux dashboard pane opt-in via `config.dashboard_pane`** (issue #29). The `/start` Dashboard Pane section now branches on a three-value config field: `"auto"` (default; spawn when `$TMUX` is set — today's behavior), `"never"` (never spawn; wrappers and dashboard-view re-render still happen), `"always"` (attempt spawn regardless of `$TMUX`, log a note if not in tmux). Covers the case of a user in tmux who wants the pane skipped without leaving the session. Chosen over a full action-list dispatcher after a four-perspective panel review (Ousterhout / Leveson-STPA / EA / cognitive-ergonomics) concluded the dispatcher was premature generalization.
- **Fold `inbox_slack_channels` into `slack_channel_ids`** (issue #27). The two top-level config fields held identical channel-name → ID maps and read by different runbooks, creating drift risk and lookup ambiguity. `load_config()` now folds entries from `inbox_slack_channels` into `slack_channel_ids` (canonical wins on conflict), emits a one-time stderr deprecation warning, and leaves the legacy key in place so user config.json is untouched. Skill and runbook docs reference only `slack_channel_ids`. Reference config drops the legacy field. `bin/test_profile_resolution.py` adds three scenarios (S1-S3): merge with conflict, canonical-only silent, legacy-only promoted-with-warning.
- **Don't prompt when the mechanical sweep already answered** (issue #25). Three `/start` interactive prompts that re-asked questions the sweep had already resolved are gated or removed: (a) the carryover mini-triage now splits on `deferral_count` per methodology principles #4 (light-touch) / #5 (reckoning at threshold) — below `reckoning_threshold`, carryover tasks and their `defer_reason`s are surfaced read-only and the 5-way keep/defer/drop/backlog/re-scope prompt fires only at or above threshold; (b) the pre-work scan no longer inserts a synthetic "Morning communication work" completed task into the agenda — a single headline line summarizes the detected activity instead; (c) "Anything not on your calendar?" fires only when the calendar sweep failed or the integration was unavailable. Relates #4 (origin of the mini-triage feature).
- **Private-by-default EOD** (issue #23). The end-of-day flow is reordered to write the local handoff doc **before** drafting any external posts, and the session-close step is gated on handoff-write success. External drafts (project-management check-in comment, team messaging handoff) are display-only — the `Post / Edit first / Skip` prompt is removed, no MCP write method is auto-invoked, and either sub-draft is skipped silently if its prerequisite config (`personal_sub_issue` / `config.coordination_channel`) is unset. The stale-session handler no longer offers a retroactive external post; the backfilled handoff is the recovery artifact. Rationale: the local handoff is the load-bearing artifact for tomorrow's `/start` Step 0.25; principle #7 Graceful Degradation says the local-only path is primary and every integration is optional.
- **Remove residual setup-shape assumptions from start skill and inbox runbooks** (issue #20). The plugin no longer hardcodes specific MCP tool IDs, method names, or scopes in user-facing skill bodies and runbook documentation. `skills/start/SKILL.md` pre-flight now derives its check surface from the active profile's declared inbox sources rather than preloading a fixed list of MCP tools. `docs/inbox-runbooks.md` runbooks describe intent (what to collect, using user-declared config fields) rather than naming specific provider tools and methods. Skill frontmatter `allowed-tools` in `start`, `eod`, `pickup`, and `pre-plan` no longer lists integration-specific MCP tools — those are loaded dynamically via `ToolSearch` when a runbook needs them. `docs/state-schema.md` username example replaced with a generic placeholder. Body text in `skills/eod/`, `skills/pickup/`, `skills/start/` First-Run / Focus-loading sections, and `docs/morning-assembly.md` still references "Linear MCP" / "Gmail MCP" / specific tool-method names in descriptive (not executable) contexts — those are follow-up scope.
- `wpl profile switch` now prints a deprecation note. It still updates the `active` symlink for backward compatibility, but profile resolution no longer consults the symlink.
- `wpl profile list` shows each profile's workspaces and marks the one matching cwd.
- **Stale-session recovery unified with normal EOD handoff** (issue #13). `/start`'s stale-session handler now backfills a handoff at `~/.workplanner/profiles/<name>/handoffs/{stale_date}.md` using `bin/handoff.py write` — the same path `/eod` writes to on the normal path. A distinct session-id of the form `stale-recovery-{stale_date}` makes backfills visible in the file. Step 0.25 reads backfilled and normal-path handoffs identically. Re-running `/start` on a still-stale session is idempotent: if a `stale-recovery-*` sub-section is already present for the date, the handler logs "Handoff already written" and continues.
- **External Linear posting is now orthogonal to local handoff.** The stale-session handler no longer forks between "local-handoff mode" and "external-posting mode". The local handoff is always written; a retroactive Linear post is a separate, optional decision gated on Linear MCP availability and `personal_sub_issue`.

### Removed

- **Vestigial `eod_posted` and `eod_linear_comment_url` session fields** (issue #31). After #23 (private-by-default EOD), nothing in the plugin writes `eod_posted` or `eod_linear_comment_url`. The stale-session trigger now reads `eod_handoff_written` — the canonical, actually-set EOD-completion marker. `eod_handoff_written` is documented as the single source of truth for "did EOD complete." Existing session JSON files with the old fields are ignored; they drop off on the next write.
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
