# Task Transitions Reference

All task state mutations are handled by `bin/transition.py`. This document describes the state machine and transition semantics.

---

## State Machine

```
pending ──→ in_progress ──→ done
                │
                ├──→ blocked
                │
                └──→ deferred
```

Only one task should be `in_progress` at a time (except during parallel/dispatched work with `--no-pause`). `current_task_index` in the session JSON points to the active task.

## CLI Reference

```bash
wp <command> [args]
```

The `wpl` wrapper lives at `~/.workplanner/bin/wpl` and forwards to `bin/transition.py`. Add `~/.workplanner/bin` to your PATH for the short form. A legacy symlink at `/tmp/wp` is maintained for backward compatibility. If neither exists, use `python3 bin/transition.py` directly.

| Command | Args | Effect |
|---------|------|--------|
| `done` | — | Mark current task done (records actual_min), advance to next pending |
| `blocked` | `[reason...]` | Mark current task blocked, advance to next pending |
| `defer` | — | Mark current task deferred, advance to next pending |
| `add` | `<title> [flags]` | Add a new task (see flags below) |
| `move` | `<source> --to <dest>` | Reorder a task to a new position |
| `switch` | `<target> [--no-pause]` | Switch focus to a different task |
| `dispatch` | `<target>` | Mark a task as dispatched to another session |
| `status` | — | Print one-line status summary |

### `add` flags

| Flag | Default | Description |
|------|---------|-------------|
| `--est N` | 30 | Estimate in minutes |
| `--at POS` | after current | Position: `top`, `end`, `t3`, or 0-based index |
| `--done` | false | Mark as already completed |
| `--started HH:MM` | auto | Start time (used with `--done`) |
| `--finished HH:MM` | now | Finish time (used with `--done`) |
| `--source VAL` | manual | Source tag |
| `--ref VAL` | — | Issue reference (e.g., `PROJ-123`) |
| `--url VAL` | — | Issue URL |
| `--notes VAL` | — | Free-text notes |

All mutating commands:
1. Read and validate `current-session.json`
2. Check preconditions (current task exists, valid index, etc.)
3. Apply mutation
4. Atomic write (tmp file → mv)
5. Re-render dashboard via `render_dashboard.py`
6. Print one-line confirmation to stdout

Exit code 0 on success, 1 on precondition failure (error to stderr).

## Transition Effects

### done

- Sets `status: "done"`, `finished_at`, `actual_min` (elapsed since `started_at`)
- Advances `current_task_index` to next pending task
- If no pending tasks remain, sets `current_task_index: null`

### blocked

- Sets `status: "blocked"`
- Appends reason to `notes` if provided
- Advances to next pending task

### defer

- Sets `status: "deferred"`
- Advances to next pending task
- Deferred tasks carry over to the next session

### add

- Creates a task and inserts it at the specified position
- Default position: after the current task
- `--at top` inserts at position 0, `--at end` appends
- `--done` marks the task as completed with timestamps (for retroactive logging)
- Adjusts `current_task_index` when inserting at or before the current task

### move

- Removes a task from its current position and inserts it at the target
- Accepts `--to top`, `--to end`, `--to t3`, or `--to <index>`
- Adjusts `current_task_index` to track the same task after reordering
- No-op if source and destination are the same position

### switch

- Sets `current_task_index` to the target
- If target is `pending`, sets it to `in_progress` with `started_at`
- **Auto-pauses** the previous `in_progress` task (sets it back to `pending`)
- Use `--no-pause` to keep the previous task `in_progress` (for parallel/dispatched work)
- Switching to a dispatched task (without `--no-pause`) **reclaims** it — clears the `dispatched` flag

### dispatch

- Sets `dispatched: true` on the target task
- If target is `pending`, promotes it to `in_progress` with `started_at`
- Signals that this task is being worked on in another session
- `done` warns (stderr) when completing a dispatched task
- Dashboard shows `[⇄]` instead of `[▶]` for dispatched tasks

## Write Protocol

All mutations use atomic writes:
1. Write to `current-session.json.tmp`
2. `mv` to `current-session.json`
3. Call `render_dashboard.py` to regenerate `dashboard-view.txt`

**Conflict rule:** JSON is the source of truth. Markdown agenda and dashboard are derived views.

## Multi-Session Coordination

When tasks are dispatched to separate tmux panes via `/workplanner:dispatch`:

- The **main session** (the one that ran `/workplanner:start`) is the **state owner**
- Use `dispatch <target>` to mark a task as owned by another session — sets `dispatched: true`
- `/workplanner:pickup` and `/workplanner:dispatch` should call `dispatch` on the target task
- Any session can use `wpl` (or `~/.workplanner/bin/wpl`) to update task state (the wrapper self-heals on every run)
- `done` warns when completing a dispatched task (another session may still be working on it)
- `switch` to a dispatched task reclaims it (clears the flag); use `--no-pause` to keep it dispatched
- `transition.py` uses atomic writes (tmp → mv) which prevents file corruption but not logical races — coordinate through the main session to avoid conflicting state changes
