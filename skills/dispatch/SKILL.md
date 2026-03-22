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
mkdir -p ~/.workplanner/profiles/active/handoffs
```

Write to `~/.workplanner/profiles/active/handoffs/t{index+1}.md`.

### 5. Launch tmux pane

Determine working directory from `~/.workplanner/profiles/active/config.json` field `working_directory`, or fall back to `$PWD`.

Write launcher to `/tmp/dispatch-t{index+1}.sh`:
```bash
#!/bin/bash -l
cd {working_dir}
claude --permission-mode plan "$(cat ~/.workplanner/profiles/active/handoffs/t{index+1}.md)"
```

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
