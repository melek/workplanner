#!/usr/bin/env python3
"""Verification for issue #23 (private-by-default EOD).

Asserts the structural invariants of the EOD and stale-session skill
prose so a future edit can't re-introduce the "always prompt to post"
behavior. The tests are content-level (grep-style) because EOD is
skill prose, not engine code — there's no transition.py surface to
behavior-test directly.

Exit 0 on success, 1 on any failed assertion.

Usage:
    python3 bin/test_eod_flow.py
"""

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
EOD_SKILL = REPO / "skills" / "eod" / "SKILL.md"
START_SKILL = REPO / "skills" / "start" / "SKILL.md"
EOD_DOC = REPO / "docs" / "eod-consolidation.md"


def _read(path):
    return path.read_text()


def _assert_contains(text, needle, label):
    assert needle in text, f"{label}: expected to find {needle!r}"


def _assert_absent(text, needle, label):
    assert needle not in text, f"{label}: forbidden substring present: {needle!r}"


def _assert_order(text, before, after, label):
    """Assert `before` appears before `after` in text (both must be present)."""
    i = text.find(before)
    j = text.find(after)
    assert i >= 0, f"{label}: {before!r} not found"
    assert j >= 0, f"{label}: {after!r} not found"
    assert i < j, f"{label}: {before!r} should precede {after!r} but did not"


def test_eod_skill_step_order():
    """Step 2 is the local handoff; external drafts come after."""
    text = _read(EOD_SKILL)
    _assert_contains(text, "### Step 2: Write local handoff doc", "eod step 2 heading")
    _assert_contains(text, "### Step 3: Display optional external drafts",
                     "eod step 3 heading")
    _assert_contains(text, "### Step 4: Close session", "eod step 4 heading")
    _assert_order(text, "Step 2: Write local handoff",
                  "Step 3: Display optional external drafts",
                  "handoff before external drafts")
    _assert_order(text, "Step 3: Display optional external drafts",
                  "Step 4: Close session",
                  "drafts before close")


def test_eod_skill_no_posting_prompt():
    """The Post / Edit first / Skip interactive branch must not return."""
    text = _read(EOD_SKILL)
    _assert_absent(text, "Post it", "eod skill: Post it option")
    _assert_absent(text, "Edit first", "eod skill: Edit first option")
    _assert_absent(text,
                   'Post this to your weekly check-in sub-issue?',
                   "eod skill: Post-this prompt")
    _assert_absent(text, "save_comment",
                   "eod skill: save_comment call")


def test_eod_skill_close_gate():
    """Step 4 is gated on Step 2 success."""
    text = _read(EOD_SKILL)
    _assert_contains(text, "Session-close gate", "eod close gate section")
    _assert_contains(text, "only fires if", "close-fires-if language")


def test_eod_skill_conditional_drafts():
    """External drafts are conditional on config."""
    text = _read(EOD_SKILL)
    _assert_contains(text, "Only if `personal_sub_issue` is set",
                     "check-in conditional on sub-issue")
    _assert_contains(text, "Only if `config.coordination_channel` is set",
                     "messaging conditional on channel")


def test_start_skill_no_retroactive_post_prompt():
    """Stale-session handler must not offer a retroactive external post."""
    text = _read(START_SKILL)
    _assert_absent(text, "Post yesterday's update",
                   "start skill: retroactive post prompt")
    _assert_absent(text, "[Post / Skip]",
                   "start skill: Post/Skip option chip")
    _assert_contains(text, "No retroactive external posting",
                     "start skill: retroactive-post negation present")


def test_eod_doc_step_order():
    """docs/eod-consolidation.md mirrors the skill's new order."""
    text = _read(EOD_DOC)
    _assert_contains(text, "## Step 2: Local Handoff Doc (mandatory)",
                     "doc step 2 heading")
    _assert_contains(text, "## Step 3: Display Optional External Drafts",
                     "doc step 3 heading")
    _assert_contains(text, "## Step 4: Carryover & Close",
                     "doc step 4 heading")
    _assert_order(text, "Step 2: Local Handoff Doc",
                  "Step 3: Display Optional External Drafts",
                  "doc: handoff before drafts")


def test_eod_doc_no_auto_post():
    """The procedure doc must not describe auto-posting."""
    text = _read(EOD_DOC)
    _assert_absent(text, "On confirmation, post via",
                   "doc: auto-post language")
    _assert_absent(text, "Post this?",
                   "doc: Post-this prompt")
    _assert_contains(text, "display-only", "doc: display-only posture")


def main():
    tests = [
        test_eod_skill_step_order,
        test_eod_skill_no_posting_prompt,
        test_eod_skill_close_gate,
        test_eod_skill_conditional_drafts,
        test_start_skill_no_retroactive_post_prompt,
        test_eod_doc_step_order,
        test_eod_doc_no_auto_post,
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
