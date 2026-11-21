#!/usr/bin/env python3
"""Build the SessionStart context block for a resolved profile (issue #82).

Called by bin/session-hook.sh with the profile root the CLI resolved, the
SessionStart `source` (startup | resume | clear | compact) and the plugin
root. Prints the block to stdout; prints nothing on error. Stdlib only and
no import of transition.py, so it fits the hook's 5 s budget.

What it delivers, all deterministic:
  - which profile and the current local time in the profile's timezone;
  - the session state: open (task summary), closed (when, and how to log
    post-close work), stale or missing (run /workplanner:start);
  - the authorization scope (profile file, or the default rule);
  - a short methodology pointer, skipped on `resume` (the transcript
    already carries it).
"""
import json
import sys
from datetime import datetime
from pathlib import Path

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None

DEFAULT_AUTHORIZATION = (
    "A yes covers only what it names. Outward-facing or hard-to-reverse actions "
    "(posting, publishing, merging, pushing, writes to shared systems, deleting) "
    "each need their own yes, asked one at a time. A general \"do it\" after a "
    "list covers only the items that were plainly proposed for doing now; when in "
    "doubt, ask. Work outside the profile's workspaces is a context switch: say so first."
)
AUTH_MAX_CHARS = 1500


def _now(cfg):
    tz = cfg.get("timezone")
    if tz and ZoneInfo:
        try:
            return datetime.now(ZoneInfo(tz)), tz
        except Exception:
            pass
    return datetime.now(), "system time"


def _authorization(root, cfg):
    rel = cfg.get("authorization")
    if rel:
        p = root / rel
        try:
            text = p.read_text().strip()
        except OSError:
            text = ""
        if text:
            if len(text) > AUTH_MAX_CHARS:
                text = text[:AUTH_MAX_CHARS].rstrip() + f"\n… (truncated; full text: wpl authorization)"
            return text, "from this profile"
    return DEFAULT_AUTHORIZATION, "default; a profile can set its own with the `authorization` config key"


def _load_config(root):
    """Profile config with the CLI's user.json fallback for timezone/eod_target."""
    try:
        cfg = json.loads((root / "config.json").read_text())
    except (OSError, ValueError):
        cfg = {}
    try:
        user = json.loads((root.resolve().parent.parent / "user.json").read_text())
    except (OSError, ValueError):
        user = {}
    for key in ("timezone", "eod_target"):
        if not cfg.get(key) and user.get(key):
            cfg[key] = user[key]
    return cfg


def build(root, source, plugin_root):
    root = Path(root)
    cfg = _load_config(root)
    now, tz_label = _now(cfg)
    today = now.date().isoformat()
    lines = [f"Workplanner profile: {root.name} | Now: {now.strftime('%a %d %b %Y %H:%M')} ({tz_label})"]

    session_path = root / "session" / "current-session.json"
    try:
        s = json.loads(session_path.read_text())
    except (OSError, ValueError):
        s = None

    if s is None:
        lines.append("  No session yet today. Run /workplanner:start to open the day.")
    elif s.get("date") != today:
        lines.append(f"  The open session is from {s.get('date', '?')}, not today. "
                     f"Run /workplanner:start to close it out and open today.")
    elif s.get("checkpoint") == "closed":
        at = s.get("closed_at") or "EOD"
        written = "the handoff is written" if s.get("eod_handoff_written", True) else "no handoff was written"
        lines.append(f"  Today's session closed at {at}; {written}. Work done now isn't in it. "
                     f"To log post-close work: `wpl session reopen --reason \"...\"`, "
                     f"then update the handoff and `wpl session close`.")
    else:
        tasks = s.get("tasks", [])
        idx = s.get("current_task_index")
        eod = s.get("eod_target", "18:00")
        done = sum(1 for t in tasks if t.get("status") == "done")
        remaining = sum(t.get("estimate_min", 0) or 0 for t in tasks
                        if t.get("status") in ("pending", "in_progress", "blocked"))
        lines.append("  Active workplan for today:")
        if idx is not None and 0 <= idx < len(tasks):
            t = tasks[idx]
            tag = " (dispatched)" if t.get("dispatched") else ""
            lines.append(f"  Current: t{idx + 1} — {t.get('title')} (~{t.get('estimate_min', '?')}m){tag}")
        lines.append(f"  Progress: {done}/{len(tasks)} done | ~{remaining}m remaining | EOD: {eod}")
        pending = [f"t{i + 1}" for i, t in enumerate(tasks) if t.get("status") == "pending"]
        if pending:
            lines.append(f"  Pending: {', '.join(pending[:5])}")
        others = [f"t{i + 1}" for i, t in enumerate(tasks) if t.get("dispatched") and i != idx]
        if others:
            lines.append(f"  Dispatched (other sessions): {', '.join(others)}")
    lines.append("  Use `wpl` (e.g. wpl done, wpl status); if it is not found, call ~/.workplanner/bin/wpl.")

    auth, auth_src = _authorization(root, cfg)
    lines.append("")
    lines.append(f"Authorization ({auth_src}):")
    lines.extend("  " + l if l.strip() else "" for l in auth.splitlines())

    if source != "resume":
        mpath = (plugin_root.rstrip("/") + "/docs/methodology.md") if plugin_root \
            else "docs/methodology.md (in the workplanner plugin)"
        lines.append("")
        lines.append(f"Methodology: for work-shape questions (prioritization, triage, carryover, deferral, "
                     f"budget, scoping, rhythm), read {mpath} and apply its seven principles by name; "
                     f"if you deviate from a principle, name it and say why. Profile rule overrides for a "
                     f"skill: `wpl addenda --for <skill>`.")
    return "\n".join(lines)


def main():
    root = sys.argv[1] if len(sys.argv) > 1 else ""
    source = sys.argv[2] if len(sys.argv) > 2 else "startup"
    plugin_root = sys.argv[3] if len(sys.argv) > 3 else ""
    if not root:
        return
    try:
        print(build(root, source, plugin_root))
    except Exception:
        pass


if __name__ == "__main__":
    main()
