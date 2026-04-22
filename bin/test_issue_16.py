#!/usr/bin/env python3
"""Verification scenarios for issue #16 (path-resolution cleanup).

Runs V1-V6 from the issue against an isolated WPL_ROOT under /tmp.
Exit 0 on success, 1 on any failed assertion.

Usage:
    python3 bin/test_issue_16.py

The script spawns the CLIs as subprocesses with a sanitized environment
so it never touches the user's real ~/.workplanner state.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


BIN_DIR = Path(__file__).resolve().parent
TRANSITION = BIN_DIR / "transition.py"
HANDOFF = BIN_DIR / "handoff.py"
SESSION_HOOK = BIN_DIR / "session-hook.sh"
WPL_WRAPPER_NAME = "wpl"


def _run(cwd, env, *args, script=None, check_exit=None, stdin_input=None):
    """Invoke a workplanner script as a subprocess.
    Returns (rc, stdout, stderr)."""
    full_env = dict(os.environ)
    for k in [k for k in full_env if k.startswith("WPL_")]:
        del full_env[k]
    full_env.update(env)
    full_env.setdefault("WPL_CHILD", "1")
    script_path = script or TRANSITION
    cmd = [sys.executable, str(script_path), *args]
    proc = subprocess.run(
        cmd,
        cwd=str(cwd),
        env=full_env,
        capture_output=True,
        text=True,
        input=stdin_input,
    )
    if check_exit is not None:
        assert proc.returncode == check_exit, (
            f"expected exit {check_exit}, got {proc.returncode}\n"
            f"cmd: {cmd}\nstdout: {proc.stdout}\nstderr: {proc.stderr}"
        )
    return proc.returncode, proc.stdout, proc.stderr


def _init_session(cwd, env, date_str):
    """Create a minimal current-session.json for the profile resolved at cwd."""
    # First resolve where we should write.
    _, root, _ = _run(cwd, env, "profile", "whoami", "--print-root", check_exit=0)
    root = Path(root.strip())
    session_path = root / "session" / "current-session.json"
    session_path.parent.mkdir(parents=True, exist_ok=True)
    session_path.write_text(json.dumps({
        "date": date_str,
        "week": "W16",
        "checkpoint": "initialized",
        "eod_target": "18:00",
        "current_task_index": 0,
        "tasks": [
            {"uid": "task0001", "title": "First task", "status": "in_progress",
             "estimate_min": 30, "source": "manual", "started_at": "09:00"},
            {"uid": "task0002", "title": "Second task", "status": "pending",
             "estimate_min": 15, "source": "manual"},
        ],
    }) + "\n")
    return root, session_path


def main():
    base = tempfile.mkdtemp(prefix="wpl-issue16-")
    try:
        wpl_root = Path(base) / ".wpl"
        ws_a = Path(base) / "ws-a"
        ws_b = Path(base) / "ws-b"
        for p in (ws_a, ws_b):
            p.mkdir(parents=True, exist_ok=True)

        env = {"WPL_ROOT": str(wpl_root)}

        # Bootstrap two profiles pinned to separate workspaces.
        _run(Path(base), env, "profile", "create", "pa",
             "--workspace", str(ws_a), check_exit=0)
        _run(Path(base), env, "profile", "create", "pb",
             "--workspace", str(ws_b), check_exit=0)

        import datetime as _dt
        today = _dt.date.today().isoformat()

        root_a, session_a = _init_session(ws_a, env, today)
        root_b, session_b = _init_session(ws_b, env, today)

        # ── V1: concurrent sessions in different profiles; each dashboard-view
        # reflects only that profile's state ───────────────────────────────
        _, _, _ = _run(ws_a, env, "done", check_exit=0)  # marks task0 done in pa
        # Confirm pa's dashboard exists and mentions the profile.
        dash_a = root_a / "session" / "dashboard-view.txt"
        dash_b = root_b / "session" / "dashboard-view.txt"
        assert dash_a.exists(), "V1: pa dashboard-view.txt not written"
        assert "[pa]" in dash_a.read_text(), \
            f"V1: pa dashboard header missing [pa]: {dash_a.read_text()[:200]!r}"
        # pb's dashboard shouldn't have been touched.
        assert not dash_b.exists() or "[pa]" not in dash_b.read_text(), \
            "V1: pb dashboard leaked pa state"

        # Now mutate in pb from its workspace; pa's dashboard should stay.
        _, _, _ = _run(ws_b, env, "done", check_exit=0)
        assert dash_b.exists(), "V1: pb dashboard-view.txt not written"
        assert "[pb]" in dash_b.read_text(), \
            f"V1: pb dashboard header missing [pb]: {dash_b.read_text()[:200]!r}"
        assert "[pa]" in dash_a.read_text(), "V1: pa dashboard was overwritten"
        print("V1: concurrent dashboard isolation [OK]")

        # ── V2: `wpl --profile other done` from profile A's cwd writes
        # dashboard-view.txt under `other`, not active ─────────────────
        # Re-init sessions so we can do another mutation.
        session_a.write_text(json.dumps({
            "date": today, "week": "W16", "checkpoint": "initialized",
            "eod_target": "18:00", "current_task_index": 0,
            "tasks": [
                {"uid": "task00a1", "title": "PA-task", "status": "in_progress",
                 "estimate_min": 30, "source": "manual", "started_at": "09:00"},
            ],
        }) + "\n")
        session_b.write_text(json.dumps({
            "date": today, "week": "W16", "checkpoint": "initialized",
            "eod_target": "18:00", "current_task_index": 0,
            "tasks": [
                {"uid": "task00b1", "title": "PB-task", "status": "in_progress",
                 "estimate_min": 30, "source": "manual", "started_at": "09:00"},
            ],
        }) + "\n")
        # Snapshot pa's dashboard modification time so we can detect no-touch.
        dash_a_mtime_before = dash_a.stat().st_mtime if dash_a.exists() else 0
        # Make sure active symlink points at pa so a buggy render would land there.
        _, _, _ = _run(Path(base), env, "profile", "switch", "pa", check_exit=0)
        # From ws_a cwd, run with --profile pb. The render should update pb's
        # dashboard, not pa's (which is what 'active' points at).
        _, _, _ = _run(ws_a, env, "--profile", "pb", "done", check_exit=0)
        dash_b_text = dash_b.read_text()
        assert "[pb]" in dash_b_text, \
            f"V2: pb dashboard not updated under --profile pb: {dash_b_text[:200]!r}"
        # Parse pb's session state — task should be done.
        sess_b = json.loads(session_b.read_text())
        assert sess_b["tasks"][0]["status"] == "done", \
            f"V2: pb task not marked done: {sess_b['tasks'][0]!r}"
        # pa's session should be untouched.
        sess_a = json.loads(session_a.read_text())
        assert sess_a["tasks"][0]["status"] == "in_progress", \
            f"V2: pa task was touched: {sess_a['tasks'][0]!r}"
        print("V2: --profile override routes render correctly [OK]")

        # ── V3: dispatch-style cross-profile launch — handoff writes to
        # the right profile when --profile overrides ──────────────────
        # Simulate what the dispatch skill does: from cwd=ws_a, with
        # --profile pb, write a handoff. It should land under pb/handoffs/.
        rc, out, err = _run(
            ws_a, env, "--profile", "pb", "write",
            "--session-id", "s-test-v3",
            "--trajectory", "V3 trajectory test",
            script=HANDOFF, check_exit=0,
        )
        handoff_path = Path(out.strip())
        assert str(handoff_path).startswith(str(root_b)), \
            f"V3: handoff landed outside pb root: {handoff_path} (root_b={root_b})"
        assert handoff_path.parent.name == "handoffs"
        # Confirm content made it in.
        body = handoff_path.read_text()
        assert "V3 trajectory test" in body, \
            f"V3: trajectory not in handoff file: {body[:200]!r}"
        print("V3: dispatch handoff respects --profile [OK]")

        # ── V4: session-hook.sh fired from profile B's workspace injects
        # status for profile B ─────────────────────────────────────────
        # The hook uses `wpl`, which we install via ensure_wrapper(). Run
        # `wpl` once to set up the wrapper, then invoke the hook from ws_b.
        # We pass the wrapper path via WPL_BIN so it's unambiguous.
        wpl_bin = wpl_root / "bin" / "wpl"
        # transition.py::ensure_wrapper writes the wrapper at this path on
        # any run with WPL_ROOT set.
        assert wpl_bin.exists(), f"V4: wpl wrapper missing at {wpl_bin}"
        hook_env = dict(env)
        hook_env["WPL_BIN"] = str(wpl_bin)
        proc = subprocess.run(
            ["bash", str(SESSION_HOOK)],
            cwd=str(ws_b),
            env={**os.environ, **hook_env, "WPL_CHILD": "1"},
            capture_output=True, text=True,
        )
        # Hook emits the status text to stdout.
        output = proc.stdout
        assert "PB-task" in output or "Active workplan for today" in output, \
            f"V4: hook output missing pb context: {output!r}"
        # Must NOT show PA's task title.
        assert "PA-task" not in output, \
            f"V4: hook leaked pa context from ws_b cwd: {output!r}"
        print("V4: session-hook resolves from cwd [OK]")

        # ── V5: `wpl defer --reason ... --format json` includes defer_reason
        # in the task record ───────────────────────────────────────────
        # Re-init pa session with a deferrable task. defer count starts at 0
        # so a single defer doesn't hit threshold (default 3).
        session_a.write_text(json.dumps({
            "date": today, "week": "W16", "checkpoint": "initialized",
            "eod_target": "18:00", "current_task_index": 0,
            "tasks": [
                {"uid": "taskV5", "title": "V5-task", "status": "in_progress",
                 "estimate_min": 30, "source": "manual", "started_at": "09:00"},
            ],
        }) + "\n")
        _, out, _ = _run(ws_a, env, "--format", "json", "defer",
                         "--reason", "waiting on legal", check_exit=0)
        payload = json.loads(out)
        # Look for the affected task with defer_reason.
        tasks_in_payload = payload.get("session", {}).get("tasks", [])
        matched = [t for t in tasks_in_payload if t.get("uid") == "taskV5"]
        assert matched, f"V5: V5-task not in payload: {payload!r}"
        assert matched[0].get("defer_reason") == "waiting on legal", \
            f"V5: defer_reason missing or wrong: {matched[0]!r}"
        print("V5: defer_reason surfaces in --format json [OK]")

        # ── V6: `wpl defer` at reckoning threshold with --format json emits
        # a structured payload and exits 2 ──────────────────────────────
        # Pre-load a task at deferral_count = threshold-1 so one more defer
        # crosses the line.
        session_a.write_text(json.dumps({
            "date": today, "week": "W16", "checkpoint": "initialized",
            "eod_target": "18:00", "current_task_index": 0,
            "tasks": [
                {"uid": "taskV6", "title": "V6-task", "status": "in_progress",
                 "estimate_min": 30, "source": "manual", "started_at": "09:00",
                 "deferral_count": 2, "defer_reason": "prior reason"},
            ],
        }) + "\n")
        rc, out, err = _run(ws_a, env, "--format", "json", "defer",
                            "--reason", "still waiting")
        assert rc == 2, f"V6: expected exit 2 at threshold, got {rc} (stdout={out!r}, stderr={err!r})"
        # Output should be a single JSON line, not human text.
        try:
            rpayload = json.loads(out)
        except json.JSONDecodeError:
            raise AssertionError(f"V6: output not JSON: {out!r}")
        assert rpayload.get("result") == "reckoning-required", \
            f"V6: wrong result field: {rpayload!r}"
        assert rpayload.get("action") == "defer"
        assert rpayload.get("threshold") == 3
        assert set(rpayload.get("choices", [])) == {"b", "d", "x", "t", "k"}
        task_record = rpayload.get("task", {})
        assert task_record.get("uid") == "taskV6"
        assert task_record.get("deferral_count") == 3
        assert task_record.get("defer_reason") == "still waiting"
        print("V6: reckoning threshold honours --format json [OK]")

        print("\nAll V1-V6 scenarios passed.")
        return 0
    finally:
        shutil.rmtree(base, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
