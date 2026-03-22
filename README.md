# workplanner

A Claude Code plugin for daily work-planning. Sweeps your inboxes each morning, builds a prioritized agenda with time estimates, tracks task transitions via CLI throughout the day, and consolidates end-of-day updates. Supports multiple life contexts (work, home, side projects) through a profile system.

## Install

Add via the marketplace entry in `~/.claude/settings.json`:

```json
"extraKnownMarketplaces": {
  "melek-claude-code-plugins": {
    "source": { "source": "github", "repo": "melek/workplanner" },
    "autoUpdate": true
  }
},
"enabledPlugins": {
  "workplanner@melek-claude-code-plugins": true
}
```

Or install from a local clone:

```bash
claude plugin add .    # from the repo root
```

## Usage

### Skills

Seven skills handle distinct phases of the day:

```
/workplanner:start      # Morning assembly — inbox sweep, triage, agenda build
/workplanner:eod        # End-of-day — finalize tasks, draft updates, close session
/workplanner:pre-plan   # Generate task briefings ahead of time
/workplanner:pickup     # Resume a task with pre-planned context
/workplanner:dispatch   # Hand off a task to a new tmux session
/workplanner:horizon    # Review and manage the backlog
/workplanner:freeze     # Save/restore tmux sessions across reboots
```

### CLI

Task transitions during the day go through the `wpl` CLI:

```bash
wpl done                              # Mark current task done, advance
wpl blocked "waiting on deploy"       # Mark blocked with reason
wpl defer                             # Defer to tomorrow
wpl add "Quick PR review" --est 15    # Add a task
wpl switch t3                         # Switch focus to task 3
wpl move t5 --to t2                   # Reorder tasks
wpl backlog "Future idea" --target next-week  # Send to backlog
wpl status                            # Print session summary
wpl undo                              # Undo last mutation
```

The `wpl` wrapper lives at `~/.workplanner/wpl`. Add it to your PATH:

```bash
echo 'export PATH="$HOME/.workplanner:$PATH"' >> ~/.zshrc
```

### Profiles

Workplanner supports multiple contexts via profiles. Each profile has independent config, session state, and backlog:

```bash
wpl profile list                  # Show all profiles
wpl profile create home           # Create a new profile
wpl profile switch home           # Switch active profile
wpl profile delete old-project    # Remove a profile
```

First-run setup creates your initial profile through a guided interview.

### Dashboard

A tmux pane shows live task progress:

```
 WORKPLAN  Mon 03 Mar — W10
------------------------------
 t1  API review            25m  done
 t2  Classifier tuning   14m/30m  active
 t3  Team channel reply     ~15m
 -- 10:00-12:00 Animal care --
 t4  Benchmark analysis     ~1h
 t5  Weekly check-in       ~30m
------------------------------
 Done: 1/5  |  ~2h15m left
 EOD: 17:00 |  Buffer: OK
```

Opened automatically during morning assembly. Uses `fswatch` for live updates (polls without it).

## Architecture

Three-layer separation:

- **Engine** (`bin/`) — CLI and state management. Methodology-agnostic. Atomic JSON writes.
- **Methodology** (`docs/methodology.md`) — Productivity philosophy. Seven principles. Customizable via config; deviations tracked in the decision log.
- **Integrations** (inbox runbooks in `docs/`) — Config-driven data source procedures. No hardcoded MCP servers.

State lives in `~/.workplanner/profiles/<name>/`. The session JSON is the single source of truth — dashboard and agenda are derived views.

For full architecture details, see [SPECIFICATION.md](SPECIFICATION.md).

## Methodology

Workplanner ships with a GTD-influenced productivity methodology covering triage, estimation, deferral reckoning, and more. Every default is overridable; changes are recorded in the decision log with rationale.

See [docs/methodology.md](docs/methodology.md) for the full philosophy.

## Dependencies

- **Python 3.9+** (stdlib only, no pip packages)
- **Claude Code** (plugin host)
- **Optional:** MCP servers for inbox sources (Linear, Gmail, Google Calendar, Slack, GitHub). All integrations degrade gracefully.
- **Optional:** `fswatch` for responsive dashboard updates (polls without it)
- **Optional:** `tmux` for dashboard pane and task dispatch

## Development

**Source:** https://github.com/melek/workplanner
**Issues:** [GitHub Issues](https://github.com/melek/workplanner/issues)

No build step, no test suite, no linter. Run the CLI directly:

```bash
python3 bin/transition.py status
python3 bin/transition.py profile list
python3 bin/transition.py decision list
```

## License

MIT
