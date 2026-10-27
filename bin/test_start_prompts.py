#!/usr/bin/env python3
"""Verification for issue #25 (stop prompting when sweep answered).

Asserts structural invariants of the /start skill prose so a future
edit can't re-introduce the unconditional prompts or the synthetic
pre-work completed-task insertion. Tests are content-level (grep-
style) because /start is skill prose, not engine code.

Exit 0 on success, 1 on any failed assertion.

Usage:
    python3 bin/test_start_prompts.py
"""

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
START_SKILL = REPO / "skills" / "start" / "SKILL.md"
INBOX_RUNBOOKS = REPO / "docs" / "inbox-runbooks.md"
MORNING_ASSEMBLY = REPO / "docs" / "morning-assembly.md"
TASK_TRANSITIONS = REPO / "docs" / "task-transitions.md"
PICKUP_SKILL = REPO / "skills" / "pickup" / "SKILL.md"
STATE_SCHEMA = REPO / "docs" / "state-schema.md"


def _read(path):
    return path.read_text()


def _assert_contains(text, needle, label):
    assert needle in text, f"{label}: expected to find {needle!r}"


def _assert_absent(text, needle, label):
    assert needle not in text, f"{label}: forbidden substring present: {needle!r}"


def test_pre_work_no_task_insertion():
    """Pre-work scan must not insert a synthetic completed task."""
    text = _read(START_SKILL)
    _assert_absent(text, 'wpl add "Morning communication work"',
                   "pre-work: synthetic task insertion")
    _assert_absent(text, "Insert a completed task at position 0",
                   "pre-work: completed-task language")
    # Headline path must still be present.
    _assert_contains(text, "Pre-work: {N} Slack replies",
                     "pre-work: headline example preserved")
    _assert_contains(text, "headline only", "pre-work: headline-only framing")


def test_carryover_has_threshold_gate():
    """Carryover surfacing splits on deferral_count vs reckoning threshold."""
    text = _read(START_SKILL)
    _assert_contains(text, "Below threshold",
                     "carryover: below-threshold case")
    _assert_contains(text, "At or above threshold",
                     "carryover: at-or-above-threshold case")
    _assert_contains(text, "surface read-only",
                     "carryover: read-only surfacing")
    _assert_contains(text, "No prompt.",
                     "carryover: explicit no-prompt-below-threshold")


def test_carryover_no_unconditional_prompt():
    """The 5-way keep/defer/drop/backlog/re-scope prompt must not fire for every carryover."""
    text = _read(START_SKILL)
    # The forbidden phrase is "Keep / defer / drop / backlog / re-scope?" appearing
    # as the prompt text in the example block for ALL carryover tasks. Since the
    # split now removes it from the below-threshold example, the 5-way chip must
    # not appear at all in the below-threshold read-only block.
    _assert_absent(text, "Keep / defer / drop / backlog / re-scope?",
                   "carryover: unconditional 5-way prompt")


def test_calendar_fallback_gated():
    """'Anything not on your calendar?' prompt must be gated on sweep failure."""
    text = _read(START_SKILL)
    _assert_contains(text, "only if the sweep failed",
                     "calendar: gate language (skill)")
    _assert_contains(text, "do not ask",
                     "calendar: do-not-ask language (skill)")


def test_calendar_fallback_gated_in_runbooks():
    """Same gate language must appear in inbox-runbooks.md."""
    text = _read(INBOX_RUNBOOKS)
    _assert_contains(text, "If the calendar sweep fails",
                     "calendar: gate language (runbooks)")
    _assert_contains(text, "If the sweep succeeded, do not ask",
                     "calendar: succeeded-do-not-ask (runbooks)")


def test_calendar_fallback_gated_in_assembly_doc():
    """Same gate language must appear in morning-assembly.md."""
    text = _read(MORNING_ASSEMBLY)
    _assert_contains(text, "fires only if the calendar sweep failed",
                     "calendar: gate language (assembly doc)")


def test_dashboard_pane_policy_referenced():
    """Dashboard Pane section must read config.dashboard_pane and branch on auto/always/never."""
    text = _read(START_SKILL)
    _assert_contains(text, "config.dashboard_pane",
                     "dashboard_pane: config field reference")
    _assert_contains(text, '"auto"', "dashboard_pane: auto value")
    _assert_contains(text, '"always"', "dashboard_pane: always value")
    _assert_contains(text, '"never"', "dashboard_pane: never value")


def test_dashboard_pane_never_skips_spawn():
    """The 'never' branch must explicitly skip pane-spawn."""
    text = _read(START_SKILL)
    # The case branch for never should not call split-window.
    # A minimal structural check: the never branch description mentions
    # skipping spawn.
    _assert_contains(text, "never spawn the pane",
                     "dashboard_pane: never branch language")


def test_subtask_feature_surfaced_in_task_transitions():
    """--parent flag must be documented in docs/task-transitions.md (issue #33)."""
    text = _read(TASK_TRANSITIONS)
    _assert_contains(text, "--parent",
                     "task-transitions: --parent flag in add table")
    _assert_contains(text, "Sub-tasks",
                     "task-transitions: Sub-tasks section heading")


def test_subtask_feature_surfaced_in_start_skill():
    """start SKILL must mention --parent in the task-transitions example (issue #33)."""
    text = _read(START_SKILL)
    _assert_contains(text, "--parent",
                     "start skill: --parent in transition example")


def test_subtask_feature_surfaced_in_morning_assembly():
    """morning-assembly.md must mention parent/child in Step 3 (issue #33)."""
    text = _read(MORNING_ASSEMBLY)
    _assert_contains(text, "Parent/child for project work",
                     "morning-assembly: parent/child section")


def test_subtask_feature_surfaced_in_pickup():
    """pickup SKILL must mention parent/children handling (issue #33)."""
    text = _read(PICKUP_SKILL)
    _assert_contains(text, "parent",
                     "pickup skill: parent task handling")


def test_methodology_pointer_in_session_hook():
    """SessionStart hook must emit the methodology pointer with an
    apply-not-just-consult directive (issue #35).

    The hook is the only channel that reliably reaches cold-start Claude
    sessions tied to a workplanner profile. Discoverability alone proved
    insufficient — a real-session test showed the agent read
    methodology.md but reasoned in generic PM vocabulary anyway. The
    directive was strengthened from "consult" to "apply + cite at least
    one principle by name."
    """
    hook_path = REPO / "bin" / "session-hook.sh"
    text = _read(hook_path)
    _assert_contains(text, "methodology.md",
                     "session-hook: methodology.md pointer")
    _assert_contains(text, "work-shape questions",
                     "session-hook: work-shape trigger phrase")
    _assert_contains(text, "apply its seven principles by name",
                     "session-hook: apply-not-just-consult directive")
    _assert_contains(text, "Cite at least one principle",
                     "session-hook: cite-principle instruction")
    _assert_contains(text, "if you deviate from a principle, name it",
                     "session-hook: deviation-must-be-named rule")


def test_pre_work_headline_only_in_assembly_doc():
    """#62: morning-assembly.md must not contradict the skill's headline-only rule."""
    text = _read(MORNING_ASSEMBLY)
    _assert_absent(text, 'inserts a completed "Morning communication work" task',
                   "assembly doc: synthetic pre-work task")
    _assert_absent(text, "captured as a completed task", "assembly doc: pre-work as task")
    _assert_contains(text, "headline", "assembly doc: pre-work headline")


def test_closed_session_has_archive_step():
    """#62: a closed session's route names the archive steps explicitly."""
    text = _read(START_SKILL)
    _assert_contains(text, "## Closed Session Handler", "start: closed-session handler")
    section = text.split("## Closed Session Handler", 1)[1].split("\n## ", 1)[0]
    _assert_contains(section, "archive", "start: closed handler archives the session")


def test_stale_recovery_write_uses_replace():
    """#59: stale-recovery handoff write is a full rewrite."""
    _assert_contains(_read(START_SKILL), "--replace", "start: stale recovery --replace")


def test_state_schema_task_fields():
    """#62: the task table lists the fields live tasks carry."""
    text = _read(STATE_SCHEMA).split("### Task object", 1)[1].split("\n### ", 1)[0]
    for field in ("| `uid` |", "| `priority` |", "| `drop_reason` |", "| `dispatched` |"):
        _assert_contains(text, field, f"schema: {field}")
    _assert_contains(text, "`dropped`", "schema: dropped status")


def test_task_transitions_document_drop_and_edit():
    """#60/#61: drop and edit are documented."""
    text = _read(TASK_TRANSITIONS)
    for needle in ("### drop", "### edit", "dropped"):
        _assert_contains(text, needle, f"transitions: {needle}")


def test_archive_step_never_overwrites():
    """Review fix: the shared archive step names the no-overwrite rule."""
    text = _read(START_SKILL)
    step5 = [l for l in text.splitlines() if l.startswith("5. Archive")][0]
    _assert_contains(step5, "-N.json", "start: archive step no-overwrite rule")


def test_closed_handler_sweep_not_in_future():
    """Review fix: a same-day restart must not sweep from a future time."""
    section = _read(START_SKILL).split("## Closed Session Handler", 1)[1].split("\n## ", 1)[0]
    _assert_contains(section, "sweep_since", "start: closed handler sweep_since rule")
    _assert_contains(section, "now", "start: sweep_since capped at now")


def test_stale_recovery_mentions_dropped():
    """Review fix: stale-recovery trajectory includes dropped tasks."""
    text = _read(START_SKILL)
    _assert_contains(text, "done / dropped / deferred / blocked", "start: stale trajectory covers dropped")


def test_task_transitions_lists_new_commands():
    """Review fix: --as, explicit-target and pointer rules include drop/edit."""
    text = _read(TASK_TRANSITIONS)
    _assert_contains(text, "(done, blocked, deferred, dropped, removed)", "transitions: pointer rule")
    _assert_contains(text, "`done`, `drop`, `edit`, `rename` and `remove` accept", "transitions: --as list")
    _assert_contains(text, "`done`, `drop`, `blocked`, `defer` and `reckon` take", "transitions: target list")


def test_handoff_write_semantics_documented():
    """Review fix: handoff docs describe omit-means-keep and --replace."""
    for path in (STATE_SCHEMA, REPO / "bin" / "handoff.py"):
        _assert_contains(_read(path), "--replace", f"{path.name}: --replace documented")


def main():
    tests = [
        test_pre_work_no_task_insertion,
        test_pre_work_headline_only_in_assembly_doc,
        test_closed_session_has_archive_step,
        test_stale_recovery_write_uses_replace,
        test_state_schema_task_fields,
        test_task_transitions_document_drop_and_edit,
        test_archive_step_never_overwrites,
        test_closed_handler_sweep_not_in_future,
        test_stale_recovery_mentions_dropped,
        test_task_transitions_lists_new_commands,
        test_handoff_write_semantics_documented,
        test_carryover_has_threshold_gate,
        test_carryover_no_unconditional_prompt,
        test_calendar_fallback_gated,
        test_calendar_fallback_gated_in_runbooks,
        test_calendar_fallback_gated_in_assembly_doc,
        test_dashboard_pane_policy_referenced,
        test_dashboard_pane_never_skips_spawn,
        test_subtask_feature_surfaced_in_task_transitions,
        test_subtask_feature_surfaced_in_start_skill,
        test_subtask_feature_surfaced_in_morning_assembly,
        test_subtask_feature_surfaced_in_pickup,
        test_methodology_pointer_in_session_hook,
    ]
    failures = 0
    for t in tests:
        try:
            t()
            print(f"  ok  {t.__name__}")
        except AssertionError as e:
            failures += 1
            print(f"FAIL  {t.__name__}: {e}")
    if failures:
        print(f"\n{failures} failure(s)")
        sys.exit(1)
    print(f"\n{len(tests)} tests passed")


if __name__ == "__main__":
    main()
