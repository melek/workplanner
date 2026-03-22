#!/usr/bin/env python3
"""Append an event to the workplanner events.json for the active profile.

Usage:
    python3 write_event.py --id eod_reminder --type info --message "EOD in 30 minutes" --ttl 45
"""
import argparse, json, os, sys
from datetime import datetime
from pathlib import Path

WPL_ROOT = Path.home() / ".workplanner"


def get_events_path():
    active = WPL_ROOT / "profiles" / "active"
    if active.is_symlink() and active.resolve().is_dir():
        return active.resolve() / "session" / "events.json"
    if active.is_dir() and not active.is_symlink():
        return active / "session" / "events.json"
    return None


def main():
    parser = argparse.ArgumentParser(description="Write an event to the workplanner event queue")
    parser.add_argument("--id", required=True, help="Unique event ID (dedupe key)")
    parser.add_argument("--type", default="info", choices=["warning", "alert", "info"], help="Event severity")
    parser.add_argument("--message", "-m", required=True, help="Event message")
    parser.add_argument("--ttl", type=int, default=None, help="Auto-expire after N minutes (omit to persist)")
    args = parser.parse_args()

    events_path = get_events_path()
    if events_path is None:
        sys.stderr.write("workplanner: no active profile found\n")
        sys.exit(1)

    try:
        with open(events_path) as f:
            events = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        events = []

    # Skip if event with this ID already exists and is not dismissed
    for ev in events:
        if ev["id"] == args.id and not ev.get("dismissed"):
            return

    events.append({
        "id": args.id,
        "type": args.type,
        "message": args.message,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "ttl_min": args.ttl,
        "dismissed": False,
    })

    events_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = str(events_path) + ".tmp"
    with open(tmp, "w") as f:
        json.dump(events, f, indent=2)
    os.rename(tmp, str(events_path))


if __name__ == "__main__":
    main()
