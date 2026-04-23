# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

A Claude Code plugin for daily work-planning. Sweeps inboxes each morning, builds a prioritized agenda, tracks task transitions via CLI, consolidates EOD updates. Supports multiple life contexts (work, home) via profiles.

## EA/PA methodology — when to consult

This plugin operates under a seven-principle EA/PA methodology ([`docs/methodology.md`](docs/methodology.md)) with a triage framework ([`docs/triage-framework.md`](docs/triage-framework.md)) that applies it to the daily agenda. When asked a work-shape question in a session tied to a workplanner profile — prioritization, triage, carryover, deferral, budget, scoping, rhythm — consult those docs before reasoning from first principles. The framework is opinionated-but-adjustable: deviations go through `wpl config` and are recorded in the decision log (`wpl config diff`, `wpl decision list`). Principle-indexed reasoning (e.g. "honors #4 Carryover Earns Its Place + #5 Force the Reckoning") beats reinvented PM heuristics.

## Development

No build step, no test suite, no linter. Python 3.9+ stdlib only (no dependencies). All scripts use `#!/usr/bin/env python3`.

Run the CLI directly:
```bash
python3 bin/transition.py status
python3 bin/transition.py profile list
python3 bin/transition.py decision list
python3 bin/transition.py config diff
python3 bin/render_dashboard.py
```

State files live in `~/.workplanner/` — the repo contains no runtime state.

Track bugs and features via **GitHub Issues** (https://github.com/melek/workplanner/issues).

## Architecture

### Three-layer separation

- **Engine** (`bin/`): CLI and state management. Methodology-agnostic. Handles task CRUD, profiles, backlog, decision log, dashboard rendering. Enforces rules mechanically via atomic JSON writes.
- **Methodology** (`docs/methodology.md` + config defaults): The reference productivity philosophy. Seven principles. Customizable via config changes recorded in the decision log.
- **Integrations** (inbox runbooks in `docs/` + profile config): Config-driven data source procedures. No hardcoded MCP servers or usernames.

### Plugin structure

- `.claude-plugin/plugin.json` — Plugin manifest with SessionStart hook
- `skills/*/SKILL.md` — Seven skills: start, eod, pickup, dispatch, pre-plan, horizon, freeze
- `bin/transition.py` — The state machine CLI (`wpl`). Single mutation interface.
- `bin/render_dashboard.py` — Renders dashboard from session JSON
- `bin/dashboard_tui.py` — Curses TUI for tmux dashboard pane
- `bin/write_event.py` — Dashboard alert queue
- `bin/session-hook.sh` — Injects workplan status into Claude Code sessions
- `docs/` — Procedural runbooks, methodology, schemas, reference config

### Data root: `~/.workplanner/`

```
~/.workplanner/
  user.json                          # Cross-profile identity
  decision-log.json                  # Methodology deviations
  profiles/
    active -> work/                  # Symlink to current profile
    work/
      config.json                    # MCP bindings, triage config
      session/current-session.json   # Today's schedule
      backlog.json                   # Future-scoped items
      undo.jsonl                     # Mutation undo log
      briefings/                     # Pre-generated task briefings
```

### Key design rules

1. `current-session.json` is the single source of truth. Dashboard and agenda are derived.
2. During the day, mutate state only through `bin/transition.py` (the `wpl` CLI).
3. During assembly (`/start`) and EOD (`/eod`), skills write JSON directly with atomic writes.
4. Task IDs (`t1`, `t2`, ...) are positional — derived from array index, not stored.
5. All integrations are optional. Graceful degradation everywhere.
6. Deterministic plumbing, flexible policy — engine enforces structure, LLM handles judgment.
