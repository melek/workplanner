---
name: pickup
description: "Pick up an workplanner task in this session. Reads task context from the session, marks it in_progress, and starts planning the work. Use when opening a new Claude session to work on a specific task."
argument-hint: "[task — t3, index, or search term]"
allowed-tools: Read, Write, Edit, Glob, Grep, Bash, AskUserQuestion, Agent, ToolSearch
---

# Pickup

Pick up an workplanner task in this Claude session. Collects task context and starts planning.

**Plugin root:** `${CLAUDE_PLUGIN_ROOT}`
**Transition CLI:** `${CLAUDE_PLUGIN_ROOT}/bin/transition.py`

**Principles applied (see `docs/methodology.md`):** Step 6 implements **Brief Before Gate (completed staff work)** — the briefing is presented and the principal's yes/no is the gate. Step 7 implements the **No Surprises** rule — the acknowledgment is recorded via `wpl brief` so subsequent advance commands carry the principal's authorization on record. Skipping Step 7 means `wpl done` / `wpl blocked` / `wpl defer` will refuse with an error; that's intentional.

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

**If the task is a parent (has children):** the children are related sub-steps that share the parent's gate. Read them alongside the parent and present the whole group in Step 6 rather than picking up the parent in isolation. Children are tasks whose `parent` field points at this task's index; `wpl status` renders them nested below the parent.

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

Then ask: "Ready to start, or want to adjust the approach?" **On go-ahead, proceed to Step 7.**

### Step 7: Record acknowledgment

Once the principal gives the go-ahead ("ready", "go", "yes", or "go but with X" once the X has been incorporated), record the briefing acknowledgment so subsequent advance mutations will succeed:

```bash
# When a briefing artifact file exists (e.g. from /workplanner:pre-plan):
wpl brief <task-id> --artifact-path "$PROFILE_ROOT/briefings/{date}/{filename}"

# Otherwise (live-context-only pickup, no on-disk briefing):
wpl brief <task-id>
```

This is the structural reflection of **Brief Before Gate** (methodology principle 3): without the recorded acknowledgment, `wpl done` / `wpl blocked` / `wpl defer` / `wpl reckon keep|break|delegate` will refuse with an error and self-correction message naming this skill or `wpl brief` directly. The refusal is intentional — if the principal hasn't acknowledged the brief, the staff (CLI) hasn't been authorized to advance.

If the principal redirects ("not now, go work on t5 instead", "actually skip this", "block on Sam"), don't record the brief — pickup ended without authorization, which is itself a valid outcome. Re-pickup later when the conditions are right.

## Notes

- This skill is the local-session counterpart of `/workplanner:dispatch` (which spawns a new tmux pane)
- The user may run this from a different Claude session than the one that ran `/workplanner:start`
- Always use `transition.py` for state changes, never edit the JSON directly
