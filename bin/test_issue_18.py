#!/usr/bin/env python3
"""Verification scenarios for issue #18 (cognitive-ergonomics cleanup).

Runs V1-V9 from the issue against an isolated WPL_ROOT under /tmp.
Exit 0 on success, 1 on any failed assertion.

Usage:
    python3 bin/test_issue_18.py

Each V maps to one finding from the convergent 3-pass LLM-as-user
review; they all exercise error-message / warning / hint changes, not
semantic behavior. Baseline preserved: commands that used to succeed
still succeed, commands that used to fail still fail (with better
messages).
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo


# Use UTC throughout so "today" matches what local_today() returns when
# the test profile is configured to UTC. Avoids off-by-one-day surprises
# around midnight in whichever tz the test host is running.
def _utc_today():
    return datetime.now(ZoneInfo("UTC")).date()


BIN_DIR = Path(__file__).resolve().parent
TRANSITION = BIN_DIR / "transition.py"
SESSION_HOOK = BIN_DIR / "session-hook.sh"


def _run(cwd, env, *args, check_exit=None, stdin_input=None):
    """Invoke transition.py as a subprocess. Returns (rc, stdout, stderr)."""
    full_env = dict(os.environ)
    for k in [k for k in full_env if k.startswith("WPL_")]:
        del full_env[k]
    full_env.update(env)
    full_env.setdefault("WPL_CHILD", "1")
    proc = subprocess.run(
        [sys.executable, str(TRANSITION), *args],
        cwd=str(cwd),
        env=full_env,
        capture_output=True,
        text=True,
        input=stdin_input,
    )
    if check_exit is not None:
        assert proc.returncode == check_exit, (
            f"expected exit {check_exit}, got {proc.returncode}\n"
            f"args: {args}\nstdout: {proc.stdout}\nstderr: {proc.stderr}"
        )
    return proc.returncode, proc.stdout, proc.stderr


def _write_session(profile_dir, tasks, session_date=None, current_index=None):
    """Write a current-session.json with the given tasks. Profile's
    timezone is configured to UTC-equivalent so session_date matching
    today is deterministic regardless of wall-clock shift."""
    if session_date is None:
        session_date = _utc_today().isoformat()
    session_path = profile_dir / "session" / "current-session.json"
    session_path.parent.mkdir(parents=True, exist_ok=True)
    session_path.write_text(json.dumps({
        "date": session_date,
        "checkpoint": "initialized",
        "eod_target": "18:00",
        "current_task_index": current_index,
        "tasks": tasks,
    }) + "\n")
    return session_path


def _configure_tz(profile_dir, tz_name):
    """Write `timezone` directly into the profile's config.json."""
    config_path = profile_dir / "config.json"
    try:
        cfg = json.loads(config_path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        cfg = {}
    cfg["timezone"] = tz_name
    config_path.write_text(json.dumps(cfg, indent=2) + "\n")


def _clear_tz(profile_dir):
    """Remove `timezone` from config.json if present (for V9)."""
    config_path = profile_dir / "config.json"
    try:
        cfg = json.loads(config_path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return
    cfg.pop("timezone", None)
    config_path.write_text(json.dumps(cfg, indent=2) + "\n")


def main():
    base = tempfile.mkdtemp(prefix="wpl-issue18-")
    try:
        wpl_root = Path(base) / ".wpl"
        ws = Path(base) / "ws"
        ws_b = Path(base) / "ws-b"
        for p in (ws, ws_b):
            p.mkdir(parents=True, exist_ok=True)

        env = {"WPL_ROOT": str(wpl_root)}

        # Bootstrap a profile pinned to ws. Pin timezone to UTC so
        # session_date = today.isoformat() is accurate in local_today().
        _run(Path(base), env, "profile", "create", "pa",
             "--workspace", str(ws), check_exit=0)
        _run(Path(base), env, "profile", "create", "pb",
             "--workspace", str(ws_b), check_exit=0)
        profile_a = wpl_root / "profiles" / "pa"
        profile_b = wpl_root / "profiles" / "pb"
        _configure_tz(profile_a, "UTC")
        _configure_tz(profile_b, "UTC")

        today = _utc_today().isoformat()

        # ── V1: banner distinguishes "all done" from "no active, pending" ──
        _write_session(profile_a, tasks=[
            {"uid": "v1-1", "title": "T1", "status": "in_progress",
             "estimate_min": 30, "source": "manual", "started_at": "09:00",
             "briefed_at": "2026-04-27T09:00:00", "brief_rationale": "test fixture"},
            {"uid": "v1-2", "title": "T2", "status": "pending",
             "estimate_min": 15, "source": "manual"},
            {"uid": "v1-3", "title": "T3", "status": "pending",
             "estimate_min": 20, "source": "manual"},
            {"uid": "v1-4", "title": "T4", "status": "pending",
             "estimate_min": 10, "source": "manual"},
        ], current_index=0)
        _run(ws, env, "switch", "t2", check_exit=0)
        _run(ws, env, "done", check_exit=0)
        rc, out, err = _run(ws, env, "status", check_exit=0)
        header = out.splitlines()[0] if out.splitlines() else ""
        assert "All tasks complete" not in header, \
            f"V1: banner lied about completion: {header!r}"
        assert "No active task" in header and "3 pending" in header, \
            f"V1: expected 'No active task — 3 pending' in header, got {header!r}"
        # Finish the plan; now banner should be "All tasks complete".
        for tid_ in ("t1", "t3", "t4"):
            _run(ws, env, "switch", tid_, check_exit=0)
            _run(ws, env, "done", check_exit=0)
        rc, out, err = _run(ws, env, "status", check_exit=0)
        header = out.splitlines()[0]
        assert header.startswith("All tasks complete"), \
            f"V1: completion banner not restored: {header!r}"
        print("V1: 'All tasks complete' fires only when done == total [OK]")

        # ── V2: stale-session warning + JSON fields ──────────────────
        stale_date = (_utc_today() - timedelta(days=3)).isoformat()
        _write_session(profile_a, tasks=[
            {"uid": "v2-1", "title": "Stale task", "status": "in_progress",
             "estimate_min": 30, "source": "manual", "started_at": "09:00"},
        ], session_date=stale_date, current_index=0)
        rc, out, err = _run(ws, env, "status", check_exit=0)
        assert "warning: session is from" in err, \
            f"V2: stale warning missing in stderr: {err!r}"
        assert stale_date in err, \
            f"V2: stale date missing from warning: {err!r}"
        rc, out, err = _run(ws, env, "--format", "json", "status", check_exit=0)
        payload = json.loads(out)
        assert payload.get("is_stale") is True, \
            f"V2: is_stale not true: {payload!r}"
        assert payload.get("session_date_offset_days") == 3, \
            f"V2: offset_days != 3: {payload.get('session_date_offset_days')}"
        print("V2: stale-session warning + JSON fields [OK]")

        # Restore today's session for subsequent tests.
        _write_session(profile_a, tasks=[
            {"uid": "v3-1", "title": "T1", "status": "in_progress",
             "estimate_min": 30, "source": "manual", "started_at": "09:00",
             "briefed_at": "2026-04-27T09:00:00", "brief_rationale": "test fixture"},
            {"uid": "v3-2", "title": "T2", "status": "pending",
             "estimate_min": 15, "source": "manual"},
        ], current_index=0)

        # ── V3: index-range error uses tN form on tN input ───────────
        rc, out, err = _run(ws, env, "switch", "t99")
        assert rc == 1, f"V3: expected rc=1, got {rc}"
        assert "t99 out of range" in err, \
            f"V3: expected 't99 out of range', got {err!r}"
        assert "t1" in err and "t2" in err, \
            f"V3: expected valid tN range in error, got {err!r}"
        assert "wpl status" in err, \
            f"V3: expected 'wpl status' hint, got {err!r}"
        print("V3: tN input gets tN range in error [OK]")

        # ── V4: missing session file names /workplanner:start ────────
        session_path = profile_a / "session" / "current-session.json"
        session_path.unlink()
        rc, out, err = _run(ws, env, "status")
        assert rc == 1, f"V4: expected rc=1, got {rc}"
        assert "/workplanner:start" in err, \
            f"V4: expected /workplanner:start in error, got {err!r}"
        rc, out, err = _run(ws, env, "--format", "json", "status")
        err_payload = json.loads(err)
        assert err_payload.get("next_action") == "/workplanner:start", \
            f"V4: JSON error missing next_action: {err_payload!r}"
        print("V4: missing session error names /workplanner:start [OK]")

        # Restore session.
        _write_session(profile_a, tasks=[
            {"uid": "v5-1", "title": "T1", "status": "in_progress",
             "estimate_min": 30, "source": "manual", "started_at": "09:00",
             "briefed_at": "2026-04-27T09:00:00", "brief_rationale": "test fixture"},
            {"uid": "v5-2", "title": "T2", "status": "pending",
             "estimate_min": 15, "source": "manual"},
            {"uid": "v5-3", "title": "T3", "status": "pending",
             "estimate_min": 20, "source": "manual"},
        ], current_index=0)

        # ── V5: done/blocked/defer t3 redirects to switch pattern ────
        for verb in ("done", "blocked", "defer"):
            rc, out, err = _run(ws, env, verb, "t3")
            assert rc == 1, f"V5 ({verb}): expected rc=1, got {rc}"
            assert "current task" in err, \
                f"V5 ({verb}): expected 'current task' in error, got {err!r}"
            assert f"wpl switch t3 && wpl {verb}" in err, \
                f"V5 ({verb}): missing switch-pattern redirect: {err!r}"
        # Backlog should be unpolluted (blocked t3 did not silent-accept).
        rc, out, _ = _run(ws, env, "backlog", "--list", check_exit=0)
        assert "t3" not in out, f"V5: blocked t3 polluted backlog: {out!r}"
        # Sanity: multi-word blocking reason still works.
        # Need an in_progress task; re-init and switch.
        _write_session(profile_a, tasks=[
            {"uid": "v5s-1", "title": "T1", "status": "in_progress",
             "estimate_min": 30, "source": "manual", "started_at": "09:00",
             "briefed_at": "2026-04-27T09:00:00", "brief_rationale": "test fixture"},
        ], current_index=0)
        rc, out, err = _run(ws, env, "blocked", "on", "code", "review",
                            check_exit=0)
        assert "on code review" in (out + err) or "blocked" in out, \
            f"V5 sanity: multi-word reason: out={out!r} err={err!r}"
        print("V5: done/blocked/defer tN redirected [OK]")

        # ── V6: session-hook resolves from cwd (fix #6) ──────────────
        # Write today's session in pb and hit the hook from ws_b.
        # The hook uses `date +%Y-%m-%d` (local wall clock), so the
        # session date must match local today — not UTC today — for the
        # hook's freshness gate to pass. This test doesn't exercise the
        # UTC-vs-local staleness logic; it just checks profile
        # resolution.
        from datetime import date as _local_date
        _write_session(profile_b, tasks=[
            {"uid": "v6b-1", "title": "PB-task", "status": "in_progress",
             "estimate_min": 30, "source": "manual", "started_at": "09:00",
             "briefed_at": "2026-04-27T09:00:00", "brief_rationale": "test fixture"},
        ], session_date=_local_date.today().isoformat(), current_index=0)
        # Ensure wpl wrapper exists — ensure_wrapper() runs on every
        # transition.py call, so the prior _run calls created it.
        wpl_bin = wpl_root / "bin" / "wpl"
        assert wpl_bin.exists(), f"V6: wpl wrapper missing at {wpl_bin}"
        hook_env = dict(os.environ)
        for k in [k for k in hook_env if k.startswith("WPL_")]:
            del hook_env[k]
        hook_env.update(env)
        hook_env["WPL_BIN"] = str(wpl_bin)
        hook_env["WPL_CHILD"] = "1"
        proc = subprocess.run(
            ["bash", str(SESSION_HOOK)],
            cwd=str(ws_b),
            env=hook_env,
            capture_output=True,
            text=True,
        )
        assert "PB-task" in proc.stdout, \
            f"V6: hook output missing pb context: {proc.stdout!r}"
        print("V6: session-hook resolves from cwd [OK]")

        # ── V7: `wpl backlog list` redirects, doesn't pollute ────────
        # Reset backlog to empty.
        (profile_a / "backlog.json").write_text(
            '{"schema_version": 1, "items": []}\n')
        for alias in ("list", "show", "ls"):
            rc, out, err = _run(ws, env, "backlog", alias)
            assert rc == 1, f"V7 ({alias}): expected rc=1, got {rc}"
            assert "not a subcommand" in err, \
                f"V7 ({alias}): missing redirect: {err!r}"
            assert "--list" in err, \
                f"V7 ({alias}): missing --list hint: {err!r}"
        # Backlog must remain empty.
        bl = json.loads((profile_a / "backlog.json").read_text())
        assert bl.get("items") == [], f"V7: backlog got polluted: {bl!r}"
        # Sanity: multi-word title starting with "list" works.
        rc, out, err = _run(ws, env, "backlog", "list", "of", "onboarding",
                            "tasks", check_exit=0)
        bl = json.loads((profile_a / "backlog.json").read_text())
        assert len(bl.get("items", [])) == 1, \
            f"V7 sanity: multi-word title not added: {bl!r}"
        assert bl["items"][0]["title"] == "list of onboarding tasks"
        print("V7: 'wpl backlog list' redirected, multi-word preserved [OK]")

        # ── V8: --profile breadcrumb + JSON fields ───────────────────
        _write_session(profile_b, tasks=[
            {"uid": "v8b-1", "title": "PB-task", "status": "in_progress",
             "estimate_min": 30, "source": "manual", "started_at": "09:00",
             "briefed_at": "2026-04-27T09:00:00", "brief_rationale": "test fixture"},
        ], current_index=0)
        rc, out, err = _run(ws, env, "--profile", "pb", "status", check_exit=0)
        first_line = out.splitlines()[0] if out.splitlines() else ""
        assert "(profile: pb" in first_line and "--profile flag" in first_line, \
            f"V8: breadcrumb missing/wrong: {first_line!r}"
        # Env-var form.
        env_e = dict(env); env_e["WPL_PROFILE"] = "pb"
        rc, out, err = _run(ws, env_e, "status", check_exit=0)
        first_line = out.splitlines()[0]
        assert "$WPL_PROFILE" in first_line, \
            f"V8: env-var breadcrumb wrong: {first_line!r}"
        # JSON status carries fields.
        rc, out, err = _run(ws, env, "--format", "json", "--profile", "pb",
                            "status", check_exit=0)
        payload = json.loads(out)
        assert payload.get("profile_name") == "pb", \
            f"V8: profile_name wrong: {payload!r}"
        assert payload.get("resolved_via") == "cli-flag", \
            f"V8: resolved_via wrong: {payload!r}"
        # JSON mutation carries fields at top level.
        rc, out, err = _run(ws, env, "--format", "json", "--profile", "pb",
                            "switch", "t1", check_exit=0)
        mut = json.loads(out)
        assert mut.get("profile_name") == "pb"
        assert mut.get("resolved_via") == "cli-flag"
        # No breadcrumb when cwd-match resolution (the common case).
        rc, out, err = _run(ws, env, "status", check_exit=0)
        first_line = out.splitlines()[0] if out.splitlines() else ""
        assert not first_line.startswith("(profile:"), \
            f"V8: breadcrumb leaked for cwd-match: {first_line!r}"
        print("V8: --profile breadcrumb and JSON fields [OK]")

        # ── V9: UTC-fallback warning fires once per process ──────────
        _clear_tz(profile_a)
        _write_session(profile_a, tasks=[
            {"uid": "v9-1", "title": "T1", "status": "in_progress",
             "estimate_min": 30, "source": "manual", "started_at": "09:00",
             "briefed_at": "2026-04-27T09:00:00", "brief_rationale": "test fixture"},
        ], current_index=0)
        rc, out, err = _run(ws, env, "status", check_exit=0)
        warn_count = err.count("profile has no 'timezone' set")
        assert warn_count == 1, \
            f"V9: expected exactly 1 UTC warning per process, got {warn_count}: {err!r}"
        # Set timezone; subsequent run is silent.
        _run(ws, env, "config", "set", "timezone", "UTC",
             "--rationale", "test", check_exit=0)
        rc, out, err = _run(ws, env, "status", check_exit=0)
        assert "profile has no 'timezone'" not in err, \
            f"V9: warning leaked after tz was set: {err!r}"
        print("V9: UTC-fallback one-shot warning [OK]")

        print("\nAll V1-V9 scenarios passed.")
        return 0
    finally:
        shutil.rmtree(base, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
