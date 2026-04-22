---
name: dispatch
description: "Hand off an workplanner task to a Claude Code session in a new tmux pane. Generates a handoff prompt with full task context and launches claude."
argument-hint: "[task — t3, index, or search term] [extra instructions...]"
allowed-tools: Read, Bash, Grep, Glob, ToolSearch, Agent
---

# Dispatch

Hand off an workplanner task to a Claude Code session in a new tmux pane. The session starts in normal interactive mode so it plans with the user before acting.

**Plugin root:** `${CLAUDE_PLUGIN_ROOT}`
**Transition CLI:** `${CLAUDE_PLUGIN_ROOT}/bin/transition.py`

## Profile resolution

Dispatch is the highest-stakes skill for cross-profile launches: if the parent `wpl` invocation uses `--profile other` (or `WPL_PROFILE=other`), the dispatched child must land in `other`'s state tree, not whatever the `active` symlink points at.

Resolve **both** `PROFILE_ROOT` and `PROFILE_NAME` at the top of the skill:

```bash
PROFILE_ROOT=$(wpl profile whoami --print-root)
PROFILE_NAME=$(wpl profile whoami --print-name)
```

Use `$PROFILE_ROOT` for file paths. Pass `WPL_PROFILE=$PROFILE_NAME` to the dispatched child so any `wpl` calls it makes inherit the same profile, regardless of the child's cwd (the user may launch in a different workspace tree).

## Arguments

`$ARGUMENTS`

Parse arguments:
- Empty → dispatch the current task
- `t3` → dispatch task at index 2
- `t3 fix the flaky test` → dispatch with extra instructions appended
- Everything after the task identifier is extra instructions

## Procedure

### 1. Load context

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/bin/transition.py" status
```

Parse the JSON output. Identify the target task:
- If a task ID/index argument is provided, use that
- Otherwise use `current_task_index`
- If neither exists, tell the user and stop

Collect from the status output:
- **Task:** index, title, status, estimate_min, ref, notes
- **Sub-tasks:** If the task has children (via `parent` field), summarize sibling status
- **Extra instructions:** Any text after the task ID in `$ARGUMENTS`

### 2. Mark task in_progress

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/bin/transition.py" switch <index>
```

### 3. Generate handoff prompt

Write a briefing prompt that sets context but does NOT direct Claude to start executing:

```
I'd like your help with: {title}

Context:
- Task: t{index+1} — {title}
- Source: {ref} (if present)
- Estimate: {estimate_min}m
- Notes: {notes, if present}

{Extra instructions from arguments, if any}

Review the situation and suggest an approach before making changes.
```

If the task has a `ref` that looks like a Linear issue, include: `Check Linear issue {ref} for full context.`

Keep it concise — the dispatched session has full filesystem and CLAUDE.md access.

### 4. Write prompt to file

```bash
mkdir -p "$PROFILE_ROOT/handoffs"
```

Write to `$PROFILE_ROOT/handoffs/t{index+1}.md`.

### 5. Launch tmux pane

Determine working directory from `$PROFILE_ROOT/config.json` field `working_directory`, or fall back to `$PWD`.

Write launcher to `/tmp/dispatch-t{index+1}.sh`. The launcher must export `WPL_PROFILE` so the child's `wpl` calls inherit the resolved profile, not whatever the child's cwd resolves to (which may be a different workspace tree):

```bash
#!/bin/bash -l
cd {working_dir}
export WPL_PROFILE={PROFILE_NAME}
claude --permission-mode plan "$(cat $PROFILE_ROOT/handoffs/t{index+1}.md)"
```

Interpolate `{PROFILE_NAME}` and `$PROFILE_ROOT` into the launcher text at write-time — the child shell won't have the parent's `$PROFILE_NAME` variable unless you bake it in.

Launch:
```bash
chmod +x /tmp/dispatch-t{index+1}.sh
tmux split-window -h -t '{top}' /tmp/dispatch-t{index+1}.sh
tmux select-pane -T "t{index+1}"
```

### 6. Confirm

```
Dispatched t{index+1} "{title}" → tmux pane "t{index+1}"
```

## Edge cases

- **No tmux:** Print the handoff prompt instead so the user can copy it.
- **Task not found:** List available task IDs from status output.
- **No current task:** Tell the user to pick a task first.
