#!/usr/bin/env python3
"""Regression suite for issue #54 — explicit targets and multi-agent safety.

`done`, `blocked`, `defer` and `reckon` used to act only on the shared
`current_task_index`, so finishing a side task took switch → done →
switch, and two agents moving the pointer closed each other's tasks.
They now take an optional explicit target that leaves the pointer
alone. Mutations hold an exclusive per-profile lock across load → save,
so concurrent writers no longer lose updates, and every mutation is
appended to `mutations.jsonl` so contention can be diagnosed later.

Run: `python3 bin/test_issue_54.py`
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TRANSITION = REPO / "bin" / "transition.py"


def _cmd(*args):
    return [sys.executable, str(TRANSITION), *args]


def _run(cwd, env, *args, check_exit=None):
    """Run transition.py with the given args. Returns (rc, stdout, stderr)."""
    proc = subprocess.run(_cmd(*args), cwd=str(cwd), env={**os.environ, **env},
                          capture_output=True, text=True)
    if check_exit is not None and proc.returncode != check_exit:
        raise AssertionError(
            f"expected exit {check_exit}, got {proc.returncode}\n"
            f"args: {args}\nstdout: {proc.stdout}\nstderr: {proc.stderr}"
        )
    return proc.returncode, proc.stdout, proc.stderr


def _bootstrap(base):
    wpl_root = Path(base) / "wpl"
    profile_dir = wpl_root / "profiles" / "test"
    (profile_dir / "session").mkdir(parents=True)
    (profile_dir / "config.json").write_text('{"workspaces": [], "timezone": "UTC"}\n')
    (profile_dir / "backlog.json").write_text('{"schema_version": 1, "items": []}\n')
    return wpl_root, profile_dir


def _write_session(profile_dir, tasks, current_index=None):
    body = {
        "date": datetime.now(timezone.utc).date().isoformat(),
        "week": "W43",
        "checkpoint": "assembly_complete",
        "eod_target": "18:00",
        "current_task_index": current_index,
        "tasks": tasks,
        "inbox_items": [],
        "headlines": [],
    }
    (profile_dir / "session" / "current-session.json").write_text(
        json.dumps(body, indent=2) + "\n")


def _session(profile_dir):
    return json.loads(
        (profile_dir / "session" / "current-session.json").read_text())


def _task(uid, title, status="pending", **extra):
    t = {"uid": uid, "title": title, "status": status,
         "estimate_min": 15, "source": "manual"}
    t.update(extra)
    return t


def _three(current_status="in_progress"):
    """t1 in progress and current; t2, t3 pending."""
    return [
        _task("aaaa0001", "One", status=current_status, started_at="09:00"),
        _task("bbbb0002", "Two"),
        _task("cccc0003", "Three"),
    ]


def main():
    base = tempfile.mkdtemp(prefix="wpl-issue54-")
    try:
        wpl_root, profile_dir = _bootstrap(base)
        env = {"WPL_ROOT": str(wpl_root), "WPL_CHILD": "1"}
        ws = Path(base)

        # ── V1: done tN completes a side task without touching the pointer ──
        _write_session(profile_dir, _three(), current_index=0)
        _run(ws, env, "done", "t3", check_exit=0)
        s = _session(profile_dir)
        assert s["tasks"][2]["status"] == "done", f"V1: t3 not done: {s['tasks'][2]}"
        assert s["tasks"][0]["status"] == "in_progress", f"V1: t1 disturbed: {s['tasks'][0]}"
        assert s["current_task_index"] == 0, f"V1: pointer moved: {s['current_task_index']}"
        print("V1: done tN leaves the current task alone [OK]")

        # ── V2: uid targets work too ──
        _run(ws, env, "done", "bbbb0002", check_exit=0)
        s = _session(profile_dir)
        assert s["tasks"][1]["status"] == "done", f"V2: uid target failed: {s['tasks'][1]}"
        assert s["current_task_index"] == 0
        print("V2: done <uid> works [OK]")

        # ── V3: blocked tN <reason> ──
        _write_session(profile_dir, _three(), current_index=0)
        _run(ws, env, "blocked", "t2", "waiting", "on", "review", check_exit=0)
        s = _session(profile_dir)
        assert s["tasks"][1]["status"] == "blocked", f"V3: {s['tasks'][1]}"
        assert "waiting on review" in s["tasks"][1].get("notes", ""), f"V3: {s['tasks'][1]}"
        assert s["current_task_index"] == 0, "V3: pointer moved"
        print("V3: blocked tN <reason> [OK]")

        # ── V4: blocked without a target still treats every word as reason ──
        _run(ws, env, "blocked", "on", "3", "tests", check_exit=0)
        s = _session(profile_dir)
        assert s["tasks"][0]["status"] == "blocked"
        assert "on 3 tests" in s["tasks"][0].get("notes", ""), f"V4: {s['tasks'][0]}"
        print("V4: blocked <reason> on the current task unchanged [OK]")

        # ── V5: defer tN --reason ──
        _write_session(profile_dir, _three(), current_index=0)
        _run(ws, env, "defer", "t2", "--reason", "needs input", check_exit=0)
        s = _session(profile_dir)
        assert s["tasks"][1]["status"] == "deferred", f"V5: {s['tasks'][1]}"
        assert s["tasks"][1].get("defer_reason") == "needs input"
        assert s["current_task_index"] == 0, "V5: pointer moved"
        print("V5: defer tN [OK]")

        # ── V6: finishing the current task falls back to the one other
        #        in-progress task instead of leaving the pointer empty ──
        tasks = _three()
        tasks[2]["status"] = "in_progress"
        _write_session(profile_dir, tasks, current_index=0)
        _run(ws, env, "done", check_exit=0)
        s = _session(profile_dir)
        assert s["current_task_index"] == 2, \
            f"V6: pointer should fall back to t3, got {s['current_task_index']}"
        print("V6: pointer falls back to the remaining in-progress task [OK]")

        # ── V7: no current task and no target → error names the target form ──
        _write_session(profile_dir, _three(current_status="pending"))
        rc, _, err = _run(ws, env, "done")
        assert rc == 1 and "wpl done t" in err, f"V7: rc={rc} err={err!r}"
        print("V7: missing target error suggests `wpl done tN` [OK]")

        # ── V8: --as guards the explicit target ──
        _write_session(profile_dir, _three(), current_index=0)
        rc, _, err = _run(ws, env, "done", "t2", "--as", "Three")
        assert rc == 1 and "does not match" in err, f"V8: rc={rc} err={err!r}"
        assert _session(profile_dir)["tasks"][1]["status"] == "pending"
        print("V8: --as mismatch on explicit target refuses [OK]")

        # ── V9: reckon <choice> tN ──
        tasks = _three()
        tasks[1]["deferral_count"] = 3
        _write_session(profile_dir, tasks, current_index=0)
        _run(ws, env, "reckon", "x", "t2", check_exit=0)
        titles = [t["title"] for t in _session(profile_dir)["tasks"]]
        assert titles == ["One", "Three"], f"V9: {titles}"
        assert _session(profile_dir)["current_task_index"] == 0
        print("V9: reckon x tN drops the named task [OK]")

        # ── V10: concurrent writers don't lose updates ──
        _write_session(profile_dir, [])
        n = 12
        procs = [
            subprocess.Popen(_cmd("add", f"Parallel {i}", "--est", "5"),
                             cwd=str(ws), env={**os.environ, **env},
                             stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                             text=True)
            for i in range(n)
        ]
        errs = [p.communicate()[1] for p in procs]
        assert all(p.returncode == 0 for p in procs), f"V10: failures: {errs}"
        got = sorted(t["title"] for t in _session(profile_dir)["tasks"])
        assert len(got) == n, f"V10: lost updates — {len(got)}/{n} tasks: {got}"
        print(f"V10: {n} concurrent adds, none lost [OK]")

        # ── V11: mutations are logged ──
        log = profile_dir / "mutations.jsonl"
        assert log.exists(), "V11: mutations.jsonl missing"
        entries = [json.loads(l) for l in log.read_text().splitlines()]
        adds = [e for e in entries if e["action"] == "add"]
        assert len(adds) >= n, f"V11: expected ≥{n} add entries, got {len(adds)}"
        e = adds[-1]
        for key in ("ts", "action", "argv", "pid", "affected"):
            assert key in e, f"V11: missing {key} in {e}"
        assert e["affected"][0]["uid"], f"V11: affected uid missing: {e}"
        print("V11: mutations.jsonl records each change [OK]")

        print("\nAll V1-V11 scenarios passed.")
        return 0
    finally:
        shutil.rmtree(base, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
