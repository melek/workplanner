---
name: pickup
description: "Pick up an workplanner task in this session. Reads task context from the session, marks it in_progress, and starts planning the work. Use when opening a new Claude session to work on a specific task."
argument-hint: "[task — t3, index, or search term]"
allowed-tools: Read, Write, Edit, Glob, Grep, Bash, AskUserQuestion, Agent, ToolSearch, mcp__linear-server__get_issue, mcp__linear-server__list_comments
---

# Pickup

Pick up an workplanner task in this Claude session. Collects task context and starts planning.

**Plugin root:** `${CLAUDE_PLUGIN_ROOT}`
**Transition CLI:** `${CLAUDE_PLUGIN_ROOT}/bin/transition.py`

## Profile resolution

Resolve `PROFILE_ROOT` once and reuse. Do not hardcode `~/.workplanner/profiles/active/…`.

```bash
PROFILE_ROOT=$(wpl profile whoami --print-root)
```

## Arguments

`$ARGUMENTS`

- Task ID like `t3` → pick up task at index 2
- Integer like `2` → pick up task at index 2
- Search term like `API` or `dashboard` → find matching task by title/ref
- Empty → show available tasks and ask which one

## Procedure

### Step 1: Load session state

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/bin/transition.py" status
```

Parse the JSON output. If no session exists, stop: "No active session. Run `/workplanner:start` first."

### Step 2: Identify the task

**If argument is a task ID or index:** look it up directly in the status output.

**If argument is a search term:** match against task titles and refs (case-insensitive). If multiple matches, list them and ask.

**If no argument:** list all pending and in_progress tasks, ask which one.

If the task is already `done`, `blocked`, or `deferred`, warn and confirm before proceeding.

### Step 3: Mark in_progress

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/bin/transition.py" switch <index>
```

### Step 4: Check for existing briefing

Look for a pre-generated briefing in `$PROFILE_ROOT/briefings/{session.date}/`:

```bash
ls "$PROFILE_ROOT/briefings/{session.date}/" 2>/dev/null | grep "{task.uid}"
```

A briefing matches if the filename contains the task's UID.

**If a briefing exists:** Read it and use its Context, Key Decisions, Draft Plan, and Ready to Execute sections as the primary context source. Skip or abbreviate Step 5's live context gathering — the briefing already did this research.

**If no briefing exists:** Proceed to Step 5 for live context gathering. Optionally note: "No pre-plan briefing found. Gathering context now..."

### Step 5: Gather context (skip if briefing covers it)

Read the task object from session state. For each available context source:

- **`ref` field** (Linear issue): fetch the issue via Linear MCP (`get_issue`) for description, comments, status
- **`url` field**: note it for reference
- **`notes` field**: read for any handoff instructions or prior context
- **Related files**: if notes mention file paths, read them

If a briefing was found in Step 4, only fetch context for things the briefing flagged as needing fresh data (e.g., "Ready to Execute: No — check Linear for update from Sam").

### Step 6: Present and plan

Present a summary:

```
## Picking up: t3 — <title>
**Source:** <source> | **Estimate:** ~<est>m
**Briefing:** <"pre-planned" with relative age, or "live context">

### What I understand
<1-3 sentences about what this task involves>

### Key decisions
<from briefing or gathered context — bullet list of open questions>

### Suggested approach
<proposed steps — from briefing's Draft Plan if available, refined with any fresh context>
```

Then ask: "Ready to start, or want to adjust the approach?"

## Notes

- This skill is the local-session counterpart of `/workplanner:dispatch` (which spawns a new tmux pane)
- The user may run this from a different Claude session than the one that ran `/workplanner:start`
- Always use `transition.py` for state changes, never edit the JSON directly
