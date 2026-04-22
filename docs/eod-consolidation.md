# EOD Consolidation Procedure

Internal reference for the end-of-day consolidation pipeline. Wraps up the session, writes the local handoff doc (the load-bearing artifact for the next day), displays optional external drafts, and closes the session.

## Private-by-default posture

The local handoff doc is written before any external drafts are shown, and the session-close gate depends on a successful handoff write. External integrations (project-management check-ins, team messaging) are **display-only**: drafts are shown for the user's reference and never auto-posted. This reflects the three-layer architecture (engine/methodology first, integrations optional) and principle #7 Graceful Degradation.

---

## Overview

Four sequential steps. Unlike morning assembly, these are not checkpointed — they run in order each time EOD is triggered.

---

## Step 1: Finalize Tasks

1. Check for any tasks with status `in_progress` or `pending`.
2. Present them to the user and ask once: "done, defer (with reason?), blocked, or backlog?"
3. The user can answer for all at once (e.g., "t3 done, t4 defer — waiting on legal").
4. Update all statuses accordingly in `current-session.json`. Deferred tasks should carry a `defer_reason` where the user provided one — `wpl defer --reason "..."` sets this field on the task, and it persists across carryover.

---

## Step 2: Local Handoff Doc (mandatory)

1. Write (or update) today's handoff file at `~/.workplanner/profiles/<resolved-profile-name>/handoffs/YYYY-MM-DD.md` using `bin/handoff.py write`.
2. The file uses a merge-by-section format so concurrent sessions (dispatched tmux panes, multiple Claude instances) each contribute to their own `### <session-id>` sub-section without clobbering others.
3. Sections written: `Session trajectory`, `Deferred with reasons` (each deferred/blocked task with its `defer_reason`), `Open questions`, `Context for tomorrow`.
4. Session identifier is auto-detected: `$CLAUDE_SESSION_ID` → `$TMUX_PANE` → process-start-hash fallback.
5. Idempotent within a session: re-running EOD overwrites this session's sub-sections only.
6. On success, set `eod_handoff_written: true` in session state. On failure, surface the error, leave the session open, and do not advance to steps 3-4.

**Stale-session recovery uses the same path.** When `/start` finds a session with `eod_handoff_written: false` from an earlier date, it backfills a handoff at `~/.workplanner/profiles/<name>/handoffs/{stale_date}.md` using a distinct session-id of the form `stale-recovery-{stale_date}`. The next morning's `/start` Step 0.25 reads the backfilled handoff exactly as it would a normal `/eod`-written one — there is no second mechanism or separate path. No retroactive external-post prompt fires. See `skills/start/SKILL.md` → "Stale Session Handler".

---

## Step 3: Display Optional External Drafts

External drafts are **display-only** — no prompts, no auto-posting. The user reviews drafts on their own schedule and decides independently whether to publish.

### Step 3a: Project-management check-in draft (conditional)

1. Only if `personal_sub_issue` is set in session state.
2. Compile a draft comment from the day's tasks.
3. Format:
   ```markdown
   ### Monday, March 3 — Daily Update
   **Done:**
   - Reviewed API docs feedback, filed 2 issues
   - Tuned search ranking: accuracy up to 82%
   **Carrying over:**
   - Benchmark analysis — blocked on data refresh
   **Notes:**
   - (any user-provided notes)
   ```
4. Display with header: "Draft check-in for your sub-issue — copy and post yourself if useful."
5. Do NOT call any MCP write method. Do NOT prompt Post/Edit/Skip.

### Step 3b: Team messaging handoff draft (conditional)

1. Only if `config.coordination_channel` is set.
2. Draft a 2-3 sentence handoff message for the coordination channel.
3. Summarize: what was done, what is carrying over, any blockers for the team.
4. Display with header: "Draft handoff for the coordination channel — post it yourself if useful."
5. Do NOT auto-post. Do NOT call any messaging integration's send method.

If either source (`personal_sub_issue` / `config.coordination_channel`) is unset, skip that sub-step silently.

---

## Step 4: Carryover & Close

1. Extract all tasks with status `deferred` or `blocked` from the session.
2. Write the carryover record into session state for the next morning assembly. Tasks keep their `defer_reason` field intact.
3. Close the session: set `checkpoint: "closed"`. Step 2 must have succeeded; if it did not, do not close.
