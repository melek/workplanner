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


def main():
    tests = [
        test_pre_work_no_task_insertion,
        test_carryover_has_threshold_gate,
        test_carryover_no_unconditional_prompt,
        test_calendar_fallback_gated,
        test_calendar_fallback_gated_in_runbooks,
        test_calendar_fallback_gated_in_assembly_doc,
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
