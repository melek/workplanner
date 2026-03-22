---
name: pre-plan
description: "Generate task briefings and surface workplan revisions. Researches tasks in parallel, writes briefings to ~/.workplanner/profiles/active/briefings/{date}/, then presents strategic revisions (decomposition, estimate corrections, duplicates, completed tasks) as a batch for cross-task awareness."
argument-hint: "[all | t3 | search term] — defaults to all pending tasks"
allowed-tools: Read, Write, Edit, Bash, Glob, Grep, Agent, ToolSearch, AskUserQuestion, mcp__linear-server__get_issue, mcp__linear-server__list_comments
---

# Pre-Plan

Generate task briefings ahead of time and surface strategic workplan revisions. Two phases: (1) parallel research agents produce per-task briefings, (2) their signals are collected and reviewed as a batch with cross-task awareness — enabling decomposition, estimate fixes, deduplication, and reordering before work begins.

**Plugin root:** `${CLAUDE_PLUGIN_ROOT}`
**Transition CLI:** `${CLAUDE_PLUGIN_ROOT}/bin/transition.py`

## Arguments

`$ARGUMENTS`

- `all` or empty → briefings for all pending/in_progress tasks
- Task ID like `t3` → briefing for that one task
- Search term like `API` → find matching task, brief it
- Multiple IDs like `t2 t4 t6` → brief those specific tasks
- `--force` → regenerate even if fresh briefings exist

## Procedure

### Step 1: Load session state

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/bin/transition.py" status
```

Parse JSON. If no session: "No active session. Run `/workplanner:start` first."

### Step 2: Select tasks to brief

Based on arguments, build a list of tasks to brief. Default: all tasks with status `pending` or `in_progress`. Skip `done`, `deferred`, `blocked` unless explicitly targeted.

For each selected task, check if a fresh briefing already exists (see Step 3). If it does, note it and skip unless the user passed `--force`.

### Step 3: Check for existing briefings

```bash
ls ~/.workplanner/profiles/active/briefings/{session.date}/ 2>/dev/null
```

A briefing is **fresh** if:
- It exists in the directory matching `session.date`
- Its filename contains the task's UID (e.g., `t02-e8a45522-ciab-docs.md`)

If all targeted tasks already have fresh briefings, report: "All briefings are current. Use `--force` to regenerate."

### Step 4: Classify tasks by archetype

For each task, classify its likely work pattern to guide the briefing agent:

| Archetype | Signals | Briefing focus |
|-----------|---------|----------------|
| `inventory` | "review", "audit", "list", "check" | Enumerate scope, count items, surface patterns |
| `diff-analysis` | "compare", "regression", version refs | Baseline vs current, key metrics, delta summary |
| `draft` | "write", "draft", "post", "document" | Outline, key points, audience, prior art |
| `scope` | "plan", "design", "architect", "RFC" | Requirements, constraints, open questions, options |
| `gather` | "research", "investigate", "find" | Sources to check, known context, gaps |
| `decision` | "decide", "choose", "evaluate" | Options, trade-offs, stakeholder input needed |
| `execute` | (default) | Steps, blockers, dependencies, key files |

Classification is best-effort from the task title, ref, and notes. Default to `execute`.

### Step 5: Launch briefing agents in parallel

For each task needing a briefing, launch an Agent (subagent_type: general-purpose) with a prompt like:

```
Research and prepare a briefing for this task. Do NOT make any changes — read-only research only.

Task: t{N} — {title}
Archetype: {archetype}
Source: {source}
Ref: {ref or "none"}
URL: {url or "none"}
Notes: {notes or "none"}
Estimate: {estimate_min}m

Research instructions:
- If there's a Linear ref, fetch the issue description, status, comments, and any linked issues
- If there's a URL, note it for context
- Check the current working directory and related files for relevant code/config
- For "draft" tasks, look for templates or prior examples
- For "scope" tasks, identify open questions and constraints
- For "inventory" tasks, enumerate what needs reviewing

Your response MUST contain two clearly separated sections. Use these exact headers:

=== BRIEFING ===

---
task: t{N}
uid: {uid}
title: {title}
ref: {ref}
archetype: {archetype}
generated: {ISO 8601 timestamp}
---

## Context
[2-4 sentences: what this task is about, why it matters, where it came from]

## Key Decisions
[Bullet list of decisions to make or questions to answer. "None identified" if straightforward.]

## Draft Plan
[Numbered steps for executing this task. Be specific — name files, commands, people.]

## Ready to Execute
[Yes/No + any blockers or prerequisites. E.g., "Yes" or "No — waiting on API access from Sam"]

## Estimated Pickup Time
[Re-estimate based on research. "{original}m as estimated" or "{revised}m (originally {original}m) — {reason}"]

=== WORKPLAN SIGNALS ===

Based on your research, report ANY of the following that apply. If none apply, write "No signals." Be specific — include evidence from what you found.

- DECOMPOSE: This task should be split into subtasks. [List the subtasks with estimates.]
- ESTIMATE: The estimate is wrong. [New estimate and why.]
- DONE: This task appears already completed. [Evidence: PR merged, issue closed, etc.]
- DONE_BY_OTHER: Someone else completed this. [Who, when, evidence.]
- DUPLICATE: This overlaps with another task in the session. [Which task and how.]
- BLOCKED: This task has a dependency that isn't met. [What's blocking.]
- REORDER: This task should move earlier/later. [Why — e.g., blocks another task.]
- SCOPE_CHANGE: The actual scope differs from what the title suggests. [What changed.]
- QUESTION: Something needs clarification before this can start. [The question and who to ask.]
```

Use `run_in_background: false` for single tasks, `run_in_background: true` for batch (3+ tasks) — but collect all results before proceeding.

**Important:** Agents must be read-only. No file writes, no state changes, no external API calls beyond reading.

### Step 6: Write briefings

```bash
mkdir -p ~/.workplanner/profiles/active/briefings/{session.date}
```

For each completed agent, extract the `=== BRIEFING ===` section and write to:
```
~/.workplanner/profiles/active/briefings/{session.date}/t{NN}-{uid}-{slug}.md
```

Where:
- `{NN}` is zero-padded display index (01, 02, ...)
- `{uid}` is the task's 8-char UID
- `{slug}` is a kebab-case slug from the title (max 30 chars, truncated at word boundary)

Example: `t02-e8a45522-ciab-docs.md`

### Step 7: Collect and process workplan signals

Extract the `=== WORKPLAN SIGNALS ===` section from each agent's output. Discard any "No signals." entries.

**Cross-task analysis:** Now review all signals together with the full task list visible. Look for:
- **Symmetry:** If t2 says DUPLICATE of t5 but t5 didn't flag it, confirm the overlap
- **Cascade:** If t3 says BLOCKED by something t1 produces, suggest reordering
- **Consolidation:** Multiple DECOMPOSE signals that share subtasks → merge into one decomposition
- **Conflicts:** One agent says DONE, another references it as a dependency → verify

Group signals into a revision plan:

```markdown
## Workplan Revisions

Based on pre-planning research, {N} signals were found across {M} tasks:

### Estimates
- **t2** API docs: 30m → 60m (3 docs to write, not 1)
- **t4** Performance analysis: 30m as estimated

### Decomposition
- **t5** "Set up team rotation" should split into:
  - t5a: Read onboarding doc (~15m)
  - t5b: Post welcome thread (~15m)
  - t5c: Update Linear team membership (~10m)

### Status changes
- **t3** "Reply to Sam's comment" — already done (comment posted yesterday at 16:42)

### Dependencies & ordering
- **t4** blocks **t6** (analysis results needed for accuracy report) — suggest moving t4 before t6

### Questions for you
- **t2**: Should API docs target the existing template or start fresh? (affects estimate)
- **t7**: Linear issue PROJ-680 was reassigned to Alex — still yours?
```

### Step 8: Apply revisions (auto + interactive)

Signals fall into two confidence tiers. **Auto-apply** signals that have clear evidence and low risk. **Ask the user** when confidence is low, context is missing, or the decision is preference-dependent.

If there are no signals at all, skip to Step 9.

#### Auto-apply (high confidence, reversible, evidence-backed)

Apply these immediately via `transition.py`, logging each action as you go:

| Signal | Condition to auto-apply | Action |
|--------|------------------------|--------|
| DONE | Issue status is "Done"/"Closed" in Linear, or PR merged, or comment already posted — agent cited specific evidence | `switch {index} && done` |
| DONE_BY_OTHER | Agent found a specific person + timestamp + artifact | `switch {index} && done` (note who in briefing) |
| BLOCKED | Agent identified a concrete unmet dependency (missing access, waiting on deploy, etc.) | `switch {index} && blocked "{reason}"` |
| ESTIMATE | Research reveals clear scope mismatch (e.g., "3 docs not 1", "API has 12 endpoints not 3") | Note revised estimate in briefing's "Estimated Pickup Time" section |
| REORDER | Task A produces output that task B explicitly needs as input — both tasks are in the session | `move {source} --to {dest}` |

#### Ask the user (low confidence, irreversible, or preference-dependent)

Collect these into a single prompt and present together for cross-task context:

| Signal | Why it needs confirmation |
|--------|-------------------------|
| DECOMPOSE | Subtask granularity is a preference — some people prefer one big task, others want fine splits |
| DUPLICATE | Which task to keep vs defer depends on framing preference and which has more context |
| SCOPE_CHANGE | The user may have intentionally scoped it differently from what Linear says |
| QUESTION | By definition requires human input |
| DONE / DONE_BY_OTHER | When evidence is indirect or ambiguous (e.g., "seems like this was addressed in a thread") |
| REORDER | When the dependency is soft (nice-to-have ordering, not a hard block) |

Present as:

```
## Revisions needing your input

{N} signals need a decision:

1. **t5 — DECOMPOSE:** Split "Team rotation setup" into 3 subtasks?
   - t5a: Read onboarding doc (~15m)
   - t5b: Post welcome thread (~15m)
   - t5c: Update Linear team membership (~10m)
   → [y] split / [n] keep as-is

2. **t7 — DUPLICATE:** Overlaps with t2 (both reference PROJ-680).
   → [keep t2] / [keep t7] / [keep both]

3. **t2 — QUESTION:** Should API docs target the existing template or start fresh?
   → (your answer here)
```

Use AskUserQuestion for the batch. Apply approved changes via `transition.py`.

#### Restore active task

After all mutations, switch back to the previously active task (or leave null if none was active):
```bash
python3 "${CLAUDE_PLUGIN_ROOT}/bin/transition.py" switch {original_current_index}
```

### Step 9: Write index README

Write `~/.workplanner/profiles/active/briefings/{session.date}/README.md`:

```markdown
# Briefings — {date}

Generated: {timestamp}
Session: {session.date}, {session.week}

| Task | Title | Archetype | Status | Signals |
|------|-------|-----------|--------|---------|
| t1 | Fix notification loop | execute | briefed | — |
| t2 | API docs | draft | briefed | estimate revised (60m) |
| t3 | Reply to Sam | execute | done | completed yesterday |
| t4 | Performance analysis | diff-analysis | briefed | blocks t6 |
| t5 | Team rotation setup | scope | briefed | decomposed → 3 subtasks |

## Revisions
### Auto-applied
- t3 marked done (already completed — Linear issue closed)
- t4 moved before t6 (hard dependency)

### User-approved
- t5 decomposed into t5a, t5b, t5c

## Files
- [t01-a1b2c3d4-fix-email-bot.md](t01-a1b2c3d4-fix-email-bot.md)
- [t02-e8a45522-ciab-docs.md](t02-e8a45522-ciab-docs.md)
- [t04-f9g8h7i6-performance-analysis.md](t04-f9g8h7i6-performance-analysis.md)
- [t05-h1i2j3k4-team-rotation-setup.md](t05-h1i2j3k4-team-rotation-setup.md)
```

### Step 10: Report

```
Pre-planned {N} tasks → ~/.workplanner/profiles/active/briefings/{date}/

Briefings:
  t1 — Fix notification loop (execute, ~30m)
  t2 — API docs (draft, ~60m ⬆ revised from 30m)
  t4 — Performance analysis (diff-analysis, ~30m)
  t5 — Team rotation setup (scope, decomposed → 3 subtasks)

Auto-applied:
  ✓ t3 marked done (Linear issue closed yesterday)
  ✓ t4 moved before t6 (hard dependency)

User-approved:
  ✓ t5 decomposed into 3 subtasks (~40m total)

Skipped: t6 (already briefed)
```

## Notes

- Briefings are workspace-agnostic — they reference tasks by title/ref, not repo-specific paths
- The `/pickup` skill checks this directory automatically when switching tasks
- Old date directories are left in place (no auto-cleanup)
- Re-running `/pre-plan` for a task that already has a briefing skips it unless `--force` is passed
- Agents are launched in parallel for batch briefings — expect ~30-90s total for a full day's tasks
- High-confidence signals (DONE with evidence, BLOCKED with concrete dependency, hard REORDER) are auto-applied — all via transition.py so they're in the undo log
- Low-confidence or preference-dependent signals (DECOMPOSE, DUPLICATE, SCOPE_CHANGE, QUESTION) are batched and presented for user confirmation
- Cross-task signal analysis happens in the main context (not in agents) so it has visibility into the full task list
- All auto-applied changes are reversible via `transition.py undo`
