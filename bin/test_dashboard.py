#!/usr/bin/env python3
"""Regression suite for dashboard rendering — issues #2, #48, #50.

#50: the summary said "~90m left" next to an EOD clock time, which reads
as wall-clock time; it is the summed estimate of unfinished tasks, now
labelled "Budget". #48: a closed session kept rendering a live EOD
target, buffer and alerts. #2: protected-block separators were placed by
projecting estimates from "now", so a block that had already ended kept
moving; and two blocks landing on the same row overwrote each other.

Run: `python3 bin/test_dashboard.py`
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
RENDER = REPO / "bin" / "render_dashboard.py"
TRANSITION = REPO / "bin" / "transition.py"
ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _bootstrap(base, config):
    wpl_root = Path(base) / "wpl"
    profile_dir = wpl_root / "profiles" / "test"
    (profile_dir / "session").mkdir(parents=True)
    (profile_dir / "config.json").write_text(json.dumps(
        {"workspaces": [], "timezone": "UTC", **config}))
    (profile_dir / "backlog.json").write_text('{"schema_version": 1, "items": []}\n')
    return wpl_root, profile_dir


def _write_session(profile_dir, tasks, **extra):
    body = {
        "date": date.today().isoformat(),
        "week": "W43",
        "checkpoint": "assembly_complete",
        "eod_target": "23:59",
        "current_task_index": None,
        "tasks": tasks,
        "inbox_items": [],
        "headlines": [],
    }
    body.update(extra)
    (profile_dir / "session" / "current-session.json").write_text(json.dumps(body))


def _render(base, wpl_root):
    env = {**os.environ, "WPL_ROOT": str(wpl_root), "WPL_PROFILE": "test",
           "WPL_CHILD": "1", "COLUMNS": "60"}
    proc = subprocess.run([sys.executable, str(RENDER)], cwd=str(base), env=env,
                          capture_output=True, text=True, check=True)
    return ANSI.sub("", proc.stdout)


def _task(uid, title, status="pending", **extra):
    t = {"uid": uid, "title": title, "status": status,
         "estimate_min": 30, "source": "manual"}
    t.update(extra)
    return t


def main():
    now = datetime.now()
    if now.hour == 23 and now.minute >= 50 or now.hour == 0 and now.minute < 10:
        print("Skipping: clock-relative scenarios are unreliable around midnight.")
        return 0
    base = tempfile.mkdtemp(prefix="wpl-dashboard-")
    try:
        # A block that ended a few minutes ago, and two upcoming blocks
        # that both land after every task.
        past_start = (now - timedelta(minutes=8)).strftime("%H:%M")
        past_end = (now - timedelta(minutes=5)).strftime("%H:%M")
        after_past = (now - timedelta(minutes=3)).strftime("%H:%M")
        wpl_root, profile_dir = _bootstrap(base, {"protected_blocks": [
            {"label": "Coffee", "start": past_start, "end": past_end},
            {"label": "Late A", "start": "23:57", "end": "23:58"},
            {"label": "Late B", "start": "23:58", "end": "23:59"},
        ]})

        # ── D1 (#50): summary says Budget, not "left" ──
        _write_session(profile_dir, [
            _task("aaaa0001", "Early", status="done", started_at="00:01",
                  finished_at="00:20", actual_min=19),
            _task("bbbb0002", "After break", status="done",
                  started_at=after_past, finished_at=after_past, actual_min=1),
            _task("cccc0003", "Later"),
            _task("dddd0004", "Even later"),
        ])
        out = _render(base, wpl_root)
        assert "Budget: ~1h" in out, f"D1: Budget label missing:\n{out}"
        assert " left" not in out, f"D1: ambiguous 'left' still rendered:\n{out}"
        env = {**os.environ, "WPL_ROOT": str(wpl_root), "WPL_CHILD": "1"}
        status = subprocess.run([sys.executable, str(TRANSITION), "status"], cwd=str(base),
                                env=env, capture_output=True, text=True).stdout
        assert "Budget: ~1h" in status and " left" not in status, \
            f"D1: CLI status line not relabelled:\n{status}"
        print("D1: Budget label in dashboard and status line [OK]")

        # ── D2 (#2): an ended block sits where it happened — before the
        #    finished task that started after it, not in front of the
        #    remaining work, where projecting from "now" used to put it ──
        rows = [l.strip() for l in out.splitlines()]
        coffee = next(i for i, l in enumerate(rows) if "Coffee" in l)
        early = next(i for i, l in enumerate(rows) if "Early" in l)
        after = next(i for i, l in enumerate(rows) if "After break" in l)
        assert early < coffee < after, f"D2: ended block misplaced:\n{out}"
        print("D2: ended block anchored by recorded start times [OK]")

        # ── D3 (#2): two blocks at the same position both render, in order ──
        late_a = next((i for i, l in enumerate(rows) if "Late A" in l), None)
        late_b = next((i for i, l in enumerate(rows) if "Late B" in l), None)
        assert late_a is not None and late_b is not None and late_a < late_b, \
            f"D3: co-located blocks lost or out of order:\n{out}"
        print("D3: co-located blocks all render [OK]")

        # ── D4 (#48): a closed session shows carry-over, not a live day ──
        _write_session(profile_dir, [
            _task("aaaa0001", "Shipped", status="done", actual_min=20),
            _task("bbbb0002", "Next time", status="deferred"),
        ], checkpoint="closed", eod_handoff_written=True,
           date=(date.today() - timedelta(days=2)).isoformat())
        out = _render(base, wpl_root)
        assert "CLOSED" in out, f"D4: closed marker missing:\n{out}"
        assert "Carrying over: 1" in out, f"D4: carry-over count missing:\n{out}"
        assert "Session closed 2 days ago" in out, f"D4: age missing:\n{out}"
        assert "Buffer" not in out and "EOD:" not in out, \
            f"D4: live-day fields still shown:\n{out}"
        print("D4: closed session renders as between-days [OK]")

        print("\nAll D1-D4 scenarios passed.")
        return 0
    finally:
        shutil.rmtree(base, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
