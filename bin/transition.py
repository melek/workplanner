#!/usr/bin/env python3
"""Deterministic state mutations for workplanner.

All commands: read session -> validate -> mutate -> atomic write -> render -> print confirmation.
Exit 0 on success, 1 on precondition failure.

Python 3.9+ stdlib only.
"""

import argparse
import json
import os
import subprocess
import sys
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

WPL_ROOT = Path.home() / ".workplanner"


def resolve_profile_root():
    """Resolve the active profile directory via symlink."""
    active = WPL_ROOT / "profiles" / "active"
    if active.is_symlink():
        resolved = active.resolve()
        if resolved.is_dir():
            return resolved
        # Broken symlink — fall through to single-profile fallback
    elif active.is_dir():
        return active
    # Fallback: if no active symlink, check for a single profile
    profiles_dir = WPL_ROOT / "profiles"
    if profiles_dir.is_dir():
        candidates = [d for d in profiles_dir.iterdir()
                      if d.is_dir() and d.name != "active"]
        if len(candidates) == 1:
            return candidates[0]
    fail(f"No active profile. Run /workplanner:start to set up, "
         f"or create one with: wpl profile create <name>")


def resolve_paths():
    """Return a namespace of resolved paths for the active profile."""
    root = resolve_profile_root()
    class P:
        PROFILE_ROOT = root
        SESSION = root / "session" / "current-session.json"
        CONFIG = root / "config.json"
        BACKLOG = root / "backlog.json"
        UNDO_LOG = root / "undo.jsonl"
        ARCHIVE_DIR = root / "session" / "agendas" / "archive"
        DASHBOARD = root / "session" / "dashboard-view.txt"
        EVENTS = root / "session" / "events.json"
        AGENDAS = root / "session" / "agendas"
        BRIEFINGS = root / "briefings"
    return P


USER_JSON = WPL_ROOT / "user.json"
DECISION_LOG = WPL_ROOT / "decision-log.json"
RENDER = Path(__file__).resolve().parent / "render_dashboard.py"
MAX_UNDO = 20


# ── Utilities ────────────────────────────────────────────────────────


def generate_uid():
    """8-char stable UID for a task."""
    return uuid.uuid4().hex[:8]


def backfill_uids(session):
    """Ensure all tasks have a uid (migration safety)."""
    changed = False
    for task in session.get("tasks", []):
        if "uid" not in task:
            task["uid"] = generate_uid()
            changed = True
    return changed


def load_user_json():
    """Load user.json, returning empty dict on failure."""
    try:
        with open(USER_JSON) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def load_config():
    """Load profile config with user.json fallback for timezone/eod_target."""
    paths = resolve_paths()
    try:
        with open(paths.CONFIG) as f:
            config = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        config = {}
    user = load_user_json()
    for key in ("timezone", "eod_target"):
        if key not in config and key in user:
            config[key] = user[key]
    return config


def load_decision_log():
    """Load decision-log.json, returning empty list on failure."""
    try:
        with open(DECISION_LOG) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def save_decision_log(log):
    """Atomic write of decision log."""
    DECISION_LOG.parent.mkdir(parents=True, exist_ok=True)
    tmp = str(DECISION_LOG) + ".tmp"
    with open(tmp, "w") as f:
        json.dump(log, f, indent=2)
        f.write("\n")
    os.rename(tmp, str(DECISION_LOG))


METHODOLOGY_DEFAULTS = {
    "triage.filter.task_cap": 10,
    "triage.deferrals.reckoning_threshold": 3,
    "triage.source_priority.carryover": "medium",
    "triage.source_priority.linear-p1": "critical",
    "triage.source_priority.linear-p2": "high",
    "triage.source_priority.slack-ping": "high",
    "triage.estimates.slack-ping": 5,
    "triage.estimates.github": 15,
    "triage.estimates.linear-high": 30,
    "triage.estimates.linear-low": 15,
    "triage.estimates.manual": 30,
    "triage.estimates.focus": 30,
    "triage.pre_work.min_minutes_for_task": 5,
    "eod_target": "18:00",
}


def _config_get_nested(config, key):
    """Get a dotted key from nested dict. E.g., 'triage.filter.task_cap'."""
    parts = key.split(".")
    val = config
    for p in parts:
        if isinstance(val, dict):
            val = val.get(p)
        else:
            return None
    return val


def _config_set_nested(config, key, value):
    """Set a dotted key in nested dict, creating intermediate dicts."""
    parts = key.split(".")
    d = config
    for p in parts[:-1]:
        if p not in d or not isinstance(d[p], dict):
            d[p] = {}
        d = d[p]
    # Try to parse value as JSON for proper typing
    try:
        d[parts[-1]] = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        d[parts[-1]] = value


def local_today(config=None):
    """Return today's date in the configured timezone."""
    tz_name = (config or {}).get("timezone", "UTC")
    return datetime.now(ZoneInfo(tz_name)).date()


def load_session():
    paths = resolve_paths()
    try:
        with open(paths.SESSION) as f:
            session = json.load(f)
    except FileNotFoundError:
        fail("No session file found.")
    except json.JSONDecodeError as e:
        fail(f"Session JSON parse error: {e}")
    if backfill_uids(session):
        save_session(session, undo=False)
    return session


def save_undo(session):
    """Append current state to undo log before mutation."""
    paths = resolve_paths()
    try:
        entry = json.dumps({"ts": datetime.now().isoformat(), "state": session})
        with open(paths.UNDO_LOG, "a") as f:
            f.write(entry + "\n")
        # Truncate to MAX_UNDO entries
        lines = paths.UNDO_LOG.read_text().splitlines()
        if len(lines) > MAX_UNDO:
            paths.UNDO_LOG.write_text("\n".join(lines[-MAX_UNDO:]) + "\n")
    except OSError:
        pass  # non-fatal


def save_session(session, undo=True):
    """Atomic write: tmp file then mv. Resolves symlinks to avoid replacing them."""
    paths = resolve_paths()
    if undo:
        try:
            with open(paths.SESSION) as f:
                old = json.load(f)
            save_undo(old)
        except (FileNotFoundError, json.JSONDecodeError):
            pass
    paths.SESSION.parent.mkdir(parents=True, exist_ok=True)
    target = paths.SESSION.resolve()
    tmp = str(target) + ".tmp"
    with open(tmp, "w") as f:
        json.dump(session, f, indent=2)
        f.write("\n")
    os.rename(tmp, str(target))


def render():
    """Re-render dashboard after mutation."""
    subprocess.run([sys.executable, str(RENDER)], capture_output=True)


def fail(msg):
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)


def now_hhmm():
    return datetime.now().strftime("%H:%M")


def current_task(session):
    """Return (index, task_dict) for the current task, or (None, None)."""
    idx = session.get("current_task_index")
    tasks = session.get("tasks", [])
    if idx is None or idx < 0 or idx >= len(tasks):
        return None, None
    return idx, tasks[idx]



def elapsed_minutes(started_at):
    """Wall-clock minutes from HH:MM to now."""
    if not started_at:
        return 0
    try:
        now = datetime.now()
        parts = started_at.split(":")
        start = now.replace(hour=int(parts[0]), minute=int(parts[1]), second=0, microsecond=0)
        return max(0, int((now - start).total_seconds() / 60))
    except Exception:
        return 0


def tid(index):
    """Display ID from 0-based index."""
    return f"t{index + 1}"


def parse_task_target(raw, session):
    """Parse a task target string. Accepts 't3' (display ID), '2' (0-based index),
    or an 8-char uid. Returns (0-based index, task dict)."""
    tasks = session.get("tasks", [])
    if raw.startswith("t") and raw[1:].isdigit():
        target = int(raw[1:]) - 1
    elif raw.isdigit():
        target = int(raw)
    else:
        # Try uid lookup
        for i, t in enumerate(tasks):
            if t.get("uid") == raw:
                return i, t
        fail(f"Invalid task target: {raw}")
        return None, None  # unreachable

    if target < 0 or target >= len(tasks):
        fail(f"Index {target} out of range (0-{len(tasks) - 1}).")
    return target, tasks[target]


def parse_position(raw, num_tasks):
    """Parse a position string for --at/--to. Accepts 't3', '2', 'top', 'end'.
    Returns a 0-based insertion index (may equal num_tasks for end)."""
    if raw == "top":
        return 0
    if raw == "end":
        return num_tasks
    if raw.startswith("t") and raw[1:].isdigit():
        return int(raw[1:]) - 1
    if raw.isdigit():
        return int(raw)
    fail(f"Invalid position: {raw}")
    return 0  # unreachable


def hhmm_diff(start_hhmm, end_hhmm):
    """Minutes between two HH:MM strings."""
    sh, sm = map(int, start_hhmm.split(":"))
    eh, em = map(int, end_hhmm.split(":"))
    return max(0, (eh * 60 + em) - (sh * 60 + sm))


def hhmm_subtract(hhmm, minutes):
    """Subtract minutes from an HH:MM string, returning HH:MM."""
    h, m = map(int, hhmm.split(":"))
    total = max(0, h * 60 + m - minutes)
    return f"{total // 60:02d}:{total % 60:02d}"


def fmt_duration(mins):
    """Format minutes as Xm or XhYm."""
    if mins is None:
        return ""
    mins = int(mins)
    if mins >= 60:
        h = mins // 60
        m = mins % 60
        return f"{h}h{m:02d}m" if m else f"{h}h"
    return f"{mins}m"


def load_backlog():
    """Load backlog.json, creating it if missing."""
    paths = resolve_paths()
    try:
        with open(paths.BACKLOG) as f:
            return json.load(f)
    except FileNotFoundError:
        return {"schema_version": 1, "last_reviewed": None, "items": []}
    except json.JSONDecodeError:
        return {"schema_version": 1, "last_reviewed": None, "items": []}


def save_backlog(backlog, undo=True):
    """Atomic write for backlog.json."""
    paths = resolve_paths()
    if undo:
        try:
            with open(paths.BACKLOG) as f:
                old = json.load(f)
            entry = json.dumps({"ts": datetime.now().isoformat(), "type": "backlog", "state": old})
            with open(paths.UNDO_LOG, "a") as f:
                f.write(entry + "\n")
            lines = paths.UNDO_LOG.read_text().splitlines()
            if len(lines) > MAX_UNDO:
                paths.UNDO_LOG.write_text("\n".join(lines[-MAX_UNDO:]) + "\n")
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            pass
    paths.BACKLOG.parent.mkdir(parents=True, exist_ok=True)
    target = paths.BACKLOG.resolve() if paths.BACKLOG.exists() else paths.BACKLOG
    tmp = str(target) + ".tmp"
    with open(tmp, "w") as f:
        json.dump(backlog, f, indent=2)
        f.write("\n")
    os.rename(tmp, str(target))


def parse_relative_date(raw, today=None):
    """Parse a date string: YYYY-MM-DD, 'tomorrow', weekday name, 'next-week'.
    Returns a date object or None."""
    if today is None:
        today = local_today(load_config())
    raw = raw.strip().lower()
    if raw == "tomorrow":
        return today + timedelta(days=1)
    if raw == "next-week":
        # Next Monday
        days_ahead = 7 - today.weekday()
        return today + timedelta(days=days_ahead)
    weekdays = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
                "friday": 4, "saturday": 5, "sunday": 6}
    if raw in weekdays:
        target_day = weekdays[raw]
        days_ahead = (target_day - today.weekday()) % 7
        if days_ahead == 0:
            days_ahead = 7  # next occurrence, not today
        return today + timedelta(days=days_ahead)
    # Try ISO date
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError:
        return None


def backlog_count():
    """Return number of items in the backlog (for dashboard)."""
    bl = load_backlog()
    return len(bl.get("items", []))


def remaining_summary(session):
    """Return string like '~3h left' for remaining estimate."""
    tasks = session.get("tasks", [])
    remaining = sum(
        t.get("estimate_min", 0) or 0
        for t in tasks
        if t.get("status") in ("pending", "in_progress", "blocked")
    )
    return f"~{fmt_duration(remaining)} left"


def status_line(session, config=None):
    """Build the one-line status string."""
    tasks = session.get("tasks", [])
    idx, task = current_task(session)
    eod = session.get("eod_target", "18:00")

    done_count = sum(1 for t in tasks if t["status"] == "done")
    total_count = len(tasks)

    remaining = sum(
        t.get("estimate_min", 0) or 0
        for t in tasks
        if t.get("status") in ("pending", "in_progress", "blocked")
    )

    # Timezone-aware date for display
    today = local_today(config)
    date_str = today.strftime("%a %b %d")
    tz_name = (config or {}).get("timezone", "")
    tz_tag = f" ({tz_name})" if tz_name else ""

    if task:
        est = task.get("estimate_min")
        est_str = f" (~{est}m)" if est else ""
        dispatch_str = " (dispatched)" if task.get("dispatched") else ""
        return (
            f"\u25b6 {tid(idx)} \u2014 {task['title']}{est_str}{dispatch_str}"
            f" | {date_str}{tz_tag}"
            f" | Done: {done_count}/{total_count}"
            f" | ~{fmt_duration(remaining)} left"
            f" | EOD: {eod}"
        )
    else:
        return (
            f"All tasks complete"
            f" | {date_str}{tz_tag}"
            f" | Done: {done_count}/{total_count}"
            f" | EOD: {eod}"
        )


# ── Commands ─────────────────────────────────────────────────────────


def cmd_done(args):
    session = load_session()
    idx, task = current_task(session)
    if task is None:
        fail("No current task.")
    if task["status"] != "in_progress":
        fail(f"{tid(idx)} is {task['status']}, not in_progress.")

    if task.get("dispatched"):
        print(f"warning: {tid(idx)} is dispatched to another session.", file=sys.stderr)

    actual = elapsed_minutes(task.get("started_at"))
    task["status"] = "done"
    task["finished_at"] = now_hhmm()
    task["actual_min"] = actual
    task.pop("dispatched", None)

    session["current_task_index"] = None

    save_session(session)
    render()

    est = task.get("estimate_min", 0)
    print(f"\u2713 {tid(idx)} done ({actual}m/{est}m est). [{remaining_summary(session)}]")


def cmd_blocked(args):
    session = load_session()
    idx, task = current_task(session)
    if task is None:
        fail("No current task.")
    if task["status"] != "in_progress":
        fail(f"{tid(idx)} is {task['status']}, not in_progress.")

    task["status"] = "blocked"
    reason = " ".join(args.reason) if args.reason else None
    if reason:
        existing = task.get("notes") or ""
        sep = " " if existing else ""
        task["notes"] = f"{existing}{sep}Blocked: {reason}".strip()

    session["current_task_index"] = None

    save_session(session)
    render()

    line = f"\u2298 {tid(idx)} blocked."
    if reason:
        line += f" ({reason})"
    line += f" [{remaining_summary(session)}]"
    print(line)


def cmd_defer(args):
    session = load_session()
    idx, task = current_task(session)
    if task is None:
        fail("No current task.")
    if task["status"] not in ("in_progress", "pending"):
        fail(f"{tid(idx)} is {task['status']}, can't defer.")

    until = getattr(args, "until", None)
    if until:
        # Defer to backlog with target_date
        target_date = parse_relative_date(until)
        if target_date is None:
            fail(f"Can't parse date: {until}. Use YYYY-MM-DD, tomorrow, monday-sunday, or next-week.")
        _move_task_to_backlog(session, idx, target_date=target_date.isoformat())
        return

    # Track deferral count
    count = task.get("deferral_count", 0) + 1
    task["deferral_count"] = count

    # Check reckoning threshold
    config = load_config()
    threshold = (config.get("triage", {})
                 .get("deferrals", {})
                 .get("reckoning_threshold", 3))
    if count >= threshold:
        print(f"\u26a0 {tid(idx)} \"{task['title']}\" has been deferred {count} times.")
        print(f"  What's actually going on?")
        print(f"  [b] Break it down into smaller tasks")
        print(f"  [d] Delegate \u2014 reassign or ask for help")
        print(f"  [x] Drop it \u2014 it's not going to happen")
        print(f"  [t] Timebox \u2014 schedule a dedicated block (sends to backlog with target date)")
        print(f"  [k] Keep deferring \u2014 I'll get to it")
        # Save the incremented count but don't change status yet
        save_session(session, undo=False)
        sys.exit(2)  # Signal to calling skill that reckoning is needed

    was_current = (idx == session.get("current_task_index"))
    task["status"] = "deferred"

    if was_current:
        session["current_task_index"] = None

    save_session(session)
    render()

    print(f"\u21b7 {tid(idx)} deferred ({count}x). [{remaining_summary(session)}]")


def cmd_add(args):
    session = load_session()
    title = " ".join(args.title)
    if not title:
        fail("Task title is required.")

    est = args.est
    tasks = session.get("tasks", [])
    cur_idx = session.get("current_task_index")

    new_task = {
        "uid": generate_uid(),
        "title": title,
        "estimate_min": est,
        "source": args.source or "manual",
        "status": "pending",
    }

    # Optional metadata
    if args.ref:
        new_task["ref"] = args.ref
    if args.url:
        new_task["url"] = args.url
    if args.notes:
        new_task["notes"] = args.notes

    # Resolve parent task
    parent_idx = None
    if args.parent:
        parent_idx, _parent_task = parse_task_target(args.parent, session)

    # Handle --done: mark as completed immediately
    if args.done:
        finished = args.finished or now_hhmm()
        started = args.started or hhmm_subtract(finished, est)
        new_task["status"] = "done"
        new_task["started_at"] = started
        new_task["finished_at"] = finished
        new_task["actual_min"] = hhmm_diff(started, finished)

    # Determine insertion position
    if args.at is not None:
        insert_pos = parse_position(args.at, len(tasks))
        insert_pos = max(0, min(insert_pos, len(tasks)))
    elif parent_idx is not None:
        # Insert after parent's last existing child
        insert_pos = parent_idx + 1
        for i in range(parent_idx + 1, len(tasks)):
            if tasks[i].get("parent") == parent_idx:
                insert_pos = i + 1
    elif cur_idx is not None and 0 <= cur_idx < len(tasks):
        insert_pos = cur_idx + 1
    else:
        insert_pos = len(tasks)

    # Adjust existing parent indices that will shift due to insertion
    for t in tasks[insert_pos:]:
        if "parent" in t and t["parent"] >= insert_pos:
            t["parent"] += 1

    tasks.insert(insert_pos, new_task)

    # Set parent field (using post-insertion index)
    if parent_idx is not None:
        post_parent_idx = parent_idx if parent_idx < insert_pos else parent_idx + 1
        new_task["parent"] = post_parent_idx

    # Adjust current_task_index if we inserted at or before it
    if cur_idx is not None and insert_pos <= cur_idx:
        session["current_task_index"] = cur_idx + 1

    save_session(session)
    render()

    status_tag = " (done)" if args.done else ""
    parent_tag = f" (child of {tid(new_task['parent'])})" if "parent" in new_task else ""
    print(f"+ {tid(insert_pos)} added: \"{title}\" (~{est}m){status_tag}{parent_tag}. [{remaining_summary(session)}]")


def cmd_switch(args):
    session = load_session()
    target_str = args.target
    target, task = parse_task_target(target_str, session)

    prev_idx = session.get("current_task_index")

    # Auto-pause previous task (unless --no-pause)
    paused_tid = None
    if not args.no_pause and prev_idx is not None and prev_idx != target:
        prev_task = session["tasks"][prev_idx]
        if prev_task.get("status") == "in_progress":
            prev_task["status"] = "pending"
            paused_tid = tid(prev_idx)

    if task["status"] == "pending":
        task["status"] = "in_progress"
        task["started_at"] = now_hhmm()

    # Reclaim: switching to a dispatched task without --no-pause clears the flag
    if not args.no_pause and task.get("dispatched"):
        task.pop("dispatched", None)

    session["current_task_index"] = target

    save_session(session)
    render()

    line = f"\u25b6 {tid(target)} \u2014 {task['title']}"
    if task.get("estimate_min"):
        line += f" (~{task['estimate_min']}m)"
    if task.get("dispatched"):
        line += " (dispatched)"
    if paused_tid:
        line += f". {paused_tid} paused."
    elif prev_idx is not None and prev_idx != target:
        prev_task = session["tasks"][prev_idx]
        if prev_task.get("status") == "in_progress":
            line += f". {tid(prev_idx)} still in_progress (--no-pause)."
    print(line)


def cmd_move(args):
    session = load_session()
    source, task = parse_task_target(args.source, session)
    tasks = session.get("tasks", [])
    cur_idx = session.get("current_task_index")

    dest = parse_position(args.to, len(tasks))
    dest = max(0, min(dest, len(tasks)))

    if source == dest:
        print(f"{tid(source)} is already at that position.")
        return

    # Pop from source, then insert at dest (the desired final position).
    # No dest adjustment needed — parse_position returns the target index
    # the user wants the task to land at.
    moved = tasks.pop(source)
    dest = min(dest, len(tasks))
    tasks.insert(dest, moved)

    # Adjust current_task_index
    if cur_idx is not None:
        if cur_idx == source:
            # Moved the current task
            session["current_task_index"] = dest
        else:
            new_idx = cur_idx
            # Removal shift
            if source < cur_idx:
                new_idx -= 1
            # Insertion shift
            if dest <= new_idx:
                new_idx += 1
            session["current_task_index"] = new_idx

    save_session(session)
    render()

    print(f"\u2194 {tid(dest)} \u2014 {moved['title']} (moved from {tid(source)})")


def cmd_remove(args):
    """Remove a task entirely from the session."""
    session = load_session()
    target, task = parse_task_target(args.target, session)
    tasks = session.get("tasks", [])
    cur_idx = session.get("current_task_index")

    title = task["title"]
    tasks.pop(target)

    # Adjust current_task_index
    if cur_idx is not None:
        if cur_idx == target:
            # Removed the current task — don't auto-advance
            session["current_task_index"] = None
        elif cur_idx > target:
            session["current_task_index"] = cur_idx - 1

    save_session(session)
    render()

    line = f"\u2716 {tid(target)} removed: \"{title}\". [{remaining_summary(session)}]"
    print(line)


def cmd_dispatch(args):
    """Mark a task as dispatched (being worked on in another session)."""
    session = load_session()
    target, task = parse_task_target(args.target, session)

    if task["status"] == "pending":
        task["status"] = "in_progress"
        task["started_at"] = now_hhmm()
    elif task["status"] != "in_progress":
        fail(f"{tid(target)} is {task['status']}, can't dispatch.")

    task["dispatched"] = True

    save_session(session)
    render()

    print(f"\u21c4 {tid(target)} dispatched \u2014 {task['title']}")


def _move_task_to_backlog(session, idx, target_date=None, not_before=None, deadline=None, tags=None):
    """Move a session task to the backlog. Handles session state cleanup."""
    tasks = session.get("tasks", [])
    task = tasks[idx]
    title = task["title"]

    backlog = load_backlog()
    item = {
        "uid": task.get("uid") or generate_uid(),
        "title": title,
        "estimate_min": task.get("estimate_min", 30),
        "source": task.get("source", "manual"),
        "status": "backlog",
        "ref": task.get("ref"),
        "url": task.get("url"),
        "notes": task.get("notes"),
        "created_at": local_today(load_config()).isoformat(),
        "target_date": target_date,
        "not_before": not_before,
        "deadline": deadline,
        "tags": tags or [],
    }
    backlog["items"].append(item)

    # Remove from session
    cur_idx = session.get("current_task_index")
    tasks.pop(idx)
    if cur_idx is not None:
        if cur_idx == idx:
            session["current_task_index"] = None
        elif cur_idx > idx:
            session["current_task_index"] = cur_idx - 1

    # Save both (session undo captures session state, backlog saves separately)
    save_session(session)
    save_backlog(backlog)
    render()

    date_tag = f" (target: {target_date})" if target_date else ""
    deadline_tag = f" (deadline: {deadline})" if deadline else ""
    print(f"\u21b3 {tid(idx)} \u2192 backlog: \"{title}\"{date_tag}{deadline_tag}. [{remaining_summary(session)}]")


def cmd_backlog(args):
    """Manage the backlog: add, move from session, list, promote, drop, edit."""
    # Route based on flags
    if args.list_items:
        _backlog_list(args)
        return
    if args.promote:
        _backlog_promote(args)
        return
    if args.drop:
        _backlog_drop(args)
        return
    if args.edit:
        _backlog_edit(args)
        return
    if args.from_task or args.from_current:
        _backlog_from_session(args)
        return
    # Default: add new item
    _backlog_add(args)


def _backlog_add(args):
    title = " ".join(args.title) if args.title else None
    if not title:
        fail("Title is required. Usage: backlog \"task title\" [--est N] [--target DATE]")

    target_date = None
    if args.target:
        d = parse_relative_date(args.target)
        if d is None:
            fail(f"Can't parse target date: {args.target}")
        target_date = d.isoformat()

    not_before = None
    if args.not_before:
        d = parse_relative_date(args.not_before)
        if d is None:
            fail(f"Can't parse not-before date: {args.not_before}")
        not_before = d.isoformat()

    bl_deadline = None
    if args.deadline:
        d = parse_relative_date(args.deadline)
        if d is None:
            fail(f"Can't parse deadline: {args.deadline}")
        bl_deadline = d.isoformat()

    tags = args.tag or []

    backlog = load_backlog()
    item = {
        "uid": generate_uid(),
        "title": title,
        "estimate_min": args.est,
        "source": "manual",
        "status": "backlog",
        "ref": args.ref,
        "url": args.url,
        "notes": args.notes,
        "created_at": local_today(load_config()).isoformat(),
        "target_date": target_date,
        "not_before": not_before,
        "deadline": bl_deadline,
        "tags": tags,
    }
    backlog["items"].append(item)
    save_backlog(backlog)

    date_info = []
    if target_date:
        date_info.append(f"target: {target_date}")
    if bl_deadline:
        date_info.append(f"deadline: {bl_deadline}")
    date_str = f" ({', '.join(date_info)})" if date_info else ""
    print(f"+ backlog: \"{title}\" (~{args.est}m){date_str}. [backlog: {len(backlog['items'])} items]")


def _backlog_from_session(args):
    session = load_session()
    if args.from_current:
        idx, task = current_task(session)
        if task is None:
            fail("No current task.")
    else:
        idx, task = parse_task_target(args.from_task, session)

    target_date = None
    if args.target:
        d = parse_relative_date(args.target)
        if d is None:
            fail(f"Can't parse target date: {args.target}")
        target_date = d.isoformat()

    bl_deadline = None
    if args.deadline:
        d = parse_relative_date(args.deadline)
        if d is None:
            fail(f"Can't parse deadline: {args.deadline}")
        bl_deadline = d.isoformat()

    tags = args.tag or []
    _move_task_to_backlog(session, idx, target_date=target_date, deadline=bl_deadline, tags=tags)


def _backlog_list(args):
    backlog = load_backlog()
    items = backlog.get("items", [])
    tag_filter = args.tag[0] if args.tag else None
    if tag_filter:
        items = [i for i in items if tag_filter in (i.get("tags") or [])]

    if not items:
        print("Backlog is empty." if not tag_filter else f"No backlog items with tag '{tag_filter}'.")
        return

    today = local_today(load_config())

    print(f"Backlog: {len(items)} items")
    print()
    for item in items:
        uid = item.get("uid", "?")[:8]
        title = item.get("title", "?")
        est = item.get("estimate_min")
        est_str = f"~{est}m" if est else ""

        # Date info
        parts = []
        target = item.get("target_date")
        deadline = item.get("deadline")
        created = item.get("created_at")
        if target:
            parts.append(f"target: {target}")
        if deadline:
            try:
                dl = datetime.strptime(deadline, "%Y-%m-%d").date()
                days = (dl - today).days
                if days < 0:
                    parts.append(f"deadline: {deadline} (OVERDUE)")
                elif days <= 2:
                    parts.append(f"deadline: {deadline} (due soon!)")
                elif days <= 6:
                    parts.append(f"deadline: {deadline} (due soon)")
                else:
                    parts.append(f"deadline: {deadline}")
            except ValueError:
                parts.append(f"deadline: {deadline}")
        if created:
            try:
                cd = datetime.strptime(created, "%Y-%m-%d").date()
                age = (today - cd).days
                if age >= 30:
                    parts.append(f"stale \u2014 {age}d, consider dropping")
                elif age >= 14:
                    parts.append(f"stale \u2014 {age}d")
            except ValueError:
                pass

        tags = item.get("tags") or []
        tag_str = f" [{', '.join(tags)}]" if tags else ""
        date_str = f"  ({'; '.join(parts)})" if parts else ""
        ref = item.get("ref")
        ref_str = f" [{ref}]" if ref else ""

        print(f"  {uid}  {title}{ref_str}  {est_str}{tag_str}{date_str}")


def _backlog_promote(args):
    backlog = load_backlog()
    items = backlog.get("items", [])
    uid = args.promote
    found = None
    for i, item in enumerate(items):
        if item.get("uid", "").startswith(uid):
            found = i
            break
    if found is None:
        fail(f"No backlog item matching uid '{uid}'.")

    item = items.pop(found)
    save_backlog(backlog)

    # Add to session
    session = load_session()
    new_task = {
        "uid": item.get("uid") or generate_uid(),
        "title": item["title"],
        "estimate_min": item.get("estimate_min", 30),
        "source": item.get("source", "backlog"),
        "status": "pending",
    }
    if item.get("ref"):
        new_task["ref"] = item["ref"]
    if item.get("url"):
        new_task["url"] = item["url"]
    if item.get("notes"):
        new_task["notes"] = item["notes"]

    tasks = session.get("tasks", [])
    cur_idx = session.get("current_task_index")
    if cur_idx is not None and 0 <= cur_idx < len(tasks):
        insert_pos = cur_idx + 1
    else:
        insert_pos = len(tasks)
    tasks.insert(insert_pos, new_task)

    if cur_idx is not None and insert_pos <= cur_idx:
        session["current_task_index"] = cur_idx + 1

    save_session(session)
    render()

    print(f"\u2191 {tid(insert_pos)} promoted from backlog: \"{item['title']}\". [{remaining_summary(session)}]")


def _backlog_drop(args):
    backlog = load_backlog()
    items = backlog.get("items", [])
    uid = args.drop
    found = None
    for i, item in enumerate(items):
        if item.get("uid", "").startswith(uid):
            found = i
            break
    if found is None:
        fail(f"No backlog item matching uid '{uid}'.")

    dropped = items.pop(found)
    save_backlog(backlog)
    print(f"\u2716 dropped from backlog: \"{dropped['title']}\". [backlog: {len(items)} items]")


def _backlog_edit(args):
    backlog = load_backlog()
    items = backlog.get("items", [])
    uid = args.edit
    found = None
    for item in items:
        if item.get("uid", "").startswith(uid):
            found = item
            break
    if found is None:
        fail(f"No backlog item matching uid '{uid}'.")

    changed = []
    if args.target:
        d = parse_relative_date(args.target)
        if d is None:
            fail(f"Can't parse target date: {args.target}")
        found["target_date"] = d.isoformat()
        changed.append(f"target: {d.isoformat()}")
    if args.not_before:
        d = parse_relative_date(args.not_before)
        if d is None:
            fail(f"Can't parse not-before date: {args.not_before}")
        found["not_before"] = d.isoformat()
        changed.append(f"not-before: {d.isoformat()}")
    if args.deadline:
        d = parse_relative_date(args.deadline)
        if d is None:
            fail(f"Can't parse deadline: {args.deadline}")
        found["deadline"] = d.isoformat()
        changed.append(f"deadline: {d.isoformat()}")
    if args.tag:
        found["tags"] = args.tag
        changed.append(f"tags: {args.tag}")
    if args.notes:
        found["notes"] = args.notes
        changed.append("notes updated")
    if args.est:
        found["estimate_min"] = args.est
        changed.append(f"est: {args.est}m")

    if not changed:
        fail("Nothing to edit. Use --target, --deadline, --not-before, --tag, --notes, or --est.")

    save_backlog(backlog)
    print(f"\u270e backlog \"{found['title']}\": {', '.join(changed)}")


def cmd_reckon(args):
    """Apply a reckoning decision to the current task after deferral threshold."""
    session = load_session()
    idx, task = current_task(session)
    if task is None:
        fail("No current task.")

    choice = args.choice.lower()
    if choice in ("k", "keep"):
        # Force defer despite threshold
        was_current = (idx == session.get("current_task_index"))
        task["status"] = "deferred"
        if was_current:
            session["current_task_index"] = None
        save_session(session)
        render()
        count = task.get("deferral_count", 0)
        print(f"\u21b7 {tid(idx)} deferred ({count}x, keeping). [{remaining_summary(session)}]")

    elif choice in ("x", "drop"):
        title = task["title"]
        tasks = session.get("tasks", [])
        tasks.pop(idx)
        cur_idx = session.get("current_task_index")
        if cur_idx is not None:
            if cur_idx == idx:
                session["current_task_index"] = None
            elif cur_idx > idx:
                session["current_task_index"] = cur_idx - 1
        save_session(session)
        render()
        print(f"\u2716 {tid(idx)} dropped: \"{title}\". [{remaining_summary(session)}]")

    elif choice in ("t", "timebox"):
        target = args.date
        if not target:
            fail("Timebox requires a date. Usage: reckon t --date friday")
        target_date = parse_relative_date(target)
        if target_date is None:
            fail(f"Can't parse date: {target}")
        _move_task_to_backlog(session, idx, target_date=target_date.isoformat())

    elif choice in ("b", "break"):
        # Just defer — the skill will handle the decomposition interactively
        was_current = (idx == session.get("current_task_index"))
        task["status"] = "deferred"
        if was_current:
            session["current_task_index"] = None
        save_session(session)
        render()
        print(f"\u21b7 {tid(idx)} deferred for decomposition. [{remaining_summary(session)}]")

    elif choice in ("d", "delegate"):
        was_current = (idx == session.get("current_task_index"))
        task["status"] = "deferred"
        if was_current:
            session["current_task_index"] = None
        existing = task.get("notes") or ""
        sep = " " if existing else ""
        task["notes"] = f"{existing}{sep}Reckoning: delegate/reassign.".strip()
        save_session(session)
        render()
        print(f"\u21b7 {tid(idx)} deferred (delegate/reassign). [{remaining_summary(session)}]")

    else:
        fail(f"Unknown reckoning choice: {choice}. Use b/d/x/t/k.")


def cmd_status(args):
    session = load_session()
    config = load_config()
    print(status_line(session, config))


def cmd_undo(args):
    """Restore the previous state from the undo log (session or backlog)."""
    paths = resolve_paths()
    if not paths.UNDO_LOG.exists():
        fail("No undo history.")
    lines = paths.UNDO_LOG.read_text().splitlines()
    lines = [l for l in lines if l.strip()]
    if not lines:
        fail("No undo history.")
    last = json.loads(lines[-1])
    state = last["state"]
    entry_type = last.get("type", "session")

    if entry_type == "backlog":
        save_backlog(state, undo=False)
        target_str = "backlog"
    else:
        save_session(state, undo=False)
        render()
        target_str = "session"

    # Remove the consumed entry
    paths.UNDO_LOG.write_text("\n".join(lines[:-1]) + "\n" if lines[:-1] else "")
    print(f"\u21a9 Undone ({target_str}). Restored state from {last['ts'][:19]}.")


def cmd_history(args):
    """Query completed tasks across archived sessions."""
    paths = resolve_paths()
    if not paths.ARCHIVE_DIR.exists():
        fail(f"No archive directory at {paths.ARCHIVE_DIR}")
    days = args.days
    ref_filter = args.ref
    source_filter = args.source
    status_filter = args.status or "done"

    files = sorted(paths.ARCHIVE_DIR.glob("*.json"), reverse=True)
    if days:
        files = files[:days]

    found = 0
    for f in files:
        try:
            session = json.load(open(f))
        except (json.JSONDecodeError, OSError):
            continue
        date = session.get("date", f.stem)
        for task in session.get("tasks", []):
            if task.get("status") != status_filter:
                continue
            if ref_filter and ref_filter.lower() not in (task.get("ref") or "").lower():
                continue
            if source_filter and task.get("source") != source_filter:
                continue
            actual = task.get("actual_min")
            actual_str = f"{actual}m" if actual else "?"
            ref = task.get("ref") or ""
            ref_str = f"  [{ref}]" if ref else ""
            print(f"  {date}  {task['title']}  ({actual_str}){ref_str}")
            found += 1
    if not found:
        print("No matching tasks found.")


def cmd_profile(args):
    """Profile management: list, create, switch, active, delete."""
    sub = args.profile_action
    profiles_dir = WPL_ROOT / "profiles"

    if sub == "list":
        if not profiles_dir.is_dir():
            print("No profiles. Run /workplanner:start to set up.")
            return
        active_name = None
        active_link = profiles_dir / "active"
        if active_link.is_symlink() and active_link.resolve().is_dir():
            active_name = active_link.resolve().name
        for d in sorted(profiles_dir.iterdir()):
            if d.is_dir() and d.name != "active":
                marker = " (active)" if d.name == active_name else ""
                print(f"  {d.name}{marker}")

    elif sub == "create":
        name = args.name
        if not name.isidentifier() and not name.replace("-", "").isalnum():
            fail(f"Invalid profile name '{name}'. Use letters, numbers, and hyphens.")
        target = profiles_dir / name
        if target.exists():
            fail(f"Profile '{name}' already exists.")
        profiles_dir.mkdir(parents=True, exist_ok=True)
        target.mkdir()
        (target / "session").mkdir()
        (target / "session" / "agendas" / "archive").mkdir(parents=True)
        (target / "briefings").mkdir()
        for fname, content in [
            ("config.json", "{}"),
            ("backlog.json", '{"schema_version": 1, "items": []}'),
        ]:
            with open(target / fname, "w") as f:
                f.write(content + "\n")
        # If no active symlink, set this as active
        active_link = profiles_dir / "active"
        if not active_link.exists():
            active_link.symlink_to(name)
        print(f"Created profile '{name}'.")

    elif sub == "switch":
        name = args.name
        target = profiles_dir / name
        if not target.is_dir():
            fail(f"Profile '{name}' does not exist.")
        active_link = profiles_dir / "active"
        if active_link.is_symlink():
            active_link.unlink()
        elif active_link.exists():
            fail(f"'{active_link}' exists but is not a symlink. Remove it manually.")
        active_link.symlink_to(name)
        print(f"Switched to profile '{name}'.")

    elif sub == "active":
        active_link = profiles_dir / "active"
        if active_link.is_symlink() and active_link.resolve().is_dir():
            print(active_link.resolve().name)
        else:
            print("No active profile.")

    elif sub == "delete":
        name = args.name
        target = profiles_dir / name
        if not target.is_dir():
            fail(f"Profile '{name}' does not exist.")
        active_link = profiles_dir / "active"
        if active_link.is_symlink() and active_link.resolve().name == name:
            fail(f"Cannot delete active profile '{name}'. Switch to another profile first.")
        all_profiles = [d for d in profiles_dir.iterdir()
                        if d.is_dir() and d.name != "active"]
        if len(all_profiles) <= 1:
            fail("Cannot delete the last remaining profile.")
        import shutil
        shutil.rmtree(target)
        user = load_user_json()
        if user.get("default_profile") == name:
            print(f"Warning: deleted profile '{name}' was your default_profile. "
                  f"Update with: wpl config set default_profile <name> --user --rationale '...'")
        print(f"Deleted profile '{name}'.")

    else:
        fail("Unknown profile action. Use: list, create, switch, active, delete")


def cmd_decision(args):
    """Decision log management."""
    sub = args.decision_action
    log = load_decision_log()

    if sub == "add":
        entry = {
            "id": f"d-{generate_uid()}",
            "date": local_today().isoformat(),
            "scope": args.key.split(".")[0] if "." in args.key else "general",
            "key": args.key,
            "default": METHODOLOGY_DEFAULTS.get(args.key),
            "value": args.value,
            "rationale": args.rationale,
            "source": args.source,
            "profile": resolve_paths().PROFILE_ROOT.name if not args.user else None,
        }
        # Try to parse value for proper typing
        try:
            entry["value"] = json.loads(args.value)
        except (json.JSONDecodeError, TypeError):
            pass
        log.append(entry)
        save_decision_log(log)
        print(f"Decision {entry['id']} recorded.")

    elif sub == "list":
        if not log:
            print("No decisions recorded.")
            return
        scope_filter = getattr(args, "scope", None)
        for entry in log:
            if scope_filter and entry.get("scope") != scope_filter:
                continue
            profile = f" [{entry['profile']}]" if entry.get("profile") else ""
            default = f" (default: {entry.get('default')})" if entry.get("default") is not None else ""
            print(f"  {entry['id']}  {entry['key']} = {entry['value']}{default}{profile}")
            print(f"           {entry['rationale']}  ({entry.get('source', '?')}, {entry.get('date', '?')})")

    elif sub == "remove":
        target_id = args.id
        before = len(log)
        log = [e for e in log if e["id"] != target_id]
        if len(log) == before:
            fail(f"Decision '{target_id}' not found.")
        save_decision_log(log)
        print(f"Removed decision {target_id}.")

    elif sub == "explain":
        key = args.key
        matches = [e for e in log if e["key"] == key]
        default = METHODOLOGY_DEFAULTS.get(key)
        # Get current value from config
        config = load_config()
        current = _config_get_nested(config, key)
        print(f"Key: {key}")
        print(f"Current value: {current}")
        print(f"Methodology default: {default}")
        if matches:
            latest = matches[-1]
            print(f"Decision: {latest['id']} ({latest.get('date', '?')})")
            print(f"Rationale: {latest['rationale']}")
            print(f"Source: {latest.get('source', '?')}")
        else:
            if current != default and current is not None:
                print("No decision recorded for this deviation from default.")
            else:
                print("Using methodology default.")

    else:
        fail("Unknown decision action. Use: add, list, remove, explain")


def cmd_config(args):
    """Config management with mandatory decision logging."""
    sub = args.config_action

    if sub == "get":
        if args.user:
            data = load_user_json()
        else:
            data = load_config()
        val = _config_get_nested(data, args.key)
        if val is None:
            print(f"{args.key}: (not set)")
        else:
            print(f"{args.key}: {json.dumps(val) if not isinstance(val, str) else val}")

    elif sub == "set":
        if not args.rationale:
            fail("--rationale is required for config changes.")
        if args.user:
            data = load_user_json()
            _config_set_nested(data, args.key, args.value)
            USER_JSON.parent.mkdir(parents=True, exist_ok=True)
            tmp = str(USER_JSON) + ".tmp"
            with open(tmp, "w") as f:
                json.dump(data, f, indent=2)
                f.write("\n")
            os.rename(tmp, str(USER_JSON))
        else:
            paths = resolve_paths()
            try:
                with open(paths.CONFIG) as f:
                    data = json.load(f)
            except (FileNotFoundError, json.JSONDecodeError):
                data = {}
            _config_set_nested(data, args.key, args.value)
            tmp = str(paths.CONFIG) + ".tmp"
            with open(tmp, "w") as f:
                json.dump(data, f, indent=2)
                f.write("\n")
            os.rename(tmp, str(paths.CONFIG))
        # Always record decision
        log = load_decision_log()
        entry = {
            "id": f"d-{generate_uid()}",
            "date": local_today().isoformat(),
            "scope": args.key.split(".")[0] if "." in args.key else "general",
            "key": args.key,
            "default": METHODOLOGY_DEFAULTS.get(args.key),
            "value": args.value,
            "rationale": args.rationale,
            "source": getattr(args, "source", "user-requested"),
            "profile": None if args.user else resolve_paths().PROFILE_ROOT.name,
        }
        try:
            entry["value"] = json.loads(args.value)
        except (json.JSONDecodeError, TypeError):
            pass
        log.append(entry)
        save_decision_log(log)
        scope = "user.json" if args.user else f"profile '{resolve_paths().PROFILE_ROOT.name}'"
        print(f"Set {args.key} = {args.value} in {scope}. Decision {entry['id']} recorded.")

    elif sub == "diff":
        config = load_config()
        any_diff = False
        for key, default in sorted(METHODOLOGY_DEFAULTS.items()):
            current = _config_get_nested(config, key)
            if current is not None and current != default:
                any_diff = True
                # Find rationale from decision log
                log = load_decision_log()
                matches = [e for e in log if e["key"] == key]
                rationale = matches[-1]["rationale"] if matches else "(no rationale recorded)"
                print(f"  {key}: {current} (default: {default})")
                print(f"    {rationale}")
        if not any_diff:
            print("All values match methodology defaults.")

    else:
        fail("Unknown config action. Use: get, set, diff")


# ── CLI ──────────────────────────────────────────────────────────────


def build_parser():
    parser = argparse.ArgumentParser(
        prog="transition.py",
        description="workplanner task state transitions.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("done", help="Mark current task done.")

    p_blocked = sub.add_parser("blocked", help="Mark current task blocked.")
    p_blocked.add_argument("reason", nargs="*", help="Blocking reason (optional).")

    p_defer = sub.add_parser("defer", help="Defer current task.")
    p_defer.add_argument("--until", type=str, default=None, help="Target date (YYYY-MM-DD, tomorrow, monday, next-week). Sends to backlog.")

    p_add = sub.add_parser("add", help="Add a new task.")
    p_add.add_argument("title", nargs="+", help="Task title.")
    p_add.add_argument("--est", type=int, default=30, help="Estimate in minutes (default: 30).")
    p_add.add_argument("--at", type=str, default=None, help="Position: t1, 0, top, end (default: after current).")
    p_add.add_argument("--done", action="store_true", help="Mark as already completed.")
    p_add.add_argument("--started", type=str, default=None, help="Start time HH:MM (for --done).")
    p_add.add_argument("--finished", type=str, default=None, help="Finish time HH:MM (for --done).")
    p_add.add_argument("--source", type=str, default=None, help="Source (default: manual).")
    p_add.add_argument("--ref", type=str, default=None, help="Issue reference.")
    p_add.add_argument("--url", type=str, default=None, help="Issue URL.")
    p_add.add_argument("--notes", type=str, default=None, help="Free-text notes.")
    p_add.add_argument("--parent", type=str, default=None, help="Parent task (t3, index, or uid). Creates a sub-task.")

    p_remove = sub.add_parser("remove", help="Remove a task entirely.")
    p_remove.add_argument("target", help="Task ID (t3) or 0-based index (2).")

    p_move = sub.add_parser("move", help="Reorder a task.")
    p_move.add_argument("source", help="Task to move (t3 or 0-based index).")
    p_move.add_argument("--to", type=str, required=True, help="Destination: t2, top, end, or index.")

    p_switch = sub.add_parser("switch", help="Switch to a different task.")
    p_switch.add_argument("target", help="Task ID (t3) or 0-based index (2).")
    p_switch.add_argument("--no-pause", action="store_true", help="Keep previous task in_progress (for parallel work).")

    p_dispatch = sub.add_parser("dispatch", help="Mark a task as dispatched to another session.")
    p_dispatch.add_argument("target", help="Task ID (t3) or 0-based index (2).")

    p_backlog = sub.add_parser("backlog", help="Manage the backlog (future/long-term tasks).")
    p_backlog.add_argument("title", nargs="*", help="Task title (for adding new items).")
    p_backlog.add_argument("--est", type=int, default=30, help="Estimate in minutes (default: 30).")
    p_backlog.add_argument("--target", type=str, default=None, help="Target date to surface (YYYY-MM-DD or relative).")
    p_backlog.add_argument("--not-before", type=str, default=None, help="Don't surface until this date.")
    p_backlog.add_argument("--deadline", type=str, default=None, help="Hard deadline.")
    p_backlog.add_argument("--ref", type=str, default=None, help="Issue reference.")
    p_backlog.add_argument("--url", type=str, default=None, help="URL.")
    p_backlog.add_argument("--notes", type=str, default=None, help="Notes.")
    p_backlog.add_argument("--tag", type=str, action="append", default=None, help="Tag (repeatable).")
    p_backlog.add_argument("--from", dest="from_task", type=str, default=None, help="Move session task to backlog (t3, index, or uid).")
    p_backlog.add_argument("--from-current", action="store_true", help="Move current task to backlog.")
    p_backlog.add_argument("--list", dest="list_items", action="store_true", help="List backlog items.")
    p_backlog.add_argument("--promote", type=str, default=None, help="Promote backlog item to today's session (uid).")
    p_backlog.add_argument("--drop", type=str, default=None, help="Remove backlog item (uid).")
    p_backlog.add_argument("--edit", type=str, default=None, help="Edit backlog item fields (uid).")

    p_reckon = sub.add_parser("reckon", help="Apply a reckoning decision after deferral threshold.")
    p_reckon.add_argument("choice", help="Decision: b(reak), d(elegate), x/drop, t(imebox), k(eep).")
    p_reckon.add_argument("--date", type=str, default=None, help="Target date for timebox (required for 't').")

    sub.add_parser("status", help="Print one-line status.")

    sub.add_parser("undo", help="Undo the last state change.")

    p_history = sub.add_parser("history", help="Query tasks from past sessions.")
    p_history.add_argument("--days", type=int, default=7, help="Number of past days (default: 7).")
    p_history.add_argument("--ref", type=str, default=None, help="Filter by ref (case-insensitive substring).")
    p_history.add_argument("--source", type=str, default=None, help="Filter by source (linear, slack, etc.).")
    p_history.add_argument("--status", type=str, default=None, help="Filter by status (default: done).")

    profile_parser = sub.add_parser("profile", help="Profile management")
    profile_sub = profile_parser.add_subparsers(dest="profile_action")
    profile_sub.required = True
    profile_sub.add_parser("list", help="List profiles")
    p_create = profile_sub.add_parser("create", help="Create a profile")
    p_create.add_argument("name", help="Profile name")
    p_switch = profile_sub.add_parser("switch", help="Switch active profile")
    p_switch.add_argument("name", help="Profile name to switch to")
    profile_sub.add_parser("active", help="Show active profile name")
    p_delete = profile_sub.add_parser("delete", help="Delete a profile")
    p_delete.add_argument("name", help="Profile name to delete")
    profile_parser.set_defaults(func=cmd_profile)

    # Decision log
    decision_parser = sub.add_parser("decision", help="Decision log management")
    decision_sub = decision_parser.add_subparsers(dest="decision_action")
    decision_sub.required = True
    d_add = decision_sub.add_parser("add", help="Record a decision")
    d_add.add_argument("--key", required=True, help="Config key affected")
    d_add.add_argument("--value", required=True, help="Chosen value")
    d_add.add_argument("--rationale", required=True, help="Why this value was chosen")
    d_add.add_argument("--source", default="user-requested",
                        choices=["user-requested", "system-suggested"])
    d_add.add_argument("--user", action="store_true", help="User-level (not profile)")
    d_list = decision_sub.add_parser("list", help="List decisions")
    d_list.add_argument("--scope", help="Filter by scope")
    d_remove = decision_sub.add_parser("remove", help="Remove a decision")
    d_remove.add_argument("id", help="Decision ID to remove")
    d_explain = decision_sub.add_parser("explain", help="Explain why a key has its value")
    d_explain.add_argument("key", help="Config key to explain")
    decision_parser.set_defaults(func=cmd_decision)

    # Config
    config_parser = sub.add_parser("config", help="Config management")
    config_sub = config_parser.add_subparsers(dest="config_action")
    config_sub.required = True
    c_get = config_sub.add_parser("get", help="Get a config value")
    c_get.add_argument("key", help="Config key (dotted path)")
    c_get.add_argument("--user", action="store_true", help="Read from user.json")
    c_set = config_sub.add_parser("set", help="Set a config value (records decision)")
    c_set.add_argument("key", help="Config key (dotted path)")
    c_set.add_argument("value", help="Value to set")
    c_set.add_argument("--rationale", required=True, help="Why this change")
    c_set.add_argument("--source", default="user-requested",
                        choices=["user-requested", "system-suggested"])
    c_set.add_argument("--user", action="store_true", help="Write to user.json")
    config_sub.add_parser("diff", help="Show deviations from methodology defaults")
    config_parser.set_defaults(func=cmd_config)

    return parser


DISPATCH = {
    "done": cmd_done,
    "blocked": cmd_blocked,
    "defer": cmd_defer,
    "add": cmd_add,
    "remove": cmd_remove,
    "move": cmd_move,
    "switch": cmd_switch,
    "dispatch": cmd_dispatch,
    "backlog": cmd_backlog,
    "reckon": cmd_reckon,
    "status": cmd_status,
    "undo": cmd_undo,
    "history": cmd_history,
    "profile": cmd_profile,
    "decision": cmd_decision,
    "config": cmd_config,
}


WRAPPER = WPL_ROOT / "bin" / "wpl"
RENDER_WRAPPER = WPL_ROOT / "bin" / "wpl-render"
LEGACY_WRAPPER = Path("/tmp/wp")
LEGACY_RENDER_WRAPPER = Path("/tmp/wp-render")


def ensure_wrapper():
    """Create ~/.workplanner/bin/wpl (and wpl-render) if missing or stale.

    The wrapper is a one-line shell script that forwards to this script's
    resolved path. It self-heals on every invocation so the path stays
    current even after plugin version updates.

    Also creates /tmp/wp as a symlink for backward compatibility.
    """
    me = str(Path(__file__).resolve())
    render_script = str((Path(__file__).resolve().parent / "render_dashboard.py"))

    # Primary wrapper: ~/.workplanner/bin/wpl
    _write_wrapper(WRAPPER, f"#!/bin/bash\nexec python3 {me} \"$@\"\n")
    _write_wrapper(RENDER_WRAPPER, f"#!/bin/bash\nexec python3 {render_script} \"$@\"\n")

    # Legacy symlinks: /tmp/wp → ~/.workplanner/bin/wpl
    _ensure_symlink(LEGACY_WRAPPER, WRAPPER)
    _ensure_symlink(LEGACY_RENDER_WRAPPER, RENDER_WRAPPER)


def _write_wrapper(path, content):
    """Write a wrapper script if missing or stale."""
    try:
        if path.exists() and content.splitlines()[1] in path.read_text():
            return
    except OSError:
        pass
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        path.chmod(0o755)
    except OSError:
        pass


def _ensure_symlink(link, target):
    """Create or update a symlink, removing stale files."""
    try:
        if link.is_symlink():
            if link.resolve() == target.resolve():
                return
            link.unlink()
        elif link.exists():
            link.unlink()  # replace old wrapper file with symlink
        link.symlink_to(target)
    except OSError:
        pass


def main():
    # ── Backward compatibility migration ──────────────────────────────
    old_root = Path.home() / "work-planning"
    if old_root.is_dir() and not WPL_ROOT.is_dir():
        print(f"Found legacy data at {old_root}/")
        print(f"Migrating to {WPL_ROOT}/ ...")
        WPL_ROOT.mkdir(parents=True)
        (WPL_ROOT / "profiles").mkdir()
        profile_dir = WPL_ROOT / "profiles" / "work"
        import shutil
        shutil.copytree(old_root, profile_dir, dirs_exist_ok=True)
        session_dir = profile_dir / "session"
        session_dir.mkdir(exist_ok=True)
        for fname in ["current-session.json", "dashboard-view.txt", "events.json"]:
            src = profile_dir / fname
            if src.exists():
                src.rename(session_dir / fname)
        agendas = profile_dir / "agendas"
        if agendas.exists():
            agendas.rename(session_dir / "agendas")
        (WPL_ROOT / "profiles" / "active").symlink_to("work")
        (WPL_ROOT / "bin").mkdir(exist_ok=True)
        print(f"Migration complete. Old data preserved at {old_root}/")
        print("You can remove it after verifying everything works.")

    ensure_wrapper()
    parser = build_parser()
    args = parser.parse_args()
    handler = DISPATCH.get(args.command)
    if handler:
        handler(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
