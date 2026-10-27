# Task Transitions Reference

All task state mutations are handled by `bin/transition.py`. This document describes the state machine and transition semantics.

---

## State Machine

```
pending ──→ in_progress ──→ done
                │
                ├──→ blocked
                │
                ├──→ deferred
                │
                └──→ dropped
```

`dropped` (closed without doing it) is reachable from `pending`, `in_progress` and `blocked`.

Only one task should be `in_progress` at a time (except during parallel/dispatched work with `--no-pause`). `current_task_index` in the session JSON points to the active task.

## CLI Reference

```bash
wp <command> [args]
```

The `wpl` wrapper lives at `~/.workplanner/bin/wpl` and forwards to `bin/transition.py`. Add `~/.workplanner/bin` to your PATH for the short form. A legacy symlink at `/tmp/wp` is maintained for backward compatibility. If neither exists, use `python3 bin/transition.py` directly.

| Command | Args | Effect |
|---------|------|--------|
| `done` | `[target] [--as "<title>"]` | Mark a task done — the named one, or the current task if omitted (see "Explicit targets" below) |
| `drop` | `[target] --reason "..." [--as "<title>"]` | Close a task without doing it (handled elsewhere, optional, covered by someone else). Kept as `dropped` with its reason |
| `blocked` | `[target] [reason...]` | Mark a task blocked; a leading `tN`/uid names the task, the remaining words are the free-text reason |
| `defer` | `[target] [--until DATE] [--reason ...]` | Defer a task (named or current); with `--until` sends to backlog with target date |
| `add` | `<title> [flags]` | Add a new task (see flags below) |
| `move` | `<source> --to <dest>` | Reorder a task to a new position |
| `switch` | `<target> [--no-pause]` | Switch focus to a different task |
| `dispatch` | `<target>` | Mark a task as dispatched to another session |
| `remove` | `<target> [--as "<title>"]` | Remove a task entirely |
| `rename` | `<target> <new-title...> [--as "<old-title>"]` | Update a task's title; preserves the original in `original_title` on first rename |
| `edit` | `<target> [--notes TEXT \| --append-notes TEXT] [--url URL] [--ref REF] [--est MIN] [--as "<title>"]` | Edit a task's notes, URL, reference or estimate after `add`, at any status |
| `status` | — | Print one-line status summary and full compact task list |

### Global flags

| Flag | Default | Description |
|------|---------|-------------|
| `--format {text,json}` | `text` | Output format. Text is glyph-friendly and LLM- and human-readable. JSON emits a structured response suitable for programmatic parsing by skills that want machine-parsable output. |
| `--profile NAME` | — | Override profile resolution for this invocation. Bypasses path-based resolution and the `$WPL_PROFILE` env var. Use for scripts running outside any declared workspace (e.g. from `/tmp`) or for deliberate cross-profile inspection. See `docs/profiles.md`. |

### Profile resolution

Every `wpl` invocation resolves a profile before touching state. Order of
precedence: `--profile` CLI flag, then `$WPL_PROFILE` env var, then
longest-prefix match of cwd against each profile's declared
`workspaces: [...]`, then a single-profile fallback, then an interactive
prompt (TTY only), then failure. The global `active` symlink is not
consulted. See `docs/profiles.md` for the full flow and migration notes.

### `--as "<title>"` echo check

`done`, `drop`, `edit`, `rename` and `remove` accept an optional `--as "<title>"` argument. When present, the CLI compares the echoed title to the target task's actual title (case-insensitive, whitespace-tolerant). On mismatch, the mutation is refused with an error naming the actual title and UID, and the process exits non-zero. This is a cheap sanity check that catches "wrong-task" mistakes from stale display IDs, and self-documents the LLM's intent in the session transcript. Absent `--as`, behavior is unchanged.

### Explicit targets

`done`, `drop`, `blocked`, `defer` and `reckon` take an optional task target (`t3` or a uid). With a target they act on that task and leave `current_task_index` alone; without one they act on the current task.

```bash
wpl done t3                          # side task finished; t1 stays current
wpl blocked t4 waiting on review     # leading tN/uid is the target, the rest is the reason
wpl defer t5 --reason "needs input"
wpl reckon drop t6
```

Prefer the explicit form whenever more than one task is in flight, or more than one agent is working the session: it needs no `switch`, so it can't take the pointer from another agent. For `blocked`, only a leading `tN` or 8-character uid counts as a target, so a reason like `on 3 tests` passes through unchanged.

When the current task leaves `in_progress` (done, blocked, deferred, dropped, removed), the pointer falls back to the one other in-progress, non-dispatched task if there is exactly one; otherwise it clears.

### Status header — "no active" vs "all complete"

The status header distinguishes three end states:

- **`All tasks complete`** — every task in the plan landed in `done`.
- **`No active task — N pending`** — no task is `in_progress`, but `N` pending tasks remain. Common after a `switch X && done` sequence that doesn't auto-advance.
- **`No active task`** — no `in_progress` task and no pending tasks (only blocked/deferred tasks remain).

Only the first means the day is done.

### Stale-session detection

Reading a session whose `date` is older than today emits a one-line stderr warning naming the date and offset, and points at `/workplanner:start`. The command still runs (graceful degradation). JSON consumers can detect staleness programmatically via `is_stale: bool` and `session_date_offset_days: int` on the status payload.

### Profile breadcrumb

When `--profile NAME` or `$WPL_PROFILE` overrides cwd-based resolution, text mode prepends a one-line breadcrumb to stdout:

```
(profile: other — via --profile flag)
```

JSON mode carries the same information on every response via `profile_name` and `resolved_via` (one of `cli-flag`, `env-var`, `cwd-match`, `single-profile-fallback`, `unresolved`). Cwd-match resolution emits no breadcrumb (the common case stays quiet).

### Timezone configuration

When the resolved profile has no `timezone` set, `local_today()` falls back to UTC and emits a one-time stderr warning naming the config key. Set the timezone explicitly to silence:

```bash
wpl config set timezone America/Los_Angeles --rationale "..."
```

### `add` flags

| Flag | Default | Description |
|------|---------|-------------|
| `--est N` | 30 | Estimate in minutes |
| `--at POS` | after current | Position: `top`, `end`, `t3`, or 0-based index |
| `--done` | false | Mark as already completed |
| `--started HH:MM` | auto | Start time (used with `--done`) |
| `--finished HH:MM` | now | Finish time (used with `--done`) |
| `--parent <id>` | — | Create as a sub-task under the named parent (`t3`, index, or uid). See "Sub-tasks" below. |
| `--source VAL` | manual | Source tag |
| `--ref VAL` | — | Issue reference (e.g., `PROJ-123`) |
| `--url VAL` | — | Issue URL |
| `--notes VAL` | — | Free-text notes |

### Sub-tasks

Tasks can be nested one level via `--parent`. When the parent rolls up several related sub-steps that share context and a single gate, use this instead of a flat sibling list. The dashboard renders parents with their children as a tree.

```bash
# Parent: the umbrella task
wpl add "Eval harness: v1 milestone" --est 140 --at top

# Children: each step under the parent (parent is now t1)
wpl add "Check the reviewer's workflow blocker" --est 30 --parent t1
wpl add "Vendor judge prompt + schema" --est 20 --parent t1
wpl add "Pull candidates into v0.jsonl" --est 45 --parent t1
```

When to use it: project-scoped work with multiple sub-steps, a shared gate, and enough coherence that the parent is a meaningful unit of progress. When to skip it: a normal day's mixed-topic agenda — flat siblings read better than a tree of unrelated items.

The `parent` field is an integer index into the task array (see `docs/state-schema.md`). Every command that inserts, moves or removes tasks re-derives it, so sub-tasks stay attached; removing a parent makes its children top-level. There is no re-parent command today; if you need to restructure an existing flat group, remove the siblings and re-add them with `--parent`.

All mutating commands:
1. Read and validate `current-session.json`
2. Check preconditions (current task exists, valid index, etc.)
3. Apply mutation
4. Atomic write (tmp file → mv)
5. Re-render dashboard via `render_dashboard.py`
6. Print one-line confirmation *plus the full compact task list* to stdout (text mode), or a structured JSON response (JSON mode)

Exit code 0 on success, 1 on precondition failure (error to stderr).

### Post-mutation feedback loop

Every mutating command emits the full compact task list after its one-line confirmation. This keeps the LLM's model of session state synchronized with engine state across turn boundaries — without the LLM having to call `wpl status` between every mutation. The list includes UIDs (stable across mutations) alongside display IDs (`tN`, unstable; recomputed on every mutation). When addressing tasks, prefer UIDs.

A `PostToolUse` plugin hook (`bin/post-tool-use-hook.sh`) provides a secondary feedback channel: after any Bash call whose command starts with `wpl`, the hook injects a compact session-state summary into the next turn's context. Both channels reinforce each other; neither is redundant.

## Transition Effects

### done

- Sets `status: "done"`, `finished_at`, `actual_min` (elapsed since `started_at`; `null` if the task was never started — nothing was measured)
- Advances `current_task_index` to next pending task
- If no pending tasks remain, sets `current_task_index: null`

### blocked

- Sets `status: "blocked"`
- Appends reason to `notes` if provided
- Advances to next pending task

### drop

- Sets `status: "dropped"`, `drop_reason`, `finished_at`, and `actual_min` (elapsed if the task was started, otherwise `null`)
- `--reason` is required: a dropped task is a decision, and the reason is what makes it readable in the handoff and history
- Allowed from `pending`, `in_progress` and `blocked`; refused for `done`, `deferred` and `dropped`
- Counted separately from done: the status line reads `Done: 7/11 · 2 dropped`, and "All tasks complete" means every task is `done` or `dropped`
- Dropped tasks are not carried over; they stay in the session and in archived history
- `reckon x` drops the task the same way (reason: the task's `defer_reason`, or "dropped at reckoning") instead of deleting it

### edit

- Sets any of `notes` (`--notes` replaces, `--append-notes` appends with ` | `), `url`, `ref`, `estimate_min`
- Works at any status — adding the link where a finished task was resolved is the common case
- Titles go through `rename`, which keeps `original_title`
- Use it instead of editing the session JSON by hand during the day; it goes through the lock, undo snapshot and mutation log

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
- Switching to a dispatched task **reclaims** it — clears the `dispatched` flag. `--no-pause` only affects the previous task

### rename

- Updates the target task's `title` to the new value
- On the **first** rename, copies the prior title into `original_title`; repeat renames update `title` only and leave `original_title` untouched (preserves the planning-phase framing for retrospection)
- No-ops if the new title matches the current title (case-insensitive, whitespace-tolerant)
- `--as "<old-title>"` echo-checks against the *current* title — useful when an LLM is renaming to confirm it's pointing at the task it thinks it is
- Surfaces in the EOD handoff as `(was: <original_title>)` next to the deferred line when the two differ — no separate prompt; drift becomes visible in the artifact
- Use this when execution reshapes a task and the title-as-typed reads as fiction; the goal is that a week-later reader sees the as-shipped framing without losing the plan trace

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
- `switch` to a dispatched task reclaims it (clears the flag), with or without `--no-pause`
- Each `wpl` invocation holds an exclusive per-profile lock (`.wpl.lock`) from load to save, so concurrent agents can't overwrite each other's changes. A command that can't get the lock within 10s fails with a retryable error
- Agents should name tasks explicitly (`wpl done t3`) rather than `switch` + `done`: the current-task pointer is shared, so switching moves it for every agent
- Every mutation is appended to `mutations.jsonl` in the profile dir (timestamp, argv, pid/ppid, optional `$WPL_AGENT`, affected tasks), so conflicting changes can be traced afterwards
- `undo` is still global: it restores the last whole-session snapshot, whichever agent wrote it
