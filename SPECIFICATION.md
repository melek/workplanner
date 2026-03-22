# Workplanner Specification

Architecture and contracts for the workplanner Claude Code plugin.

For the productivity methodology, see [docs/methodology.md](docs/methodology.md).
For the data model and JSON schemas, see [docs/state-schema.md](docs/state-schema.md).
For reference configuration, see [docs/reference-config.md](docs/reference-config.md).

## Three-layer architecture

Workplanner separates concerns into three layers:

1. **Engine** (`bin/`) — Deterministic CLI and state management. Handles task CRUD, profile switching, backlog, decision log, dashboard rendering, atomic writes, and undo. Methodology-agnostic: the engine enforces structure but makes no judgment calls.

2. **Methodology** (`docs/methodology.md` + config defaults) — The productivity philosophy that drives triage, ordering, estimation, and deferral policy. Seven principles codified as configurable defaults. Users can override any principle; deviations are recorded in the decision log.

3. **Integrations** (inbox runbooks in `docs/` + profile config) — Procedures for sweeping data sources (Linear, Slack, email, calendar, GitHub). Each runbook is documentation that Claude Code follows — not executable code. All integrations are optional; adding a new source means writing a runbook and updating the `/start` skill.

## How a day works

Two bookends bracket the day:

- **`/start`** (morning assembly) — Sweep inboxes, load focus, triage, build agenda. Four checkpointed steps: `initialized` -> `inbox_swept` -> `focus_loaded` -> `agenda_built` -> `assembly_complete`. Checkpoint-resumable if interrupted.

- **`/eod`** (end-of-day) — Finalize open tasks, draft team update, draft handoff message, close session. Deferred and blocked tasks carry over to tomorrow.

Between the bookends, all mutations go through the `wpl` CLI (see [CLI reference](#cli-reference)).

Optional skills: `/pre-plan` (batch briefings), `/pickup` (resume with context), `/dispatch` (parallel sessions), `/horizon` (backlog review), `/freeze` (session persistence).

## Profile system

State lives in `~/.workplanner/` with profile-based isolation:

```
~/.workplanner/
  user.json                        # Cross-profile identity (timezone, name)
  decision-log.json                # Methodology deviations (shared)
  profiles/
    active -> work/                # Symlink to current profile
    work/
      config.json                  # MCP bindings, triage config
      session/current-session.json # Today's schedule
      backlog.json                 # Future-scoped items
      undo.jsonl                   # Mutation undo log
      briefings/                   # Pre-generated task briefings
    home/
      config.json
      ...
```

Each profile has independent config, session state, and backlog. The `active` symlink determines which profile is current. Switch with `wpl profile switch <name>`.

First-run setup is a GTD-principled interview that creates `user.json` and the initial profile.

## CLI reference

`wpl` is a wrapper around `bin/transition.py`. Core commands:

| Command | Effect |
|---------|--------|
| `wpl done` | Complete current task, advance to next |
| `wpl blocked "reason"` | Block current task |
| `wpl defer` | Defer to tomorrow (triggers reckoning at threshold) |
| `wpl add "title" --est N` | Add a new task |
| `wpl switch tN` | Switch to task N |
| `wpl move tN --to tM` | Reorder tasks |
| `wpl backlog "title" --target DATE` | Add to backlog |
| `wpl status` | Show current state |
| `wpl undo` | Undo last mutation |
| `wpl profile list/create/switch/delete` | Manage profiles |
| `wpl decision list/why` | Query the decision log |
| `wpl config diff` | Show config deviations from defaults |

## State machine

**Task statuses:** `pending` -> `in_progress` -> `done` | `blocked` | `deferred`

Only one task can be `in_progress` at a time. `done` auto-advances to next pending. Task IDs (`t1`, `t2`, ...) are positional — derived from array index, not stored.

## Decision log

When a user overrides a methodology default (e.g., changes the task cap, adjusts deferral threshold), the change is recorded in `~/.workplanner/decision-log.json` with a timestamp, the old/new values, and a rationale. This provides an audit trail and enables `wpl config diff` to show all deviations from reference defaults.

## Integration model

Each inbox runbook follows the same pattern:
1. Check if the required MCP server is available
2. If unavailable, skip gracefully
3. If available, execute a fixed query sequence
4. Append results to session inbox items

All integrations are optional. A single source failure never blocks assembly. To add a new source: write a runbook in `docs/inbox-runbooks.md`, add source priority and estimate defaults to config, update the `/start` skill.

## Atomic writes

All state mutations use write-to-temp-then-rename. This prevents partial reads from concurrent processes (multiple Claude Code sessions, dashboard renderer, CLI).

## Extending workplanner

- **Custom triage rules:** Override `config.triage` (source priorities, estimates, task cap, deferral threshold)
- **Custom protected blocks:** Add to `config.protected_blocks`
- **New inbox sources:** Write a runbook, no code changes needed
- **Config schema is open:** Add fields for custom runbooks or skills; the engine ignores unknown fields
