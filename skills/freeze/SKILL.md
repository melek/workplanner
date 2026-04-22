---
name: freeze
description: "Save running Claude Code tmux sessions before a reboot, and restore them after. Captures session resume IDs by matching process start times to conversation log timestamps. Use when: reboot, restart, save sessions, freeze sessions, restore sessions."
argument-hint: "[restore]"
allowed-tools: Bash, Read
---

# Freeze / Restore Sessions

Save and restore Claude Code tmux sessions across reboots.

**Scripts:** `${CLAUDE_PLUGIN_ROOT}/bin/save-sessions.sh`, `${CLAUDE_PLUGIN_ROOT}/bin/restore-sessions.sh`

## Profile resolution

The freeze state file lives in the resolved profile's session directory. Resolve the root once:

```bash
PROFILE_ROOT=$(wpl profile whoami --print-root)
```

## Arguments

`$ARGUMENTS`

- Empty or `save` → **Save** (default)
- `restore` → **Restore**

## Save (pre-reboot)

Run from any tmux pane:

```bash
bash "${CLAUDE_PLUGIN_ROOT}/bin/save-sessions.sh"
```

Outputs saved session count and contents of `$PROFILE_ROOT/session/sessions.json`. Confirm to the user what was saved.

## Restore (post-reboot)

Requires running inside tmux:

```bash
bash "${CLAUDE_PLUGIN_ROOT}/bin/restore-sessions.sh"
```

Opens each saved session in its own tmux pane via `claude --resume <id>`.

## How it works

- Maps each tmux pane's claude PID to its conversation by correlating process start time with jsonl file birth time in `~/.claude/projects/`
- Falls back to `--resume` flag in process args for already-resumed sessions
- State file: `$PROFILE_ROOT/session/sessions.json` (per-profile)
