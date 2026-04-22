# EOD Consolidation Procedure

Internal reference for the end-of-day consolidation pipeline. Wraps up the session, posts updates, writes the local handoff doc, and prepares carryover for the next day.

---

## Overview

Five sequential steps. Unlike morning assembly, these are not checkpointed -- they run in order each time EOD is triggered.

---

## Step 1: Finalize Tasks

1. Check for any tasks with status `in_progress` or `pending`.
2. Present them to the user and ask once: "done, defer (with reason?), or blocked?"
3. The user can answer for all at once (e.g., "t3 done, t4 defer — waiting on legal").
4. Update all statuses accordingly in `current-session.json`. Deferred tasks should carry a `defer_reason` where the user provided one — `wpl defer --reason "..."` sets this field on the task, and it persists across carryover.

---

## Step 2: Draft Linear Update

1. Compile a daily update comment for the personal sub-issue (from `personal_sub_issue` in session state).
2. Format the comment as follows:

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

3. Show the draft to the user for review.
4. On confirmation, post via Linear MCP `create_comment` on the sub-issue.
5. Set `eod_posted: true` and store the comment URL in `eod_linear_comment_url` in session state.

---

## Step 3: Slack Handoff Draft

1. Draft a 2-3 sentence handoff message for the coordination channel (`config.coordination_channel`).
2. Summarize: what was done, what is carrying over, any blockers for the team.
3. **Display only** -- the user posts manually. Do NOT post to Slack.

---

## Step 4: Local Handoff Doc

1. Write (or update) today's handoff file at `~/.workplanner/profiles/<resolved-profile-name>/handoffs/YYYY-MM-DD.md` using `bin/handoff.py write`.
2. The file uses a merge-by-section format so concurrent sessions (dispatched tmux panes, multiple Claude instances) each contribute to their own `### <session-id>` sub-section without clobbering others.
3. Sections written: `Session trajectory`, `Deferred with reasons` (each deferred/blocked task with its `defer_reason`), `Open questions`, `Context for tomorrow`.
4. Session identifier is auto-detected: `$CLAUDE_SESSION_ID` → `$TMUX_PANE` → process-start-hash fallback.
5. Idempotent within a session: re-running EOD overwrites this session's sub-sections only.
6. On success, set `eod_handoff_written: true` in session state (a separate checkpoint from `eod_posted`, which tracks Linear posting).

**Stale-session recovery uses the same path.** When `/start` finds a session with `eod_posted: false` from an earlier date, it backfills a handoff at `~/.workplanner/profiles/<name>/handoffs/{stale_date}.md` using a distinct session-id of the form `stale-recovery-{stale_date}`. The next morning's `/start` Step 0.25 reads the backfilled handoff exactly as it would a normal `/eod`-written one — there is no second mechanism or separate path. See `skills/start/SKILL.md` → "Stale Session Handler".

## Step 5: Carryover & Close

1. Extract all tasks with status `deferred` or `blocked` from the session.
2. Write the carryover record into session state for the next morning assembly. Tasks keep their `defer_reason` field intact.
3. Close the session: set `checkpoint: "closed"`.
