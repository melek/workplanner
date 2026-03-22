# EOD Consolidation Procedure

Internal reference for the end-of-day consolidation pipeline. Wraps up the session, posts updates, and prepares carryover for the next day.

---

## Overview

Four sequential steps. Unlike morning assembly, these are not checkpointed -- they run in order each time EOD is triggered.

---

## Step 1: Finalize Tasks

1. Check for any tasks with status `in_progress` or `pending`.
2. Present them to the user and ask once: "done, defer, or blocked?"
3. The user can answer for all at once (e.g., "t3 done, t4 defer").
4. Update all statuses accordingly in `current-session.json`.

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

## Step 4: Carryover

1. Extract all tasks with status `deferred` or `blocked` from the session.
2. Write the carryover record into session state for the next morning assembly.
3. Close the session: set a final phase marker indicating the day is complete.
