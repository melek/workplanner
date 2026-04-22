---
name: eod
description: "End-of-day consolidation. Finalizes open tasks, drafts a check-in update for the user's project-management integration and a handoff message for their messaging integration, and closes the session. Deferred/blocked tasks carry over tomorrow. The two daily bookends — start opens the day, eod closes it."
argument-hint: ""
allowed-tools: Read, Write, Bash, AskUserQuestion, ToolSearch
---

# EOD Consolidation

End-of-day wrap-up. Five steps: finalize tasks, draft Linear update, draft Slack handoff, write the local handoff doc, close session.

**Plugin root:** `${CLAUDE_PLUGIN_ROOT}`
**Transition CLI:** `wpl` (or `${CLAUDE_PLUGIN_ROOT}/bin/transition.py` if wrapper not created yet)
**Handoff library:** `${CLAUDE_PLUGIN_ROOT}/bin/handoff.py` (read / write / path subcommands)
**Full procedure:** `${CLAUDE_PLUGIN_ROOT}/docs/eod-consolidation.md`

## Profile resolution

Before any file reads that reference profile state, resolve the concrete profile root **once** and reuse it as `PROFILE_ROOT`. Do not hardcode `~/.workplanner/profiles/active/…` — the `active` symlink is no longer the source of truth (it races under concurrent sessions / `--profile` overrides, per issue #10 and #16):

```bash
PROFILE_ROOT=$(wpl profile whoami --print-root)
```

All file paths below should be expressed relative to `$PROFILE_ROOT` (e.g. `$PROFILE_ROOT/session/current-session.json`). Subprocesses launched by this skill (`handoff.py`, `transition.py`) inherit the session's environment and run their own path-based resolution — no extra plumbing needed.

## Procedure

### Step 1: Finalize open tasks

```bash
wpl status
```

Check for any tasks with status `in_progress` or `pending`. If any exist, present them:

```
These tasks are still open:
▶ t2 — API integration review (in progress, 45m elapsed)
○ t5 — Weekly check-in (pending)
```

Ask once: "For each: done, defer (with reason?), blocked, or backlog?"

When deferring, **prompt for a reason** — "waiting on legal", "underspecified", "blocked on infra", etc. A one-line reason is enough; the point is to carry the "why" into tomorrow's carryover surface so the same task doesn't silently re-appear with zero context.

Let the user answer for all at once (free text, e.g. "t2 done, t5 defer — waiting on legal, t6 backlog friday"). Parse and apply each via transition CLI:

```bash
wpl done
wpl switch t5
wpl defer --reason "waiting on legal"
wpl switch t6
wpl backlog --from-current --target friday   # sends to backlog with target date
```

**Backlog option:** When the user says "backlog" for a task, optionally with a date (e.g., "backlog friday", "backlog 2026-04-01"), move it to the backlog file instead of deferring. Use `--from-current` or `--from t{N}` with `--target` if a date is given. This is for work that doesn't belong on tomorrow's agenda but needs to surface later.

**Deferral reckoning:** If `wpl defer` exits with code 2, the task has hit its deferral threshold (default: 3x). The CLI already printed the reckoning prompt (and will surface any prior `defer_reason` inline). Read the user's choice and apply via `wpl reckon <choice> [--date <date>] [--reason "..."]`. Options: `b` (break down — defer, then help decompose), `d` (delegate), `x` (drop), `t` (timebox to backlog, requires `--date`), `k` (keep deferring).

### Step 2: Draft Linear update

Read `personal_sub_issue` from session state. Compile an update from the day's tasks:

```markdown
### Thursday, March 5 — Daily Update
**Done:**
- <task title> — <brief note if any>
**Carrying over:**
- <task title> — <reason if blocked>
**Sent to backlog:**
- <task title> — <target date or "no date">
**Notes:**
- <any user-provided notes>
```

Show the draft. Ask: "Post this to your weekly check-in sub-issue?"
- Options: "Post it", "Edit first", "Skip"

If "Post it": use Linear MCP `save_comment` on the sub-issue ID. Record `eod_posted: true` and the comment URL in session state.

### Step 3: Slack handoff draft

Draft 2-3 sentences for the coordination channel (`config.coordination_channel`):
- What was accomplished today
- What's carrying over
- Any blockers for the team

**Display only.** Say: "Here's a draft handoff for the coordination channel — post it yourself if useful."

Do NOT post to Slack.

### Step 4: Write local handoff doc

Write (or update) the session's contribution to today's local handoff doc:

```
~/.workplanner/profiles/<resolved-profile-name>/handoffs/YYYY-MM-DD.md
```

Unlike the Linear/Slack drafts (which are one-shot posts to external systems), this is a **workspace-local, structured record** that tomorrow's `/start` will read during carryover mini-triage. Multiple sessions (dispatched tmux panes, multiple Claude instances) can each contribute; `handoff.py` merges by section and by session ID so writers never clobber each other.

**Sections this session writes:**
- **Session trajectory** — high-level narrative of what got done, deferred, blocked. Not a minute-by-minute log; a readable "here's where the day landed" paragraph or bulleted summary.
- **Deferred with reasons** — every task that ended the day in `deferred` or `blocked` status, with the `defer_reason` (or blocked reason from `notes`) inline. This is the field tomorrow's mini-triage pivots on.
- **Open questions** — anything the LLM (you) noticed during the day that needs a human decision, research, or escalation but wasn't captured elsewhere.
- **Context for tomorrow** — short, concrete pointers. "Check the deploy in #release-room first thing," "the PR #1234 discussion hit a wall — re-read before replying."

**Collecting the content:**

1. Read final session state:
   ```bash
   SESSION_JSON=$(cat "$PROFILE_ROOT/session/current-session.json")
   ```

   (The skill runs after tasks are finalized, so `deferred`/`blocked` statuses are accurate.)

2. Extract deferred/blocked tasks with their reasons. For each such task, build a dict:
   ```json
   {"title": "<task title>", "uid": "<task.uid>", "reason": "<task.defer_reason OR parsed from notes>"}
   ```

3. Pass everything to `handoff.py write`:
   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/bin/handoff.py" write \
     --trajectory "$TRAJECTORY_MARKDOWN" \
     --deferred-json "$DEFERRED_JSON" \
     --open-questions "$OPEN_Q_MARKDOWN" \
     --context "$CONTEXT_MARKDOWN"
   ```

   Omit any flag where this session has nothing to say — the library drops empty sub-sections automatically. Do NOT pass an explicit `--session-id`; the library picks one from `$CLAUDE_SESSION_ID` → `$TMUX_PANE` → process-start-hash fallback.

**Idempotent within a session:** If `/eod` is re-run (e.g., after a fix-up), the write overwrites *this session's* sub-sections only. Other sessions' contributions are preserved.

**Separate checkpoint from `eod_posted`:** writing the handoff is a distinct operation from posting to Linear. The session state records it as `eod_handoff_written: true` (extend `current-session.json` with this boolean — it's a new field, default absent/false for older sessions). This lets the stale-session handler distinguish "Linear post pending" from "handoff write pending".

### Step 5: Close session

Write final state to session JSON:
- Set `checkpoint: "closed"`
- Set `eod_handoff_written: true` (if Step 4 succeeded)
- Atomic write + re-render dashboard

Confirm: "Day closed. Handoff written to `~/.workplanner/profiles/<name>/handoffs/YYYY-MM-DD.md`. Deferred tasks will carry over tomorrow with their reasons."
