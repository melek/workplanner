#!/usr/bin/env python3
"""Regression suite for issue #70 — backlog items with only `not_before` come back.

An item's surfacing date is `target_date`, or `not_before` when there's no
target. `backlog --list` shows it and flags only undated items as stale.
(The `/start` sweep and `/horizon` grouping are skill prose; the shared rule
lives in `backlog_surface_date()`, tested here.)

Run: `python3 bin/test_issue_70.py`
"""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_issue_60_61 import Ctx, _write_session  # noqa: E402
import transition  # noqa: E402

TODAY = datetime.now(timezone.utc).date()


def d(days):
    return (TODAY + timedelta(days=days)).isoformat()


def test_surface_date_rule():
    f = transition.backlog_surface_date
    assert f({"target_date": d(3), "not_before": d(5)}) == d(3)
    assert f({"not_before": d(5)}) == d(5)
    assert f({"deadline": d(1)}) is None
    assert f({}) is None


def test_add_with_only_not_before_says_when():
    with Ctx() as c:
        _write_session(c.profile_dir, [])
        out = c.ok("backlog", "Follow up after the freeze", "--not-before", d(10))
        assert f"not before: {d(10)}, surfaces then" in out, out
        item = json.loads((c.profile_dir / "backlog.json").read_text())["items"][0]
        assert item["not_before"] == d(10) and item["target_date"] is None


def test_list_shows_not_before_and_skips_stale_for_dated():
    with Ctx() as c:
        _write_session(c.profile_dir, [])
        old = d(-20)
        items = [
            {"uid": "aaaa0001", "title": "Waiting on purpose", "estimate_min": 15,
             "created_at": old, "target_date": None, "not_before": d(5), "deadline": None},
            {"uid": "bbbb0002", "title": "Truly undated", "estimate_min": 15,
             "created_at": old, "target_date": None, "not_before": None, "deadline": None},
        ]
        (c.profile_dir / "backlog.json").write_text(json.dumps({"schema_version": 1, "items": items}))
        out = c.ok("backlog", "--list")
        waiting = [l for l in out.splitlines() if "Waiting on purpose" in l][0]
        undated = [l for l in out.splitlines() if "Truly undated" in l][0]
        assert f"not before: {d(5)}, surfaces then" in waiting, waiting
        assert "stale" not in waiting, waiting
        assert "stale" in undated, undated


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"ok  {t.__name__}")
    print(f"{len(tests)} passed")
