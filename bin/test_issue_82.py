#!/usr/bin/env python3
"""Regression suite for issue #82 — ergonomics: session lifecycle, authorization
scope, addenda reach and hook truth.

Run: `python3 bin/test_issue_82.py`
"""
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_issue_60_61 import Ctx, _write_session, _task  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
BIN = REPO / "bin"
TRANSITION = BIN / "transition.py"
HANDOFF = BIN / "handoff.py"
SESSION_HOOK = BIN / "session-hook.sh"
POST_HOOK = BIN / "post-tool-use-hook.sh"
CONTEXT = BIN / "session_context.py"
TODAY = datetime.now(timezone.utc).date().isoformat()  # Ctx profiles run in UTC


def _run(c, script, *args, stdin=None, cwd=None):
    proc = subprocess.run([sys.executable, str(script), *args], cwd=cwd or c.base, input=stdin,
                          env={**os.environ, **c.env}, capture_output=True, text=True)
    return proc.returncode, proc.stdout, proc.stderr


def _handoff(c, ctx="CTX"):
    rc, _, err = _run(c, HANDOFF, "write", "--date", TODAY, "--session-id", "s1",
                      "--from-json", "-", stdin=json.dumps({"context": ctx}))
    assert rc == 0, err


def _session(c):
    return json.loads((c.profile_dir / "session" / "current-session.json").read_text())


def _open(c, tasks=None):
    _write_session(c.profile_dir, tasks if tasks is not None else [_task("aaaa0001", "A", status="done")])


# ── 1. Session lifecycle ────────────────────────────────────────────

def test_close_refuses_without_handoff():
    with Ctx() as c:
        _open(c)
        rc, out, err = c.run("session", "close")
        assert rc != 0 and "No handoff written" in err, (out, err)
        assert _session(c)["checkpoint"] != "closed"
        rc, out, err = c.run("session", "close", "--without-handoff")
        assert rc != 0 and "--reason" in err, err


def test_close_with_handoff_sets_fields_and_marks_status():
    with Ctx() as c:
        _open(c)
        _handoff(c)
        out = c.ok("session", "close")
        s = _session(c)
        assert s["checkpoint"] == "closed" and s["eod_handoff_written"] is True and s.get("closed_at"), s
        assert "closed at" in out, out
        status = c.ok("status")
        assert "CLOSED" in status, status
        rc, _, err = c.run("session", "close")
        assert rc != 0 and "already closed" in err


def test_close_without_handoff_with_reason():
    with Ctx() as c:
        _open(c)
        c.ok("session", "close", "--without-handoff", "--reason", "day off")
        s = _session(c)
        assert s["eod_handoff_written"] is False and s["close_reason"] == "day off", s


def test_close_lists_open_tasks():
    with Ctx() as c:
        _open(c, [_task("aaaa0001", "A"), _task("bbbb0002", "B", status="done")])
        _handoff(c)
        out = c.ok("session", "close")
        assert "Still open: t1" in out, out


def test_mutation_on_closed_session_notices_and_applies():
    with Ctx() as c:
        _open(c)
        _handoff(c)
        c.ok("session", "close")
        rc, out, err = c.run("add", "Late task", "--done")
        assert rc == 0, err
        assert "notice: today's session was closed" in err, err
        assert any(t["title"] == "Late task" for t in _session(c)["tasks"])


def test_reopen_requires_reason_and_fresh_handoff():
    with Ctx() as c:
        _open(c)
        _handoff(c)
        c.ok("session", "close")
        rc, _, err = c.run("session", "reopen")
        assert rc != 0 and "--reason" in err
        c.ok("session", "reopen", "--reason", "post-EOD fixes")
        s = _session(c)
        assert s["checkpoint"] != "closed" and s["reopened"][-1]["reason"] == "post-EOD fixes"
        # Handoff predates the reopen: close must refuse until it's updated.
        hp = c.profile_dir / "handoffs" / f"{TODAY}.md"
        old = time.time() - 3600
        os.utime(hp, (old, old))
        rc, _, err = c.run("session", "close")
        assert rc != 0 and "since the session was reopened" in err, err
        _handoff(c, "UPDATED")
        c.ok("session", "close")
        assert _session(c)["checkpoint"] == "closed"


def test_reopen_refuses_open_session():
    with Ctx() as c:
        _open(c)
        rc, _, err = c.run("session", "reopen", "--reason", "x")
        assert rc != 0 and "isn't closed" in err


# ── 2. done from deferred ───────────────────────────────────────────

def test_done_accepts_deferred():
    with Ctx() as c:
        _write_session(c.profile_dir, [_task("aaaa0001", "A", status="deferred"),
                                       _task("bbbb0002", "B")], 1)
        c.ok("done", "t1")
        assert _session(c)["tasks"][0]["status"] == "done"


# ── 3. Addenda for every skill ──────────────────────────────────────

ADDENDA = """# Addenda

## Runbook 4 (Slack): standup thread
START-RULE

## eod: commit the workspace
EOD-RULE

## pickup — brief format
PICKUP-RULE

## EOD Step 3b — standup draft
EOD-STEP-RULE
"""


def _with_addenda(c, auth=None):
    cfg = {"workspaces": [], "timezone": "UTC", "runbook_addenda": "addenda.md"}
    (c.profile_dir / "addenda.md").write_text(ADDENDA)
    if auth is not None:
        cfg["authorization"] = "authorization.md"
        (c.profile_dir / "authorization.md").write_text(auth)
    (c.profile_dir / "config.json").write_text(json.dumps(cfg))


def test_addenda_partition_by_skill():
    with Ctx() as c:
        _open(c)
        _with_addenda(c)
        start = c.ok("addenda", "--for", "start")
        eod = c.ok("addenda", "--for", "eod")
        pickup = c.ok("addenda", "--for", "pickup")
        horizon = c.ok("addenda", "--for", "horizon")
        assert "START-RULE" in start and "EOD-RULE" not in start, start
        assert "EOD-RULE" in eod and "EOD-STEP-RULE" in eod and "START-RULE" not in eod, eod
        assert "PICKUP-RULE" in pickup, pickup
        assert "No profile addenda for horizon" in horizon, horizon
        rc, _, err = c.run("addenda", "--for", "nope")
        assert rc != 0 and "Unknown skill" in err


def test_authorization_default_and_profile_file():
    with Ctx() as c:
        _open(c)
        out = c.ok("authorization")
        assert "A yes covers only what it names" in out, out
        _with_addenda(c, auth="Local commits need no brief.\nNever post; draft.")
        out = c.ok("authorization")
        assert "Local commits need no brief." in out and "A yes covers" not in out, out


# ── 4. Hook truth ───────────────────────────────────────────────────

def _context(c, source="startup"):
    proc = subprocess.run([sys.executable, str(CONTEXT), str(c.profile_dir), source, "/plug"],
                          capture_output=True, text=True)
    return proc.stdout


def test_context_open_session_has_now_auth_and_methodology():
    with Ctx() as c:
        _open(c, [_task("aaaa0001", "Alpha", status="in_progress")])
        s = _session(c); s["current_task_index"] = 0
        (c.profile_dir / "session" / "current-session.json").write_text(json.dumps(s))
        out = _context(c)
        assert "Now:" in out and "(UTC)" in out, out
        assert "Current: t1 — Alpha" in out, out
        assert "A yes covers only what it names" in out, out
        assert "/plug/docs/methodology.md" in out, out
        assert "Cite at least one principle" not in out
        assert "methodology.md" not in _context(c, "resume"), "resume should skip the methodology pointer"


def test_context_closed_stale_missing():
    with Ctx() as c:
        _open(c)
        _handoff(c)
        c.ok("session", "close")
        assert "closed at" in _context(c) and "wpl session reopen" in _context(c)
        s = _session(c); s["date"] = "2000-01-01"; s["checkpoint"] = "assembly_complete"
        (c.profile_dir / "session" / "current-session.json").write_text(json.dumps(s))
        assert "is from 2000-01-01" in _context(c)
        (c.profile_dir / "session" / "current-session.json").unlink()
        assert "No session yet today" in _context(c)


def test_context_uses_profile_authorization_file():
    with Ctx() as c:
        _open(c)
        _with_addenda(c, auth="PROFILE-SCOPE-LINE")
        out = _context(c)
        assert "PROFILE-SCOPE-LINE" in out and "from this profile" in out, out


def test_hook_silent_when_no_profile_claims_cwd():
    """No `active` fallback: with two profiles and a cwd neither claims, nothing is injected."""
    with Ctx() as c:
        _open(c, [_task("aaaa0001", "SECRET-TASK", status="in_progress")])
        other = c.wpl_root / "profiles" / "other"
        (other / "session").mkdir(parents=True)
        (other / "config.json").write_text('{"workspaces": ["/nonexistent/x"], "timezone": "UTC"}\n')
        (c.profile_dir / "config.json").write_text('{"workspaces": ["/nonexistent/y"], "timezone": "UTC"}\n')
        active = c.wpl_root / "profiles" / "active"
        if not active.exists():
            active.symlink_to(c.profile_dir)
        env = {k: v for k, v in os.environ.items() if not k.startswith("WPL_")}
        env.update({"WPL_ROOT": str(c.wpl_root), "CLAUDE_PLUGIN_ROOT": str(REPO), "WPL_CHILD": "1"})
        proc = subprocess.run(["bash", str(SESSION_HOOK)], cwd=c.base, env=env,
                              input='{"source": "startup"}', capture_output=True, text=True, timeout=10)
        assert "SECRET-TASK" not in proc.stdout and proc.stdout.strip() == "", proc.stdout


def test_post_hook_marks_closed():
    if not shutil.which("jq"):
        print("     (skipped: jq not installed)")
        return
    with Ctx() as c:
        _open(c)
        _handoff(c)
        c.ok("session", "close")
        wrapper = Path(c.base) / "wpl-bin"
        wrapper.write_text(f"#!/bin/sh\nexec {sys.executable} {TRANSITION} \"$@\"\n")
        wrapper.chmod(0o755)
        proc = subprocess.run(["bash", str(POST_HOOK)], cwd=c.base,
                              input=json.dumps({"tool_name": "Bash", "tool_input": {"command": "wpl status"}}),
                              env={**os.environ, **c.env, "WPL_BIN": str(wrapper)},
                              capture_output=True, text=True)
        assert "CLOSED" in proc.stdout, proc.stdout


def test_now_hhmm_uses_profile_timezone():
    with Ctx() as c:
        _write_session(c.profile_dir, [_task("aaaa0001", "A")], None)
        (c.profile_dir / "config.json").write_text('{"workspaces": [], "timezone": "Pacific/Kiritimati"}\n')
        c.ok("switch", "t1")
        started = _session(c)["tasks"][0]["started_at"]
        from zoneinfo import ZoneInfo
        want = datetime.now(ZoneInfo("Pacific/Kiritimati"))
        got = datetime.strptime(started[-5:] if len(started) > 5 else started, "%H:%M")
        diff = abs((want.hour * 60 + want.minute) - (got.hour * 60 + got.minute)) % (24 * 60)
        assert min(diff, 24 * 60 - diff) <= 2, (started, want.strftime("%H:%M"))


# ── 5. Review fixes ─────────────────────────────────────────────────

def test_actual_min_with_system_tz_different_from_profile():
    """Profile UTC, system Asia/Tokyo: switch then done at once is ~0 minutes, not 540."""
    with Ctx() as c:
        _write_session(c.profile_dir, [_task("aaaa0001", "A")], None)
        env = {**os.environ, **c.env, "TZ": "Asia/Tokyo"}
        for args in (("switch", "t1"), ("done", "t1")):
            p = subprocess.run([sys.executable, str(TRANSITION), *args], cwd=c.base, env=env,
                               capture_output=True, text=True)
            assert p.returncode == 0, p.stderr
        assert (_session(c)["tasks"][0].get("actual_min") or 0) <= 2, _session(c)["tasks"][0]


def _addenda_file(c, text):
    (c.profile_dir / "addenda.md").write_text(text)
    (c.profile_dir / "config.json").write_text(
        json.dumps({"workspaces": [], "timezone": "UTC", "runbook_addenda": "addenda.md"}))


def test_addenda_without_sections_or_with_preamble_stays_start():
    with Ctx() as c:
        _open(c)
        _addenda_file(c, "- Skip bot messages.\n- Prefer Linear.\n")
        assert "Skip bot messages" in c.ok("addenda", "--for", "start")
        _addenda_file(c, "# Title\n\nGeneral: skip bots.\n\n## eod: commit\nEOD-X\n")
        start = c.ok("addenda", "--for", "start")
        assert "General: skip bots." in start and "EOD-X" not in start, start
        assert "EOD-X" in c.ok("addenda", "--for", "eod")


def test_addenda_fences_and_key_variants():
    with Ctx() as c:
        _open(c)
        _addenda_file(c, "## /eod: rule\nA\n```\n## not a heading\n```\nB\n\n"
                         "## `pickup` rules\nP\n\n## Dispatch-free days\nD\n\n## Freeze window Fridays\nF\n")
        eod = c.ok("addenda", "--for", "eod")
        assert "A" in eod and "## not a heading" in eod and "B" in eod, eod
        assert "P" in c.ok("addenda", "--for", "pickup")
        start = c.ok("addenda", "--for", "start")
        assert "Freeze window Fridays" in start and "Dispatch-free days" in start, start


def test_addenda_missing_file_is_reported():
    with Ctx() as c:
        _open(c)
        (c.profile_dir / "config.json").write_text(
            json.dumps({"workspaces": [], "timezone": "UTC", "runbook_addenda": "gone.md"}))
        assert "not found" in c.ok("addenda", "--for", "start")


def test_reopen_clears_flag_and_without_handoff_keeps_existing():
    with Ctx() as c:
        _open(c)
        _handoff(c)
        c.ok("session", "close")
        c.ok("session", "reopen", "--reason", "late work")
        assert _session(c)["eod_handoff_written"] is False
        c.ok("session", "close", "--without-handoff", "--reason", "nothing new")
        assert _session(c)["eod_handoff_written"] is True, "existing handoff still counts"


def test_done_from_deferred_has_no_measured_time():
    with Ctx() as c:
        _write_session(c.profile_dir, [_task("aaaa0001", "A", status="deferred", started_at="01:00"),
                                       _task("bbbb0002", "B")], 1)
        c.ok("done", "t1")
        t = _session(c)["tasks"][0]
        assert t["status"] == "done" and t.get("actual_min") is None, t


def test_closed_notice_when_no_handoff():
    with Ctx() as c:
        _open(c)
        c.ok("session", "close", "--without-handoff", "--reason", "x")
        rc, _, err = c.run("add", "Late", "--done")
        assert "none was written" in err, err
        assert "no handoff was written" in _context(c)


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"ok  {t.__name__}")
    print(f"{len(tests)} passed")
