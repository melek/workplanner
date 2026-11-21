#!/usr/bin/env python3
"""Regression suite for issue #74 — handoffs are read by the next session, however long the gap.

Morning assembly used to read only the handoff dated yesterday, so the first
session after a weekend or time off never saw the last one. `handoff.py
pending` returns every unreviewed handoff before today; `mark-reviewed`
records how far review has got.

Run: `python3 bin/test_issue_74.py`
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
HANDOFF = REPO / "bin" / "handoff.py"
TODAY = datetime.now(timezone.utc).date()  # the test profile runs in UTC


def d(days_ago):
    return (TODAY - timedelta(days=days_ago)).isoformat()


def _fresh():
    base = tempfile.mkdtemp(prefix="wpl-issue74-")
    wpl_root = Path(base) / "wpl"
    profile_dir = wpl_root / "profiles" / "test"
    (profile_dir / "session").mkdir(parents=True)
    (profile_dir / "config.json").write_text('{"workspaces": [], "timezone": "UTC"}\n')
    (profile_dir / "backlog.json").write_text('{"schema_version": 1, "items": []}\n')
    env = {"WPL_ROOT": str(wpl_root), "WPL_CHILD": "1"}
    return base, env, profile_dir / "handoffs"


def _run(base, env, *args):
    proc = subprocess.run([sys.executable, str(HANDOFF), *args], cwd=str(base),
                          env={**os.environ, **env}, capture_output=True, text=True)
    if proc.returncode != 0:
        raise AssertionError(f"{args} failed:\n{proc.stdout}\n{proc.stderr}")
    return proc.stdout


def _write(base, env, day, ctx):
    _run(base, env, "write", "--date", day, "--session-id", "s1", "--context", ctx)


def _pending(base, env):
    return json.loads(_run(base, env, "pending"))


def test_weekend_gap_reads_last_handoff():
    base, env, _ = _fresh()
    try:
        _write(base, env, d(3), "FRIDAY-CTX")
        _run(base, env, "mark-reviewed", "--through", d(4))
        out = _pending(base, env)
        assert out["count"] == 1, out
        assert "FRIDAY-CTX" in out["handoffs"][0]["raw"]
    finally:
        shutil.rmtree(base)


def test_no_record_returns_only_latest():
    base, env, _ = _fresh()
    try:
        for i, tag in ((10, "OLD"), (5, "MID"), (2, "LATEST")):
            _write(base, env, d(i), tag)
        out = _pending(base, env)
        assert out["reviewed_through"] is None
        assert [h["date"] for h in out["handoffs"]] == [d(2)], out
    finally:
        shutil.rmtree(base)


def test_multiple_unreviewed_oldest_first_and_today_excluded():
    base, env, _ = _fresh()
    try:
        for i in (6, 3, 1, 0):
            _write(base, env, d(i), f"CTX-{i}")
        _run(base, env, "mark-reviewed", "--through", d(7))
        dates = [h["date"] for h in _pending(base, env)["handoffs"]]
        assert dates == [d(6), d(3), d(1)], dates
    finally:
        shutil.rmtree(base)


def test_mark_reviewed_clears_and_never_moves_back():
    base, env, hdir = _fresh()
    try:
        _write(base, env, d(2), "X")
        assert _run(base, env, "mark-reviewed").strip() == d(1)
        assert _pending(base, env)["count"] == 0
        assert _run(base, env, "mark-reviewed", "--through", d(5)).strip() == d(1)
        assert json.loads((hdir / ".reviewed").read_text())["reviewed_through"] == d(1)
    finally:
        shutil.rmtree(base)


def test_missing_dir_is_empty():
    base, env, _ = _fresh()
    try:
        out = _pending(base, env)
        assert out["count"] == 0 and out["handoffs"] == []
    finally:
        shutil.rmtree(base)


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"ok  {t.__name__}")
    print(f"{len(tests)} passed")
