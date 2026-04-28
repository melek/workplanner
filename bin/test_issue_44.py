#!/usr/bin/env python3
"""Regression suite for issue #44 — structural brief-then-gate.

Each scenario isolates one load-bearing assertion the precondition makes:
unbriefed advances refuse, briefed advances pass, organizational ops are
unaffected, migration is invisible on first load, idempotent re-brief
preserves prior state in the undo log, and the `⚠ unbriefed` marker is
visible in `wpl status`. If any of these regress, this file fails fast
with a named scenario rather than letting the drift back into the build.

Run: `python3 bin/test_issue_44.py` (or `pytest`-compatible runner).
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TRANSITION = REPO / "bin" / "transition.py"


def _run(cwd, env, *args, check_exit=None, stdin_input=None):
    """Run transition.py with the given args. Returns (rc, stdout, stderr)."""
    cmd = [sys.executable, str(TRANSITION), *args]
    proc = subprocess.run(
        cmd,
        cwd=str(cwd),
        env={**os.environ, **env},
        capture_output=True,
        text=True,
        input=stdin_input,
    )
    if check_exit is not None and proc.returncode != check_exit:
        raise AssertionError(
            f"expected exit {check_exit}, got {proc.returncode}\n"
            f"cmd: {cmd}\n"
            f"stdout: {proc.stdout}\n"
            f"stderr: {proc.stderr}"
        )
    return proc.returncode, proc.stdout, proc.stderr


def _bootstrap(base):
    """Create a one-profile WPL_ROOT with an empty active session.

    Tasks are written via fixtures rather than /start so the test isolates
    the precondition from inbox-sweep behavior.
    """
    wpl_root = Path(base) / "wpl"
    profile_dir = wpl_root / "profiles" / "test"
    profile_dir.mkdir(parents=True)
    (profile_dir / "session").mkdir()
    (profile_dir / "config.json").write_text('{"workspaces": [], "timezone": "UTC"}\n')
    (profile_dir / "backlog.json").write_text(
        '{"schema_version": 1, "items": []}\n')
    return wpl_root, profile_dir


def _write_session(profile_dir, tasks, current_index=None, session_date=None):
    if session_date is None:
        from datetime import datetime as _dt, timezone
        session_date = _dt.now(timezone.utc).date().isoformat()
    body = {
        "date": session_date,
        "week": "W18",
        "checkpoint": "assembly_complete",
        "eod_target": "18:00",
        "current_task_index": current_index,
        "tasks": tasks,
        "inbox_items": [],
        "headlines": [],
    }
    path = profile_dir / "session" / "current-session.json"
    path.write_text(json.dumps(body, indent=2) + "\n")


def main():
    base = tempfile.mkdtemp(prefix="wpl-issue44-")
    try:
        wpl_root, profile_dir = _bootstrap(base)
        env = {"WPL_ROOT": str(wpl_root)}
        ws = Path(base)

        # ── V1: migration backfills pre-existing tasks ──────────────
        # Marker doesn't exist; first wpl call should backfill briefed_at
        # on every task in the active session. This is the upgrade path
        # for users with in-flight sessions.
        _write_session(profile_dir, tasks=[
            {"uid": "v1-1", "title": "Pre-existing in-progress",
             "status": "in_progress",
             "estimate_min": 30, "source": "manual", "started_at": "09:00"},
            {"uid": "v1-2", "title": "Pre-existing pending",
             "status": "pending",
             "estimate_min": 15, "source": "manual"},
        ], current_index=0)
        _, _, _ = _run(ws, env, "status", check_exit=0)
        marker = profile_dir / ".briefing-precondition-migrated"
        assert marker.exists(), "V1: migration marker not written"
        s = json.loads((profile_dir / "session" / "current-session.json").read_text())
        for t in s["tasks"]:
            assert t.get("briefed_at"), \
                f"V1: task {t['uid']} not auto-briefed: {t!r}"
            assert "auto-migrated" in t.get("brief_rationale", ""), \
                f"V1: task {t['uid']} rationale wrong: {t!r}"
        # `wpl done` on the auto-briefed task should now succeed.
        _, _, _ = _run(ws, env, "done", check_exit=0)
        print("V1: migration backfills + advance succeeds [OK]")

        # ── V2: tasks added post-migration are unbriefed; advance refuses ──
        # After V1: t1 done, t2 pending+briefed-via-migration. Add inserts
        # at end (cur_idx is None after done), so fresh task lands at t3.
        rc, out, err = _run(ws, env, "add", "Fresh task", "--est", "20")
        assert rc == 0, f"V2: add should succeed, got rc={rc}"
        # The new task should appear with the unbriefed marker in stdout.
        assert "unbriefed" in out, \
            f"V2: ⚠ unbriefed marker missing from add output: {out!r}"
        # Switch to the fresh unbriefed task (allowed) then try to done (refused).
        _, _, _ = _run(ws, env, "switch", "t3", check_exit=0)
        rc, out, err = _run(ws, env, "done")
        assert rc == 1, f"V2: expected exit 1 on unbriefed done, got {rc} stdout={out!r} stderr={err!r}"
        assert "has not been briefed" in err, \
            f"V2: error message missing 'has not been briefed': {err!r}"
        assert "/workplanner:pickup" in err or "wpl brief" in err, \
            f"V2: self-correction missing from error: {err!r}"
        print("V2: post-migration adds are unbriefed; advance refuses [OK]")

        # ── V3: structured JSON error carries self-correct ──────────
        rc, out, err = _run(ws, env, "--format", "json", "done")
        assert rc == 1, f"V3: expected exit 1 in JSON mode, got {rc}"
        payload = json.loads(err)
        assert "has not been briefed" in payload.get("error", ""), \
            f"V3: JSON error message wrong: {payload!r}"
        assert payload.get("tid") == "t3", f"V3: tid wrong: {payload!r}"
        assert payload.get("action") == "mark done", \
            f"V3: action wrong: {payload!r}"
        assert "/workplanner:pickup" in payload.get("self_correct", "") or \
               "wpl brief" in payload.get("self_correct", ""), \
            f"V3: self_correct missing: {payload!r}"
        print("V3: JSON error carries tid + action + self_correct [OK]")

        # ── V4: blocked, defer, reckon keep|break|delegate also refuse ──
        for verb_args, action in [
            (("blocked", "test"), "mark blocked"),
            (("defer", "--reason", "test"), "defer"),
        ]:
            rc, out, err = _run(ws, env, *verb_args)
            assert rc == 1, f"V4 ({verb_args[0]}): expected exit 1, got {rc}"
            assert action in err, \
                f"V4 ({verb_args[0]}): action '{action}' missing: {err!r}"
        # Reckon paths: keep|break|delegate refuse, drop allows.
        # Need to re-init a task with deferral_count >= threshold to enter reckoning.
        _write_session(profile_dir, tasks=[
            {"uid": "v4-1", "title": "Reckon target", "status": "in_progress",
             "estimate_min": 30, "source": "manual", "started_at": "09:00",
             "deferral_count": 3},
        ], current_index=0)
        for choice in ("k", "b", "d"):
            rc, out, err = _run(ws, env, "reckon", choice)
            assert rc == 1, f"V4 (reckon {choice}): expected exit 1, got {rc}"
            assert "has not been briefed" in err, \
                f"V4 (reckon {choice}): missing precondition error: {err!r}"
        # Drop is organizational — should succeed without briefing.
        rc, out, err = _run(ws, env, "reckon", "x")
        assert rc == 0, f"V4 (reckon drop): expected exit 0 (organizational), got {rc} err={err!r}"
        print("V4: blocked/defer/reckon keep|break|delegate refuse; drop allows [OK]")

        # ── V5: brief → done flow + idempotent re-brief ─────────────
        _write_session(profile_dir, tasks=[
            {"uid": "v5-1", "title": "Brief target", "status": "pending",
             "estimate_min": 30, "source": "manual"},
        ], current_index=None)
        _, _, _ = _run(ws, env, "switch", "t1", check_exit=0)
        # Brief with rationale; done should succeed.
        _, _, _ = _run(ws, env, "brief", "t1",
                       "--rationale", "first", check_exit=0)
        s = json.loads((profile_dir / "session" / "current-session.json").read_text())
        assert s["tasks"][0].get("briefed_at"), "V5: briefed_at not set after brief"
        assert s["tasks"][0].get("brief_rationale") == "first", \
            f"V5: rationale wrong: {s['tasks'][0]!r}"
        # Re-brief updates timestamp + rationale (idempotent — exit 0).
        _, _, _ = _run(ws, env, "brief", "t1",
                       "--rationale", "second", check_exit=0)
        s = json.loads((profile_dir / "session" / "current-session.json").read_text())
        assert s["tasks"][0].get("brief_rationale") == "second", \
            f"V5: re-brief didn't update rationale: {s['tasks'][0]!r}"
        # Undo restores the prior brief.
        _, _, _ = _run(ws, env, "undo", check_exit=0)
        s = json.loads((profile_dir / "session" / "current-session.json").read_text())
        assert s["tasks"][0].get("brief_rationale") == "first", \
            f"V5: undo didn't restore prior rationale: {s['tasks'][0]!r}"
        # Done now succeeds.
        _, _, _ = _run(ws, env, "done", check_exit=0)
        print("V5: brief → done; idempotent re-brief; undo preserves history [OK]")

        # ── V6: backlog promotion produces unbriefed task ───────────
        _, _, _ = _run(ws, env, "backlog", "Future task", "--est", "25")
        bl = json.loads((profile_dir / "backlog.json").read_text())
        backlog_uid = bl["items"][0]["uid"]
        _, _, _ = _run(ws, env, "backlog", "--promote", backlog_uid, check_exit=0)
        s = json.loads((profile_dir / "session" / "current-session.json").read_text())
        promoted = [t for t in s["tasks"] if t["title"] == "Future task"][0]
        assert not promoted.get("briefed_at"), \
            f"V6: promoted task should be unbriefed, got: {promoted!r}"
        # And status output should show the marker.
        rc, out, err = _run(ws, env, "status", check_exit=0)
        assert "unbriefed" in out, \
            f"V6: ⚠ unbriefed marker missing from status: {out!r}"
        print("V6: backlog promotion → unbriefed; marker visible [OK]")

        # ── V7: wpl add --done auto-briefs (--done is the acknowledgment) ──
        _, _, _ = _run(ws, env, "add", "Logged completed work",
                       "--est", "10", "--done", check_exit=0)
        s = json.loads((profile_dir / "session" / "current-session.json").read_text())
        added = [t for t in s["tasks"] if t["title"] == "Logged completed work"][0]
        assert added["status"] == "done"
        assert added.get("briefed_at"), \
            f"V7: --done task should be auto-briefed: {added!r}"
        assert "added as already-completed" in added.get("brief_rationale", ""), \
            f"V7: --done auto-brief rationale missing: {added!r}"
        print("V7: wpl add --done auto-briefs [OK]")

        print("\nAll V1-V7 scenarios passed.")
        return 0
    finally:
        shutil.rmtree(base, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
