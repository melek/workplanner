#!/usr/bin/env python3
"""Regression suite for issue #67: `wpl edit` sets a task's times.

`done` records wall-clock time since `started_at`, so a task left in
progress across a break records the whole span; a task added with
`add --done` and no times gets its estimate as its actual time. Neither
could be corrected without `remove` + `add`, which changes the uid.
`wpl edit --started/--finished/--actual` fixes them in place.

Run: `python3 bin/test_issue_67.py`
"""
import sys

from test_issue_60_61 import Ctx, _task, _write_session


def _done(**extra):
    fields = {"status": "done", "started_at": "09:16", "finished_at": "11:44", "actual_min": 148}
    fields.update(extra)
    return _task("aaaa0001", "A", **fields)


def test_actual_overrides_span():
    with Ctx() as c:
        _write_session(c.profile_dir, [_done()])
        out = c.ok("edit", "t1", "--actual", "30")
        t = c.tasks()[0]
        assert t["actual_min"] == 30, t
        assert (t["started_at"], t["finished_at"]) == ("09:16", "11:44"), t
        assert t["uid"] == "aaaa0001"
        assert "actual" in out, out


def test_finished_recomputes_actual():
    with Ctx() as c:
        _write_session(c.profile_dir, [_done()])
        c.ok("edit", "t1", "--finished", "09:46")
        t = c.tasks()[0]
        assert t["finished_at"] == "09:46" and t["actual_min"] == 30, t


def test_started_recomputes_actual():
    with Ctx() as c:
        _write_session(c.profile_dir, [_done()])
        c.ok("edit", "t1", "--started", "11:00")
        t = c.tasks()[0]
        assert t["started_at"] == "11:00" and t["actual_min"] == 44, t


def test_both_times_and_actual():
    """--actual wins over the span when all three are given."""
    with Ctx() as c:
        _write_session(c.profile_dir, [_done()])
        c.ok("edit", "t1", "--started", "08:00", "--finished", "09:00", "--actual", "45")
        t = c.tasks()[0]
        assert (t["started_at"], t["finished_at"], t["actual_min"]) == ("08:00", "09:00", 45), t


def test_iso_started_at_is_read():
    """started_at written by a skill as ISO 8601 still feeds the recompute."""
    with Ctx() as c:
        _write_session(c.profile_dir, [_done(started_at="2026-01-05T09:16:00-08:00")])
        c.ok("edit", "t1", "--finished", "09:46")
        assert c.tasks()[0]["actual_min"] == 30, c.tasks()[0]


def test_started_on_in_progress():
    with Ctx() as c:
        _write_session(c.profile_dir,
                       [_task("aaaa0001", "A", status="in_progress", started_at="10:00")],
                       current_index=0)
        c.ok("edit", "t1", "--started", "09:30")
        t = c.tasks()[0]
        assert t["started_at"] == "09:30" and "actual_min" not in t, t


def test_finished_refused_on_open_task():
    with Ctx() as c:
        _write_session(c.profile_dir,
                       [_task("aaaa0001", "A", status="in_progress", started_at="10:00")],
                       current_index=0)
        for flag, val in (("--finished", "11:00"), ("--actual", "20")):
            rc, out, err = c.run("edit", "t1", flag, val)
            assert rc != 0, (flag, out, err)
        assert "finished_at" not in c.tasks()[0]


def test_pending_refused():
    with Ctx() as c:
        _write_session(c.profile_dir, [_task("aaaa0001", "A")])
        rc, out, err = c.run("edit", "t1", "--started", "09:00")
        assert rc != 0, (out, err)
        assert "started_at" not in c.tasks()[0]


def test_finish_before_start_refused():
    with Ctx() as c:
        _write_session(c.profile_dir, [_done()])
        rc, out, err = c.run("edit", "t1", "--finished", "09:00")
        assert rc != 0, (out, err)
        assert c.tasks()[0]["actual_min"] == 148


def test_bad_time_format_refused():
    with Ctx() as c:
        _write_session(c.profile_dir, [_done()])
        for bad in ("9.30", "25:00", "09:7", "noon"):
            rc, out, err = c.run("edit", "t1", "--started", bad)
            assert rc != 0, (bad, out, err)
        assert c.tasks()[0]["started_at"] == "09:16"


def test_negative_actual_refused():
    with Ctx() as c:
        _write_session(c.profile_dir, [_done()])
        rc, out, err = c.run("edit", "t1", "--actual", "-5")
        assert rc != 0, (out, err)


def test_dropped_task():
    with Ctx() as c:
        _write_session(c.profile_dir, [_done(status="dropped", drop_reason="r")])
        c.ok("edit", "t1", "--actual", "10")
        assert c.tasks()[0]["actual_min"] == 10


def test_edit_times_then_undo():
    with Ctx() as c:
        before = [_done()]
        _write_session(c.profile_dir, before)
        c.ok("edit", "t1", "--finished", "09:46")
        c.ok("undo")
        assert c.tasks() == before, c.tasks()


def test_as_mismatch_leaves_times():
    with Ctx() as c:
        _write_session(c.profile_dir, [_done()])
        rc, out, err = c.run("edit", "t1", "--actual", "30", "--as", "Not A")
        assert rc != 0, (out, err)
        assert c.tasks()[0]["actual_min"] == 148


TESTS = [
    test_actual_overrides_span,
    test_finished_recomputes_actual,
    test_started_recomputes_actual,
    test_both_times_and_actual,
    test_iso_started_at_is_read,
    test_started_on_in_progress,
    test_finished_refused_on_open_task,
    test_pending_refused,
    test_finish_before_start_refused,
    test_bad_time_format_refused,
    test_negative_actual_refused,
    test_dropped_task,
    test_edit_times_then_undo,
    test_as_mismatch_leaves_times,
]


def main():
    failed = 0
    for t in TESTS:
        try:
            t()
            print(f"ok   {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL {t.__name__}: {e}")
    print(f"{len(TESTS) - failed}/{len(TESTS)} passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
