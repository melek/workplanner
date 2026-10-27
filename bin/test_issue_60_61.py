#!/usr/bin/env python3
"""Regression suite for issues #60 and #61 — task edit and task outcomes.

#61: a task settled without doing it (handled elsewhere, judged optional,
covered by someone else) had no exit but `done`, which counted it as
completed work; and `done` on a never-started task logged `actual_min: 0`,
a fabricated measurement. Now `wpl drop <task> --reason` records a
`dropped` outcome (and `reckon x` uses it instead of deleting the task),
and `actual_min` is `null` when nothing was measured.

#60: notes, url, ref and estimate could only be set at `add`; later
context needed a direct edit of the session JSON. `wpl edit` covers it.

Run: `python3 bin/test_issue_60_61.py`
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
TRANSITION = REPO / "bin" / "transition.py"
RENDER = REPO / "bin" / "render_dashboard.py"
HOOK = REPO / "bin" / "post-tool-use-hook.sh"


def _run(cwd, env, *args, script=TRANSITION):
    cmd = [sys.executable, str(script), *args]
    proc = subprocess.run(cmd, cwd=str(cwd), env={**os.environ, **env},
                          capture_output=True, text=True)
    return proc.returncode, proc.stdout, proc.stderr


def _ok(cwd, env, *args, script=TRANSITION):
    rc, out, err = _run(cwd, env, *args, script=script)
    assert rc == 0, f"expected exit 0 for {args}, got {rc}\nstdout: {out}\nstderr: {err}"
    return out


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


def _ago(minutes):
    return (datetime.now() - timedelta(minutes=minutes)).strftime("%H:%M")


class Ctx:
    def __init__(self):
        self.base = tempfile.mkdtemp(prefix="wpl-issue6061-")
        self.wpl_root, self.profile_dir = _bootstrap(self.base)
        self.env = {"WPL_ROOT": str(self.wpl_root), "WPL_CHILD": "1"}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        shutil.rmtree(self.base)

    def ok(self, *args, script=TRANSITION):
        return _ok(self.base, self.env, *args, script=script)

    def run(self, *args):
        return _run(self.base, self.env, *args)

    def tasks(self):
        return _session(self.profile_dir)["tasks"]


# ── #61 part 2: actual_min is null when nothing was measured ─────────


def test_done_never_started_is_null():
    with Ctx() as c:
        _write_session(c.profile_dir, [_task("aaaa0001", "A")])
        out = c.ok("done", "t1")
        assert c.tasks()[0]["actual_min"] is None, c.tasks()[0]
        assert "(—/15m est)" in out, out
        assert "(—/15m)" in out, f"task list should show unmeasured time as —:\n{out}"


def test_done_started_is_int():
    with Ctx() as c:
        _write_session(c.profile_dir,
                       [_task("aaaa0001", "A", status="in_progress", started_at=_ago(10))],
                       current_index=0)
        c.ok("done", "t1")
        actual = c.tasks()[0]["actual_min"]
        assert isinstance(actual, int) and actual >= 9, actual


def test_history_and_dashboard_tolerate_null():
    with Ctx() as c:
        archive = c.profile_dir / "session" / "agendas" / "archive"
        archive.mkdir(parents=True)
        (archive / "2026-01-05.json").write_text(json.dumps({
            "date": "2026-01-05",
            "tasks": [_task("aaaa0001", "Unmeasured", status="done", actual_min=None),
                      _task("bbbb0002", "Instant", status="done", actual_min=0)],
        }))
        out = c.ok("history")
        assert "Unmeasured  (—)" in out, out
        assert "Instant  (0m)" in out, f"a measured 0 is not unknown:\n{out}"
        _write_session(c.profile_dir, [_task("aaaa0001", "A", status="done", actual_min=None)])
        c.ok(script=RENDER)


# ── #61 part 1: dropped outcome ──────────────────────────────────────


def test_drop_pending():
    with Ctx() as c:
        _write_session(c.profile_dir, [_task("aaaa0001", "A"), _task("bbbb0002", "B")])
        out = c.ok("drop", "t2", "--reason", "covered elsewhere")
        t = c.tasks()[1]
        assert t["title"] == "B" and t["status"] == "dropped", t
        assert t["drop_reason"] == "covered elsewhere", t
        assert t["actual_min"] is None, t
        assert "\u2717" in out, out


def test_drop_requires_reason():
    with Ctx() as c:
        _write_session(c.profile_dir, [_task("aaaa0001", "A")])
        rc, out, err = c.run("drop", "t1")
        assert rc != 0, (out, err)
        assert c.tasks()[0]["status"] == "pending"


def test_drop_refuses_done():
    with Ctx() as c:
        _write_session(c.profile_dir, [_task("aaaa0001", "A", status="done", actual_min=5)])
        rc, out, err = c.run("drop", "t1", "--reason", "x")
        assert rc != 0, (out, err)
        assert c.tasks()[0] == _task("aaaa0001", "A", status="done", actual_min=5)


def test_drop_current_settles_pointer():
    with Ctx() as c:
        _write_session(c.profile_dir,
                       [_task("aaaa0001", "A", status="in_progress", started_at=_ago(5)),
                        _task("bbbb0002", "B")], current_index=0)
        c.ok("drop", "--reason", "not needed")
        s = _session(c.profile_dir)
        assert s["current_task_index"] is None, s["current_task_index"]
        assert isinstance(s["tasks"][0]["actual_min"], int), "started task keeps its measured time"


def test_drop_then_undo():
    with Ctx() as c:
        before = [_task("aaaa0001", "A", notes="n"), _task("bbbb0002", "B")]
        _write_session(c.profile_dir, before)
        c.ok("drop", "t1", "--reason", "r")
        c.ok("undo")
        assert c.tasks() == before, c.tasks()


def test_counts_and_complete():
    with Ctx() as c:
        _write_session(c.profile_dir, [_task("aaaa0001", "A", status="done", actual_min=3),
                                       _task("bbbb0002", "B")])
        out = c.ok("drop", "t2", "--reason", "r")
        assert "Done: 1/2" in out and "1 dropped" in out, out
        status = c.ok("status")
        assert status.startswith("All tasks complete"), status
        payload = json.loads(c.ok("--format", "json", "status"))
        assert payload["counts"]["dropped"] == 1, payload["counts"]
        assert len(payload["dropped"]) == 1 and payload["dropped"][0]["drop_reason"] == "r"


def test_reckon_x_keeps_record():
    with Ctx() as c:
        _write_session(c.profile_dir, [_task("aaaa0001", "A", deferral_count=3, defer_reason="R"),
                                       _task("bbbb0002", "B")])
        c.ok("reckon", "x", "t1")
        tasks = c.tasks()
        assert len(tasks) == 2, tasks
        assert tasks[0]["status"] == "dropped" and tasks[0]["drop_reason"] == "R", tasks[0]


def test_history_dropped():
    with Ctx() as c:
        archive = c.profile_dir / "session" / "agendas" / "archive"
        archive.mkdir(parents=True)
        (archive / "2026-01-05.json").write_text(json.dumps({
            "date": "2026-01-05",
            "tasks": [_task("aaaa0001", "Skipped", status="dropped", drop_reason="r")]}))
        out = c.ok("history", "--status", "dropped")
        assert "Skipped" in out, out


def test_dashboard_renders_dropped():
    with Ctx() as c:
        _write_session(c.profile_dir, [_task("aaaa0001", "A", status="dropped", drop_reason="r"),
                                       _task("bbbb0002", "B")])
        c.ok(script=RENDER)
        view = (c.profile_dir / "session" / "dashboard-view.txt")
        text = view.read_text() if view.exists() else c.ok(script=RENDER)
        assert "dropped" in text, text
        assert "1 dropped" in text, f"summary should count dropped tasks:\n{text}"


# ── #60: wpl edit ─────────────────────────────────────────────────────


def test_edit_url_and_ref():
    with Ctx() as c:
        _write_session(c.profile_dir, [_task("aaaa0001", "A", notes="keep")])
        c.ok("edit", "t1", "--url", "https://example.com/x", "--ref", "PROJ-123")
        t = c.tasks()[0]
        assert t["url"] == "https://example.com/x" and t["ref"] == "PROJ-123", t
        assert t["notes"] == "keep" and t["status"] == "pending", t
        last = (c.profile_dir / "mutations.jsonl").read_text().splitlines()[-1]
        assert json.loads(last)["action"] == "edit", last


def test_append_notes_empty():
    with Ctx() as c:
        _write_session(c.profile_dir, [_task("aaaa0001", "A")])
        c.ok("edit", "t1", "--append-notes", "a")
        assert c.tasks()[0]["notes"] == "a", c.tasks()[0]
        c.ok("edit", "t1", "--append-notes", "b")
        assert c.tasks()[0]["notes"] == "a | b", c.tasks()[0]


def test_notes_replace():
    with Ctx() as c:
        _write_session(c.profile_dir, [_task("aaaa0001", "A", notes="old")])
        c.ok("edit", "t1", "--notes", "x")
        assert c.tasks()[0]["notes"] == "x"


def test_notes_flags_exclusive():
    with Ctx() as c:
        _write_session(c.profile_dir, [_task("aaaa0001", "A")])
        rc, out, err = c.run("edit", "t1", "--notes", "x", "--append-notes", "y")
        assert rc != 0, (out, err)
        assert "notes" not in c.tasks()[0]


def test_edit_nothing():
    with Ctx() as c:
        _write_session(c.profile_dir, [_task("aaaa0001", "A")])
        rc, out, err = c.run("edit", "t1")
        assert rc != 0 and "--notes" in err, (rc, err)


def test_edit_done_task():
    with Ctx() as c:
        _write_session(c.profile_dir, [_task("aaaa0001", "A", status="done", actual_min=4)])
        c.ok("edit", "t1", "--url", "https://example.com/done")
        t = c.tasks()[0]
        assert t["status"] == "done" and t["url"] == "https://example.com/done", t


def test_edit_est():
    with Ctx() as c:
        _write_session(c.profile_dir, [_task("aaaa0001", "A")])
        c.ok("edit", "t1", "--est", "45")
        assert c.tasks()[0]["estimate_min"] == 45


def test_edit_as_mismatch():
    with Ctx() as c:
        _write_session(c.profile_dir, [_task("aaaa0001", "A")])
        rc, out, err = c.run("edit", "t1", "--url", "https://example.com", "--as", "Not A")
        assert rc != 0, (out, err)
        assert "url" not in c.tasks()[0]


# ── Review fixes ──────────────────────────────────────────────────────


def test_hook_shows_dropped():
    """The post-wpl context injection must not show a dropped task as open."""
    if not shutil.which("jq"):
        print("     (skipped: jq not installed)")
        return
    with Ctx() as c:
        _write_session(c.profile_dir, [_task("aaaa0001", "A", status="dropped", drop_reason="r"),
                                       _task("bbbb0002", "B")])
        wrapper = Path(c.base) / "wpl-bin"
        wrapper.write_text(f"#!/bin/sh\nexec {sys.executable} {TRANSITION} \"$@\"\n")
        wrapper.chmod(0o755)
        payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": "wpl status"}})
        proc = subprocess.run(["bash", str(HOOK)], input=payload, cwd=c.base,
                              env={**os.environ, **c.env, "WPL_BIN": str(wrapper)},
                              capture_output=True, text=True)
        out = proc.stdout
        assert "[\u2717] t1" in out, f"dropped task shown as open:\n{out}"
        assert "1 dropped" in out, f"counts line should mention dropped:\n{out}"


def test_parent_with_dropped_children_completes():
    with Ctx() as c:
        _write_session(c.profile_dir, [_task("pppp0001", "P"),
                                       _task("cccc0002", "c1", status="dropped", drop_reason="r", parent=0),
                                       _task("cccc0003", "c2", status="done", actual_min=3, parent=0)])
        out = c.ok(script=RENDER)
        first = [l for l in out.splitlines() if " P" in l][0]
        assert "[x]" in first, f"parent should render complete:\n{out}"


def test_reckon_refuses_closed_task():
    with Ctx() as c:
        _write_session(c.profile_dir, [_task("aaaa0001", "A", status="done", actual_min=30,
                                             started_at=_ago(300), deferral_count=3),
                                       _task("bbbb0002", "B", status="dropped", drop_reason="r",
                                             deferral_count=3)])
        rc, out, err = c.run("reckon", "x", "t1")
        assert rc != 0, (out, err)
        assert c.tasks()[0]["actual_min"] == 30 and c.tasks()[0]["status"] == "done", c.tasks()[0]
        rc, out, err = c.run("reckon", "k", "t2")
        assert rc != 0, (out, err)
        assert c.tasks()[1]["status"] == "dropped", c.tasks()[1]


TESTS = [
    test_done_never_started_is_null,
    test_done_started_is_int,
    test_history_and_dashboard_tolerate_null,
    test_drop_pending,
    test_drop_requires_reason,
    test_drop_refuses_done,
    test_drop_current_settles_pointer,
    test_drop_then_undo,
    test_counts_and_complete,
    test_reckon_x_keeps_record,
    test_history_dropped,
    test_dashboard_renders_dropped,
    test_edit_url_and_ref,
    test_append_notes_empty,
    test_notes_replace,
    test_notes_flags_exclusive,
    test_edit_nothing,
    test_edit_done_task,
    test_edit_est,
    test_edit_as_mismatch,
    test_hook_shows_dropped,
    test_parent_with_dropped_children_completes,
    test_reckon_refuses_closed_task,
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
