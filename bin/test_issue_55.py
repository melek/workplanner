#!/usr/bin/env python3
"""Regression suite for issues #53, #55 and #47 — task-structure integrity.

`parent` is stored as a positional index into `tasks`, so every mutation
that reorders, inserts or removes tasks must re-derive it. #55: move,
remove and the other list mutations didn't, so sub-tasks detached from
their parent (or became their own parent). #53: `add` compared
`parent >= insert_pos` without a None check and crashed on an explicit
`parent: null`. #47: `switch --no-pause` onto a dispatched task left it
dispatched, with no CLI path to reclaim it while keeping the previous
task running.

Run: `python3 bin/test_issue_55.py`
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


def _run(cwd, env, *args, check_exit=None):
    """Run transition.py with the given args. Returns (rc, stdout, stderr)."""
    cmd = [sys.executable, str(TRANSITION), *args]
    proc = subprocess.run(cmd, cwd=str(cwd), env={**os.environ, **env},
                          capture_output=True, text=True)
    if check_exit is not None and proc.returncode != check_exit:
        raise AssertionError(
            f"expected exit {check_exit}, got {proc.returncode}\n"
            f"cmd: {cmd}\nstdout: {proc.stdout}\nstderr: {proc.stderr}"
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


def _family():
    """A, B, P, c1, c2 — c1 and c2 are children of P (index 2)."""
    return [
        _task("aaaa0001", "A"),
        _task("bbbb0002", "B"),
        _task("pppp0003", "P"),
        _task("cccc0004", "c1", parent=2),
        _task("cccc0005", "c2", parent=2),
    ]


def _assert_children_of(profile_dir, parent_title, child_titles, scenario):
    tasks = _session(profile_dir)["tasks"]
    titles = [t["title"] for t in tasks]
    p_idx = titles.index(parent_title)
    for c in child_titles:
        child = tasks[titles.index(c)]
        assert child.get("parent") == p_idx, (
            f"{scenario}: {c} should be a child of {parent_title} "
            f"(index {p_idx}), got parent={child.get('parent')!r}; "
            f"tasks={[(t['title'], t.get('parent')) for t in tasks]}"
        )


def main():
    base = tempfile.mkdtemp(prefix="wpl-issue55-")
    try:
        wpl_root, profile_dir = _bootstrap(base)
        env = {"WPL_ROOT": str(wpl_root), "WPL_CHILD": "1"}
        ws = Path(base)

        # ── V1 (#53): add inserts before a task with explicit parent: null ──
        _write_session(profile_dir, tasks=[
            _task("aaaa0001", "A", status="in_progress"),
            _task("bbbb0002", "B", parent=None),
        ], current_index=0)
        _run(ws, env, "add", "New", "--est", "15", check_exit=0)
        titles = [t["title"] for t in _session(profile_dir)["tasks"]]
        assert titles == ["A", "New", "B"], f"V1: unexpected order {titles}"
        print("V1: add tolerates parent: null [OK]")

        # ── V2 (#55): move a task from above the parent to the end ──
        _write_session(profile_dir, tasks=_family())
        _run(ws, env, "move", "t1", "--to", "end", check_exit=0)
        _assert_children_of(profile_dir, "P", ["c1", "c2"], "V2")
        print("V2: move above parent keeps children attached [OK]")

        # ── V3: move the parent itself ──
        _write_session(profile_dir, tasks=_family())
        _run(ws, env, "move", "t3", "--to", "top", check_exit=0)
        _assert_children_of(profile_dir, "P", ["c1", "c2"], "V3")
        print("V3: moving the parent keeps children attached [OK]")

        # ── V4 (#55): remove a task above the parent ──
        _write_session(profile_dir, tasks=_family())
        _run(ws, env, "remove", "t1", check_exit=0)
        _assert_children_of(profile_dir, "P", ["c1", "c2"], "V4")
        print("V4: remove above parent keeps children attached [OK]")

        # ── V5: removing the parent promotes its children to top level ──
        _write_session(profile_dir, tasks=_family())
        _run(ws, env, "remove", "t3", check_exit=0)
        tasks = _session(profile_dir)["tasks"]
        orphans = [t for t in tasks if t["title"] in ("c1", "c2")]
        assert all("parent" not in t for t in orphans), \
            f"V5: children of a removed parent should be top-level: {orphans}"
        print("V5: removing the parent detaches children cleanly [OK]")

        # ── V6: backlog --from a task above the parent ──
        _write_session(profile_dir, tasks=_family())
        _run(ws, env, "backlog", "--from", "t2", check_exit=0)
        _assert_children_of(profile_dir, "P", ["c1", "c2"], "V6")
        print("V6: backlog --from above parent keeps children attached [OK]")

        # ── V7: backlog --promote inserts above the parent ──
        _write_session(profile_dir, tasks=_family(), current_index=0)
        (profile_dir / "backlog.json").write_text(json.dumps({
            "schema_version": 1,
            "items": [{"uid": "bbbbbbb9", "title": "Promoted",
                       "estimate_min": 15, "source": "manual",
                       "status": "backlog"}],
        }))
        _run(ws, env, "backlog", "--promote", "bbbbbbb9", check_exit=0)
        _assert_children_of(profile_dir, "P", ["c1", "c2"], "V7")
        print("V7: backlog --promote above parent keeps children attached [OK]")

        # ── V8: reckon drop of a task above the parent ──
        fam = _family()
        fam[0]["status"] = "in_progress"
        fam[0]["deferral_count"] = 3
        _write_session(profile_dir, tasks=fam, current_index=0)
        _run(ws, env, "reckon", "x", check_exit=0)
        _assert_children_of(profile_dir, "P", ["c1", "c2"], "V8")
        print("V8: reckon drop above parent keeps children attached [OK]")

        # ── V9 (#47): switch --no-pause onto a dispatched task reclaims it ──
        _write_session(profile_dir, tasks=[
            _task("aaaa0001", "A", status="in_progress", dispatched=True,
                  started_at="09:00"),
            _task("bbbb0002", "B", status="in_progress", started_at="09:30"),
        ], current_index=1)
        _run(ws, env, "switch", "t1", "--no-pause", check_exit=0)
        s = _session(profile_dir)
        a, b = s["tasks"]
        assert a["status"] == "in_progress" and not a.get("dispatched"), \
            f"V9: t1 should be reclaimed (in_progress, not dispatched): {a}"
        assert b["status"] == "in_progress", \
            f"V9: --no-pause should leave t2 in_progress: {b}"
        assert s["current_task_index"] == 0, f"V9: pointer should be t1: {s}"
        print("V9: switch --no-pause reclaims a dispatched task [OK]")

        print("\nAll V1-V9 scenarios passed.")
        return 0
    finally:
        shutil.rmtree(base, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
