---
name: eod
description: "End-of-day consolidation. Finalizes open tasks, writes the local handoff doc (the load-bearing artifact for tomorrow's /start), displays optional drafts for external systems without auto-posting, and closes the session. Deferred/blocked tasks carry over tomorrow. The two daily bookends — start opens the day, eod closes it."
argument-hint: ""
allowed-tools: Read, Write, Bash, AskUserQuestion, ToolSearch
---

# EOD Consolidation

End-of-day wrap-up. Four steps: finalize tasks, write the local handoff doc (mandatory), display optional external drafts, close session.

**Plugin root:** `${CLAUDE_PLUGIN_ROOT}`
**Transition CLI:** `wpl` (or `${CLAUDE_PLUGIN_ROOT}/bin/transition.py` if wrapper not created yet)
**Handoff library:** `${CLAUDE_PLUGIN_ROOT}/bin/handoff.py` (read / write / path subcommands)
**Full procedure:** `${CLAUDE_PLUGIN_ROOT}/docs/eod-consolidation.md`

## Private by default

The local handoff doc is the load-bearing artifact: tomorrow's `/start` reads it at Step 0.25 to carry decisions forward. It is **always** written and must succeed before the session closes.

External posts (project-management check-ins, team messaging handoffs) are **display-only** integrations. They are drafted for the user's reference and never auto-posted. If the user wants to publish a draft, they do so manually. No "Post this? [Post / Edit / Skip]" prompt fires at EOD.

This ordering reflects the three-layer architecture (engine/methodology first, integrations optional) and principle #7 Graceful Degradation: the plugin produces its primary artifact without any integration at all.

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

### Step 2: Write local handoff doc (mandatory)

This is the load-bearing artifact. Write (or update) the session's contribution to today's local handoff doc:

```
~/.workplanner/profiles/<resolved-profile-name>/handoffs/YYYY-MM-DD.md
```

Tomorrow's `/start` Step 0.25 reads this file to carry decisions, defer reasons, and context forward. Multiple sessions (dispatched tmux panes, multiple Claude instances) can each contribute; `handoff.py` merges by section and by session ID so writers never clobber each other.

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

**Session-close gate:** Step 4 (close session) only fires if this handoff write succeeded. If `handoff.py write` fails, surface the error, leave the session open, and let the user retry. Set `eod_handoff_written: true` in session state on success.

### Step 3: Display optional external drafts

External drafts are **display-only**. No auto-prompt. No auto-post. The user reviews the drafts and decides on their own schedule whether to copy/edit/publish any of them.

#### Step 3a: Project-management check-in draft (conditional)

Only if `personal_sub_issue` is set in session state: compile a draft check-in comment from the day's tasks.

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

Show the draft with a header like: "Draft check-in for your sub-issue — copy and post yourself if useful." Do not ask Post/Edit/Skip. Do not call any MCP write method.

If `personal_sub_issue` is unset, skip this sub-step silently.

#### Step 3b: Team messaging handoff draft (conditional)

Only if `config.coordination_channel` is set: draft 2-3 sentences for the coordination channel:
- What was accomplished today
- What's carrying over
- Any blockers for the team

Show with header: "Draft handoff for the coordination channel — post it yourself if useful." Do not auto-post. Do not call any messaging integration's send method.

If `config.coordination_channel` is unset, skip this sub-step silently.

### Step 4: Close session

Write final state to session JSON:
- Set `checkpoint: "closed"`
- Set `eod_handoff_written: true` (from Step 2 — must be true to reach this step)
- Atomic write + re-render dashboard

Confirm: "Day closed. Handoff written to `~/.workplanner/profiles/<name>/handoffs/YYYY-MM-DD.md`. Deferred tasks will carry over tomorrow with their reasons."
