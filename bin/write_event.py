#!/usr/bin/env python3
"""Append an event to the workplanner events.json for the resolved profile.

Profile resolution follows transition.py's precedence: $WPL_PROFILE env
var → cwd-based match → single-profile fallback → legacy ``active``
symlink. The legacy symlink is only consulted as a last resort so
manual invocations from an unassociated cwd still find a target.

Usage:
    python3 write_event.py --id eod_reminder --type info --message "EOD in 30 minutes" --ttl 45
"""
import argparse, json, os, sys
from datetime import datetime
from pathlib import Path

WPL_ROOT = Path(os.environ["WPL_ROOT"]) if os.environ.get("WPL_ROOT") else Path.home() / ".workplanner"


# Import profile resolution from transition.py so we honour the same
# precedence chain (--profile / $WPL_PROFILE / cwd) rather than blindly
# following the `active` symlink. Fixes the split-brain described in
# issue #16.
sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    from transition import (  # noqa: E402
        _find_profile_by_name,
        _resolve_by_cwd,
        _iter_profile_dirs,
        _profile_workspaces,
    )
    _HAS_TRANSITION = True
except ImportError:
    _HAS_TRANSITION = False


def _active_symlink_fallback():
    """Last-resort: the legacy `active` symlink."""
    active = WPL_ROOT / "profiles" / "active"
    if active.is_symlink() and active.resolve().is_dir():
        return active.resolve()
    if active.is_dir() and not active.is_symlink():
        return active
    return None


def _resolve_profile_root():
    """Same precedence as transition.py::resolve_profile_root but without
    interactive prompting or hard failure — event writes should be
    best-effort side-channel operations."""
    override = os.environ.get("WPL_PROFILE", "").strip() or None
    if override and _HAS_TRANSITION:
        target = _find_profile_by_name(override)
        if target is not None:
            return target
    if _HAS_TRANSITION:
        matched, _ws = _resolve_by_cwd()
        if matched is not None:
            return matched
        all_profiles = list(_iter_profile_dirs())
        if len(all_profiles) == 1 and not _profile_workspaces(all_profiles[0]):
            return all_profiles[0]
    return _active_symlink_fallback()


def get_events_path():
    root = _resolve_profile_root()
    if root is None:
        return None
    return root / "session" / "events.json"


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
