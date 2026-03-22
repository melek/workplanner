#!/usr/bin/env python3
"""Render the workplanner dashboard from current-session.json.

Reads ~/.workplanner/profiles/active/session/current-session.json and
~/.workplanner/profiles/active/config.json, writes
~/.workplanner/profiles/active/session/dashboard-view.txt (atomic: .tmp then mv).

Python 3.9+ stdlib only.
"""

import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

WPL_ROOT = Path.home() / ".workplanner"


def resolve_active_profile():
    active = WPL_ROOT / "profiles" / "active"
    if active.is_symlink() and active.resolve().is_dir():
        return active.resolve()
    if active.is_dir() and not active.is_symlink():
        return active
    return None


def get_paths():
    root = resolve_active_profile()
    if root is None:
        return None
    return {
        "session": root / "session" / "current-session.json",
        "config": root / "config.json",
        "dashboard": root / "session" / "dashboard-view.txt",
        "events": root / "session" / "events.json",
        "backlog": root / "backlog.json",
    }


def _get_path(key):
    """Return a single path by key, or None if no active profile."""
    paths = get_paths()
    if paths is None:
        return None
    return paths[key]


# Module-level __getattr__ for dynamic path resolution.
# dashboard_tui.py imports rd.SESSION, rd.EVENTS, rd.DASHBOARD as Path objects.
# These are resolved on each attribute access so they always reflect the active profile.
_SENTINEL = Path("/dev/null")

def __getattr__(name):
    _map = {"SESSION": "session", "EVENTS": "events", "DASHBOARD": "dashboard"}
    if name in _map:
        p = _get_path(_map[name])
        return p if p is not None else _SENTINEL
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

RESET = "\033[0m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
DIM = "\033[2m"
CYAN = "\033[36m"
ORANGE = "\033[38;5;203m"

STATUS_ICON = {
    "done": f"{GREEN}[x]{RESET}",
    "in_progress": f"{YELLOW}[\u25b6]{RESET}",
    "dispatched": f"{YELLOW}[\u21c4]{RESET}",
    "pending": "[ ]",
    "blocked": f"{CYAN}[!]{RESET}",
    "deferred": f"{CYAN}[~]{RESET}",
}


def _status_key(task):
    """Return the icon key for a task, accounting for dispatched flag."""
    if task.get("dispatched") and task.get("status") == "in_progress":
        return "dispatched"
    return task.get("status", "pending")


# ── Helpers ──────────────────────────────────────────────────────────


def parse_hhmm(s):
    """Parse 'HH:MM' to total minutes since midnight."""
    if not s:
        return None
    parts = s.split(":")
    return int(parts[0]) * 60 + int(parts[1])


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


def elapsed_minutes(started_at_str):
    """Wall-clock minutes from HH:MM to now."""
    if not started_at_str:
        return 0
    try:
        now = datetime.now()
        parts = started_at_str.split(":")
        start = now.replace(hour=int(parts[0]), minute=int(parts[1]), second=0, microsecond=0)
        diff = now - start
        return max(0, int(diff.total_seconds() / 60))
    except Exception:
        return 0


def _get_idle_periods(date_str):
    """Parse pmset log for display off/on periods on the given date.
    Returns list of (off_dt, on_dt) tuples. on_dt is None if still off."""
    try:
        result = subprocess.run(
            ["pmset", "-g", "log"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0:
            return []
    except Exception:
        return []

    periods = []
    off_time = None
    for line in result.stdout.splitlines():
        if "Display is turned off" in line or "Display is turned on" in line:
            m = re.match(r"(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})", line)
            if not m:
                continue
            ts = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
            if ts.strftime("%Y-%m-%d") != date_str:
                continue
            if "Display is turned off" in line:
                off_time = ts
            elif "Display is turned on" in line and off_time is not None:
                periods.append((off_time, ts))
                off_time = None
    if off_time is not None:
        periods.append((off_time, None))
    return periods


def _idle_minutes_in_range(start_dt, end_dt, idle_periods):
    """Sum idle minutes overlapping [start_dt, end_dt]."""
    if end_dt is None:
        end_dt = datetime.now()
    total = 0
    for off, on in idle_periods:
        if on is None:
            on = datetime.now()
        clip_start = max(off, start_dt)
        clip_end = min(on, end_dt)
        if clip_start < clip_end:
            total += (clip_end - clip_start).total_seconds() / 60
    return int(total)


def active_elapsed_minutes(started_at_str, idle_periods):
    """Elapsed minutes minus idle overlap. Returns (active, idle)."""
    wall = elapsed_minutes(started_at_str)
    if not idle_periods or not started_at_str:
        return wall, 0
    try:
        now = datetime.now()
        parts = started_at_str.split(":")
        start = now.replace(hour=int(parts[0]), minute=int(parts[1]), second=0, microsecond=0)
        idle = _idle_minutes_in_range(start, now, idle_periods)
        return max(0, wall - idle), idle
    except Exception:
        return wall, 0


def assign_ids(tasks):
    """Assign positional IDs (t1, t2, ...) to tasks. Mutates in place."""
    for i, t in enumerate(tasks):
        t["id"] = f"t{i + 1}"


def get_parent_id(t):
    """Get parent display ID from a task's 'parent' field (index into tasks array)."""
    idx = t.get("parent")
    if idx is None:
        return None
    return f"t{idx + 1}"


def load_config():
    """Load config.json, returning empty dict on failure."""
    config_path = _get_path("config")
    if config_path is None:
        return {}
    try:
        with open(config_path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


# ── Events / Alerts ──────────────────────────────────────────────────


def _load_events():
    events_path = _get_path("events")
    if events_path is None:
        return []
    try:
        with open(events_path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def _save_events(events):
    events_path = _get_path("events")
    if events_path is None:
        return
    tmp = str(events_path) + ".tmp"
    with open(tmp, "w") as f:
        json.dump(events, f, indent=2)
    os.rename(tmp, str(events_path))


def _active_events(events):
    now = datetime.now()
    active = []
    for ev in events:
        if ev.get("dismissed"):
            continue
        ttl = ev.get("ttl_min")
        if ttl is not None:
            try:
                created = datetime.fromisoformat(ev["created_at"])
                if now > created + timedelta(minutes=ttl):
                    continue
            except (ValueError, KeyError):
                continue
        active.append(ev)
    return active


def _prune_expired(events):
    now = datetime.now()
    kept = []
    for ev in events:
        if ev.get("dismissed"):
            kept.append(ev)
            continue
        ttl = ev.get("ttl_min")
        if ttl is not None:
            try:
                created = datetime.fromisoformat(ev["created_at"])
                if now > created + timedelta(minutes=ttl):
                    continue
            except (ValueError, KeyError):
                continue
        kept.append(ev)
    return kept


def _parents_with_children(tasks):
    parents = set()
    for t in tasks:
        pid = get_parent_id(t)
        if pid:
            parents.add(pid)
    return parents


def _countable_tasks(tasks):
    parents = _parents_with_children(tasks)
    return [t for t in tasks if t["id"] not in parents]


def _detect_alerts(session, existing_events, idle_periods=None):
    now = datetime.now()
    existing_ids = {ev["id"] for ev in existing_events}
    new_events = []
    tasks = session.get("tasks", [])
    eod = session.get("eod_target", "17:00")

    for t in tasks:
        if t.get("status") != "in_progress":
            continue
        est = t.get("estimate_min")
        if not est:
            continue
        if idle_periods:
            el, _ = active_elapsed_minutes(t.get("started_at"), idle_periods)
        else:
            el = elapsed_minutes(t.get("started_at"))
        if el > est * 1.5:
            eid = f"overrun_{t['id']}"
            if eid not in existing_ids:
                new_events.append({
                    "id": eid,
                    "type": "warning",
                    "message_template": "overrun",
                    "message": "",
                    "task_id": t["id"],
                    "created_at": now.isoformat(timespec="seconds"),
                    "ttl_min": 30,
                    "dismissed": False,
                })

    eod_min = parse_hhmm(eod)
    now_min = now.hour * 60 + now.minute
    time_left = max(0, eod_min - now_min) if eod_min else 0

    countable = _countable_tasks(tasks)
    remaining_min = sum(
        t.get("estimate_min", 0) or 0
        for t in countable
        if t.get("status") in ("pending", "in_progress", "blocked")
    )

    if remaining_min > 0 and remaining_min > time_left:
        eid = f"budget_shortfall_{now.strftime('%H')}"
        if eid not in existing_ids:
            new_events.append({
                "id": eid,
                "type": "alert",
                "message_template": "budget_shortfall",
                "message": "",
                "created_at": now.isoformat(timespec="seconds"),
                "ttl_min": 60,
                "dismissed": False,
            })

    return new_events


def _overrun_message(task_id, session, idle_periods=None):
    if not task_id:
        return None
    for t in session.get("tasks", []):
        if t["id"] == task_id and t.get("status") == "in_progress":
            est = t.get("estimate_min")
            if not est:
                return None
            if idle_periods:
                el, _ = active_elapsed_minutes(t.get("started_at"), idle_periods)
            else:
                el = elapsed_minutes(t.get("started_at"))
            if el > est * 1.5:
                return f"{task_id} over estimate ({el}m/{est}m)"
    return None


def _budget_shortfall_message(session):
    tasks = session.get("tasks", [])
    eod = session.get("eod_target", "17:00")
    eod_min = parse_hhmm(eod)
    now = datetime.now()
    now_min = now.hour * 60 + now.minute
    time_left = max(0, eod_min - now_min) if eod_min else 0
    countable = _countable_tasks(tasks)
    remaining_min = sum(
        t.get("estimate_min", 0) or 0
        for t in countable
        if t.get("status") in ("pending", "in_progress", "blocked")
    )
    if remaining_min > 0 and remaining_min > time_left:
        return f"~{fmt_duration(remaining_min)} work, {fmt_duration(time_left)} left"
    return None


def _render_alerts(events, session=None, idle_periods=None):
    active = _active_events(events)
    if not active:
        return []
    lines = []
    for ev in active:
        msg = ev.get("message") or ""
        template = ev.get("message_template")
        if template == "budget_shortfall" and session:
            msg = _budget_shortfall_message(session)
            if not msg:
                continue
        elif template == "overrun" and session:
            msg = _overrun_message(ev.get("task_id"), session, idle_periods)
            if not msg:
                continue
        if msg:
            lines.append(f"  {ORANGE}! {msg}{RESET}")
    return lines


# ── Protected blocks ─────────────────────────────────────────────────


def _protected_block_minutes(config, now_min, eod_min):
    """Sum protected-block minutes between now and EOD."""
    total = 0
    for block in config.get("protected_blocks", []):
        b_start = parse_hhmm(block.get("start"))
        b_end = parse_hhmm(block.get("end"))
        if b_start is None or b_end is None:
            continue
        # Clip to [now, eod]
        clip_start = max(b_start, now_min)
        clip_end = min(b_end, eod_min)
        if clip_start < clip_end:
            total += clip_end - clip_start
    return total


def _find_block_insert_position(tasks, block_start_min):
    """Determine the task index where a protected block separator should be inserted.

    Walk forward through tasks, accumulating wall-clock time from started_at of the
    first in_progress task (or current time for pending tasks). The block goes between
    the last task that finishes before the block and the first that finishes after.
    """
    # Simple heuristic: insert before the first pending task whose cumulative
    # start would fall after the block start time.
    now_min = datetime.now().hour * 60 + datetime.now().minute
    cursor = now_min
    for i, t in enumerate(tasks):
        status = t.get("status", "pending")
        if status == "done":
            # Done tasks are in the past, skip
            continue
        if status == "in_progress":
            # Current task — its start is known
            started = parse_hhmm(t.get("started_at"))
            if started is not None:
                cursor = started + (t.get("estimate_min") or 0)
            else:
                cursor = now_min + (t.get("estimate_min") or 0)
            if cursor > block_start_min:
                return i
            continue
        # pending / blocked / deferred
        if cursor >= block_start_min:
            return i
        cursor += t.get("estimate_min") or 0
        if cursor > block_start_min:
            return i
    return len(tasks)


# ── Task rendering ───────────────────────────────────────────────────


def _task_right(t, idle_periods=None):
    """Right-side status/timing string for a task line."""
    status = t.get("status", "pending")
    if status == "done":
        actual = t.get("actual_min")
        if actual is not None:
            return f"  {GREEN}{fmt_duration(actual)}{RESET}"
        return f"  {GREEN}done{RESET}"
    elif status == "in_progress":
        est = t.get("estimate_min")
        notes = t.get("notes") or ""
        if "agent" in notes.lower() or "parallel" in notes.lower():
            return f"  {YELLOW}~ agent{RESET}"
        active, idle = active_elapsed_minutes(t.get("started_at"), idle_periods)
        idle_tag = f" {DIM}(-{idle}m idle){RESET}" if idle > 0 else ""
        if est:
            over = active > est
            color = RED if over else YELLOW
            return f"  {color}{active}m/{est}m{RESET}{idle_tag}"
        return f"  {YELLOW}{active}m{RESET}{idle_tag}"
    elif status == "pending":
        est = t.get("estimate_min")
        if est:
            return f"  ~{fmt_duration(est)}"
        return ""
    elif status == "blocked":
        return f"  {CYAN}blocked{RESET}"
    elif status == "deferred":
        return f"  {CYAN}deferred{RESET}"
    return ""


def _render_children(lines, children, idle_periods=None):
    for i, child in enumerate(children):
        is_last = (i == len(children) - 1)
        connector = "\u2514\u2500\u2500" if is_last else "\u251c\u2500\u2500"
        c_icon = STATUS_ICON.get(_status_key(child), "?")
        c_title = child["title"]
        ref_tag = f" [{child.get('ref')}]" if child.get("ref") else ""
        c_right = _task_right(child, idle_periods)
        lines.append(f" {connector} {c_icon}  {child['id']:<5}{c_title}{ref_tag}{c_right}")


# ── Main render ──────────────────────────────────────────────────────


def render(session, config=None, idle_periods=None):
    if config is None:
        config = {}
    tasks = session.get("tasks", [])
    assign_ids(tasks)
    date_str = session.get("date", "")
    week = session.get("week", "")
    eod = session.get("eod_target", "18:00")

    # Parse date for header
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        header_date = dt.strftime("%a %d %b")
    except Exception:
        header_date = date_str

    now_clock = datetime.now().strftime("%H:%M")
    # Append active profile name if available
    profile_name = ""
    active_link = WPL_ROOT / "profiles" / "active"
    if active_link.is_symlink() and active_link.resolve().is_dir():
        profile_name = f" [{active_link.resolve().name}]"
    header = f" WORKPLAN  {header_date} \u2014 {week}{profile_name}"
    cols = int(os.environ.get("COLUMNS", 0)) or shutil.get_terminal_size((50, 24)).columns
    pad = max(0, cols - len(header) - len(now_clock))
    header = f"{header}{' ' * pad}{DIM}{now_clock}{RESET}"

    lines = [header, "\u2500" * 29]

    # Determine protected block insert positions
    protected_blocks = config.get("protected_blocks", [])
    block_inserts = {}  # task_index -> block info
    for block in protected_blocks:
        b_start = parse_hhmm(block.get("start"))
        if b_start is None:
            continue
        pos = _find_block_insert_position(tasks, b_start)
        block_inserts[pos] = block

    # Group tasks: parent/child via 'parent' field
    parent_children = {}
    standalone = []
    for t in tasks:
        pid = get_parent_id(t)
        if pid:
            parent_children.setdefault(pid, [])
            parent_children[pid].append(t)
        else:
            standalone.append(t)

    # Map standalone tasks to their original indices for block insertion
    standalone_indices = []
    for t in tasks:
        if get_parent_id(t) is None:
            standalone_indices.append(tasks.index(t))

    # Render each standalone task, inserting protected block separators
    for si, t in enumerate(standalone):
        orig_idx = standalone_indices[si] if si < len(standalone_indices) else si

        # Check if a protected block goes before this task
        if orig_idx in block_inserts:
            block = block_inserts[orig_idx]
            emoji = block.get("emoji", "")
            label = block.get("label", "")
            b_start = block.get("start", "")
            b_end = block.get("end", "")
            prefix = f" {emoji}" if emoji else ""
            lines.append(f"\u2500\u2500{prefix} {b_start}\u2013{b_end} {label} \u2500\u2500")

        tid = t["id"]
        has_children = tid in parent_children

        if has_children:
            children = parent_children[tid]
            all_done = all(c["status"] == "done" for c in children)
            any_in_progress = any(c["status"] == "in_progress" for c in children)
            if all_done:
                icon = STATUS_ICON["done"]
            elif any_in_progress:
                icon = STATUS_ICON["in_progress"]
            else:
                icon = STATUS_ICON["pending"]
            ref_tag = f" [{t.get('ref')}]" if t.get("ref") else ""
            lines.append(f" {icon}  {tid:<4} {t['title']}{ref_tag}")
            _render_children(lines, children, idle_periods)
        else:
            icon = STATUS_ICON.get(_status_key(t), "?")
            ref_tag = f" [{t.get('ref')}]" if t.get("ref") else ""
            title = t["title"] + ref_tag
            right = _task_right(t, idle_periods)
            lines.append(f" {icon}  {tid:<4} {title}  {right}")

    # Check if a block goes after all tasks
    for pos, block in block_inserts.items():
        if pos >= len(tasks):
            emoji = block.get("emoji", "")
            label = block.get("label", "")
            b_start = block.get("start", "")
            b_end = block.get("end", "")
            prefix = f" {emoji}" if emoji else ""
            lines.append(f"\u2500\u2500{prefix} {b_start}\u2013{b_end} {label} \u2500\u2500")

    lines.append("\u2500" * 29)

    # Summary — exclude parent tasks that have children
    countable = _countable_tasks(tasks)
    done_count = sum(1 for t in countable if t["status"] == "done")
    total_count = len(countable)

    remaining_min = sum(
        t.get("estimate_min", 0) or 0
        for t in countable
        if t["status"] in ("pending", "in_progress", "blocked")
    )

    now = datetime.now()
    eod_min = parse_hhmm(eod)
    now_min = now.hour * 60 + now.minute
    raw_time_left = max(0, eod_min - now_min) if eod_min else 0

    # Subtract protected blocks that fall between now and EOD
    protected_min = _protected_block_minutes(config, now_min, eod_min or 0)
    time_left = max(0, raw_time_left - protected_min)

    remaining_str = fmt_duration(remaining_min) if remaining_min > 0 else "0m"

    buffer_min = time_left - remaining_min
    if buffer_min > 30:
        buffer_label = f"{GREEN}OK{RESET}"
    elif buffer_min > 0:
        buffer_label = f"{YELLOW}Tight{RESET}"
    elif remaining_min == 0:
        buffer_label = f"{GREEN}OK{RESET}"
    elif buffer_min < 0:
        buffer_label = f"{RED}Over{RESET}"
    else:
        buffer_label = f"{YELLOW}Tight{RESET}"

    buffer_detail = fmt_duration(abs(buffer_min))

    # Backlog count
    backlog_path = _get_path("backlog")
    bl_count = 0
    try:
        with open(backlog_path) as f:
            bl_count = len(json.load(f).get("items", []))
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    bl_str = f"  \u2502  Backlog: {bl_count}" if bl_count > 0 else ""

    lines.append(f" Done: {GREEN}{done_count}{RESET}/{total_count}  \u2502  ~{remaining_str} left{bl_str}")
    lines.append(f" EOD: {eod}  \u2502  Buffer: {buffer_label} ({buffer_detail})")

    # Alerts
    events = _active_events(_load_events())
    alert_lines = _render_alerts(events, session, idle_periods)
    if alert_lines:
        lines.append("")
        lines.extend(alert_lines)

    return "\n".join(lines) + "\n"


# ── Alert log (--alert-log) ──────────────────────────────────────────


def render_alert_log(session, idle_periods=None):
    events = _load_events()
    if idle_periods is None:
        idle_periods = _get_idle_periods(session.get("date", ""))
    now_clock = datetime.now().strftime("%H:%M")
    cols = int(os.environ.get("COLUMNS", 0)) or shutil.get_terminal_size((50, 24)).columns
    header = " ALERT LOG"
    pad = max(0, cols - len(header) - len(now_clock))
    header = f"{header}{' ' * pad}{DIM}{now_clock}{RESET}"

    lines = [header, "\u2500" * 29]

    if not events:
        lines.append(f"  {DIM}No alerts{RESET}")
    else:
        for ev in reversed(events):
            template = ev.get("message_template")
            msg = ev.get("message") or ""
            if template == "budget_shortfall":
                msg = _budget_shortfall_message(session) or "(resolved)"
            elif template == "overrun":
                msg = _overrun_message(ev.get("task_id"), session, idle_periods) or "(resolved)"

            created = ev.get("created_at", "")[:16].replace("T", " ")
            dismissed = ev.get("dismissed", False)
            ttl = ev.get("ttl_min")
            expired = False
            if ttl is not None:
                try:
                    ct = datetime.fromisoformat(ev["created_at"])
                    expired = datetime.now() > ct + timedelta(minutes=ttl)
                except (ValueError, KeyError):
                    pass

            if dismissed:
                state = f"{DIM}dismissed{RESET}"
            elif expired:
                state = f"{DIM}expired{RESET}"
            else:
                state = f"{ORANGE}active{RESET}"

            time_short = created[11:16] if len(created) >= 16 else created
            lines.append(f"  {ORANGE}!{RESET} {time_short}  {msg}")
            lines.append(f"          {state}  ttl:{ttl or '~'}m  id:{ev.get('id', '?')}")

    lines.append("\u2500" * 29)
    return "\n".join(lines) + "\n"


# ── Main ─────────────────────────────────────────────────────────────


def main():
    alert_log_mode = "--alert-log" in sys.argv

    paths = get_paths()
    if paths is None:
        sys.stderr.write("workplanner: no active profile found\n")
        sys.exit(0)

    config = load_config()

    try:
        with open(paths["session"]) as f:
            session = json.load(f)
    except FileNotFoundError:
        _write_dashboard("No active session\n")
        return
    except json.JSONDecodeError:
        _write_dashboard("No active session\n")
        return

    assign_ids(session.get("tasks", []))

    if alert_log_mode:
        print(render_alert_log(session))
        return

    idle_periods = _get_idle_periods(session.get("date", ""))

    # Detect new alerts and update events file
    events = _load_events()
    new_events = _detect_alerts(session, events, idle_periods)
    if new_events:
        events.extend(new_events)
    events = _prune_expired(events)
    _save_events(events)

    rendered = render(session, config, idle_periods)

    _write_dashboard(rendered)
    print(rendered)


def _write_dashboard(content):
    """Atomic write to dashboard file."""
    dashboard_path = _get_path("dashboard")
    if dashboard_path is None:
        return
    dashboard_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = str(dashboard_path) + ".tmp"
    with open(tmp, "w") as f:
        f.write(content)
    os.rename(tmp, str(dashboard_path))


if __name__ == "__main__":
    main()
