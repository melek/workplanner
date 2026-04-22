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

WPL_ROOT = Path(os.environ["WPL_ROOT"]) if os.environ.get("WPL_ROOT") else Path.home() / ".workplanner"


# ── Profile resolution ──────────────────────────────────────────────
#
# Profile selection is path-based: each profile declares which filesystem
# paths it serves via its config.json "workspaces: [...]" list. The cwd
# determines the profile by longest-prefix match, with no consultation of
# the global `active` symlink. This eliminates the race where concurrent
# sessions in different profiles clobber each other via symlink flips
# (issue #10). Escape hatches: `--profile NAME` CLI flag, `WPL_PROFILE`
# env var, and a single-profile-without-workspaces fallback.

# Set by main() from the top-level --profile flag, if provided. Overrides
# env-var and path-based resolution.
PROFILE_OVERRIDE = None


def _normalize_path(raw):
    """Return the absolute, symlink-resolved form of a path string.

    `~` is expanded; the path is not required to exist (realpath handles
    missing components on modern systems). Used both for cwd and for
    workspace paths declared in profile configs.
    """
    return os.path.realpath(os.path.expanduser(str(raw)))


def _path_is_prefix(prefix, path):
    """True iff `path` equals `prefix` or is nested under it.

    Uses path-component matching so `/foo/bar` does NOT match `/foo/barn`.
    Both arguments should already be normalized via `_normalize_path`.
    """
    if prefix == path:
        return True
    # Ensure the prefix ends with the separator so we compare components,
    # not substring bytes. `/` is already separator-terminated in effect.
    if prefix.endswith(os.sep):
        return path.startswith(prefix)
    return path.startswith(prefix + os.sep)


def _iter_profile_dirs():
    """Yield profile directory Paths, skipping the `active` alias."""
    profiles_dir = WPL_ROOT / "profiles"
    if not profiles_dir.is_dir():
        return
    for d in sorted(profiles_dir.iterdir()):
        if d.is_dir() and d.name != "active":
            yield d


def _read_profile_config(profile_dir):
    """Load `config.json` for a profile, returning `{}` on any failure.

    Used by profile-resolution code that can't call `load_config()` (which
    itself depends on profile resolution — circular).
    """
    try:
        with open(profile_dir / "config.json") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _profile_workspaces(profile_dir):
    """Return the normalized `workspaces: [...]` list for a profile.

    Non-string entries are skipped silently; the validator (`wpl profile
    validate`, not implemented in this pass) would surface the problem.
    """
    cfg = _read_profile_config(profile_dir)
    raw = cfg.get("workspaces") or []
    if not isinstance(raw, list):
        return []
    out = []
    for entry in raw:
        if not isinstance(entry, str) or not entry.strip():
            continue
        out.append(_normalize_path(entry))
    return out


def _detect_workspace_overlaps():
    """Return a list of (path, [profile_name, ...]) for duplicate workspace claims.

    Two profiles claiming the *identical* workspace path is an
    unresolvable ambiguity. Proper-prefix cases are intentional
    (longest-match wins) and are not reported here.
    """
    by_path = {}
    for d in _iter_profile_dirs():
        for ws in _profile_workspaces(d):
            by_path.setdefault(ws, []).append(d.name)
    return [(path, names) for path, names in sorted(by_path.items())
            if len(names) > 1]


def _resolve_by_cwd(cwd=None):
    """Longest-prefix match of cwd against declared workspaces.

    Returns (profile_dir, matched_workspace_path) or (None, None).
    """
    if cwd is None:
        cwd = _normalize_path(os.getcwd())
    best = None  # (length, profile_dir, workspace_path)
    for d in _iter_profile_dirs():
        for ws in _profile_workspaces(d):
            if _path_is_prefix(ws, cwd):
                # Length ranks by string length; since all are normalized
                # absolute paths, longer string = deeper path.
                if best is None or len(ws) > best[0]:
                    best = (len(ws), d, ws)
    if best is None:
        return None, None
    return best[1], best[2]


def _find_profile_by_name(name):
    """Return the profile directory for `name`, or None."""
    candidate = WPL_ROOT / "profiles" / name
    if candidate.is_dir() and candidate.name != "active":
        return candidate
    return None


def _first_run_prompt(cwd):
    """Interactive prompt when cwd isn't associated with any profile.

    Returns the resolved profile directory (after association/creation) or
    None if the user cancels. Called only when stdin is a TTY and
    $WPL_CHILD != "1".
    """
    print(f"Current directory '{cwd}' isn't associated with any profile.",
          file=sys.stderr)
    existing = [d.name for d in _iter_profile_dirs()]
    if existing:
        print("", file=sys.stderr)
        print("Existing profiles:", file=sys.stderr)
        for name in existing:
            wss = _profile_workspaces(WPL_ROOT / "profiles" / name)
            if wss:
                print(f"  - {name}: {', '.join(wss)}", file=sys.stderr)
            else:
                print(f"  - {name}: (no workspaces)", file=sys.stderr)
        print("", file=sys.stderr)
    print("Options:", file=sys.stderr)
    print("  [1] Associate this directory with an existing profile",
          file=sys.stderr)
    print("  [2] Create a new profile here", file=sys.stderr)
    print("  [3] Cancel", file=sys.stderr)
    try:
        choice = input("Choice [1/2/3]: ").strip()
    except (EOFError, KeyboardInterrupt):
        return None

    if choice == "1":
        if not existing:
            print("No existing profiles to associate.", file=sys.stderr)
            return None
        try:
            name = input(f"Profile name ({'/'.join(existing)}): ").strip()
        except (EOFError, KeyboardInterrupt):
            return None
        target = _find_profile_by_name(name)
        if target is None:
            print(f"Profile '{name}' does not exist.", file=sys.stderr)
            return None
        # Check overlap before writing.
        for d in _iter_profile_dirs():
            if d.name == name:
                continue
            if cwd in _profile_workspaces(d):
                print(f"Profile '{d.name}' already claims '{cwd}'. "
                      f"Disassociate it first.", file=sys.stderr)
                return None
        _add_workspace_to_profile(target, cwd)
        print(f"Associated '{cwd}' with profile '{name}'.", file=sys.stderr)
        return target
    elif choice == "2":
        try:
            name = input("New profile name: ").strip()
        except (EOFError, KeyboardInterrupt):
            return None
        if not name:
            return None
        if not (name.isidentifier() or name.replace("-", "").isalnum()):
            print(f"Invalid profile name '{name}'.", file=sys.stderr)
            return None
        if _find_profile_by_name(name) is not None:
            print(f"Profile '{name}' already exists.", file=sys.stderr)
            return None
        target = _create_profile(name, workspaces=[cwd])
        print(f"Created profile '{name}' associated with '{cwd}'.",
              file=sys.stderr)
        return target
    else:
        return None


def resolve_profile_root():
    """Resolve the active profile directory.

    Resolution order:
      1. Explicit override: `--profile NAME` CLI flag (PROFILE_OVERRIDE)
         or `$WPL_PROFILE` env var. Bypasses path resolution entirely.
      2. Path-based: longest-prefix match of cwd against each profile's
         declared `workspaces: [...]`.
      3. Single-profile fallback: if exactly one profile exists and it
         has no workspaces declared, use it (low-friction single setup).
      4. First-run prompt: if stdin is a TTY and `$WPL_CHILD != "1"`,
         prompt the user to associate or create.
      5. Fail loudly with a diagnostic listing known profiles.

    The `active` symlink is NOT consulted. It's preserved for backward
    compatibility with external integrations (`wpl profile switch` still
    updates it) but no longer participates in resolution.
    """
    # (1) Explicit override — CLI flag wins over env var.
    override = PROFILE_OVERRIDE or os.environ.get("WPL_PROFILE", "").strip() or None
    if override:
        target = _find_profile_by_name(override)
        if target is None:
            fail(f"Profile '{override}' does not exist. "
                 f"Known: {', '.join(d.name for d in _iter_profile_dirs()) or '(none)'}.")
        return target

    # (2) Path-based match.
    cwd = _normalize_path(os.getcwd())
    matched, _ws = _resolve_by_cwd(cwd)
    if matched is not None:
        return matched

    # (3) Single-profile fallback (only if that profile declares no workspaces).
    all_profiles = list(_iter_profile_dirs())
    if len(all_profiles) == 1 and not _profile_workspaces(all_profiles[0]):
        return all_profiles[0]

    # (4) First-run prompt if interactive and not a subprocess.
    is_tty = sys.stdin.isatty() if hasattr(sys.stdin, "isatty") else False
    is_child = os.environ.get("WPL_CHILD", "") == "1"
    if is_tty and not is_child and all_profiles:
        result = _first_run_prompt(cwd)
        if result is not None:
            return result

    # (5) Fail with a helpful diagnostic.
    lines = [f"Current directory '{cwd}' is not associated with any profile."]
    if all_profiles:
        lines.append("")
        lines.append("Known profiles and their workspaces:")
        for d in all_profiles:
            wss = _profile_workspaces(d)
            if wss:
                lines.append(f"  - {d.name}: {', '.join(wss)}")
            else:
                lines.append(f"  - {d.name}: (no workspaces declared)")
        lines.append("")
        lines.append("To fix, run one of:")
        lines.append(f"  wpl profile associate <name> {cwd}")
        lines.append(f"  wpl --profile <name> <command>")
        lines.append(f"  WPL_PROFILE=<name> wpl <command>")
    else:
        lines.append("No profiles exist yet. Run /workplanner:start to set up, "
                     "or: wpl profile create <name> --workspace <path>")
    fail("\n".join(lines))


def _add_workspace_to_profile(profile_dir, workspace_path):
    """Append a workspace path to a profile's config.json, writing atomically.

    Normalizes the path, checks for overlap with other profiles (same-path
    conflict only — proper-prefix overlaps are intentional), and fails
    loudly on conflict. No-op if the path is already present.
    """
    workspace_path = _normalize_path(workspace_path)
    # Overlap check — same path claimed by another profile is ambiguous.
    for d in _iter_profile_dirs():
        if d == profile_dir:
            continue
        if workspace_path in _profile_workspaces(d):
            fail(f"Workspace '{workspace_path}' is already claimed by "
                 f"profile '{d.name}'. Disassociate it there first.")
    config_path = profile_dir / "config.json"
    cfg = _read_profile_config(profile_dir)
    current = cfg.get("workspaces") or []
    if not isinstance(current, list):
        current = []
    # Normalize for comparison; store the normalized form too.
    normalized = [_normalize_path(p) for p in current
                  if isinstance(p, str) and p.strip()]
    if workspace_path in normalized:
        return  # already present
    normalized.append(workspace_path)
    cfg["workspaces"] = normalized
    config_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = str(config_path) + ".tmp"
    with open(tmp, "w") as f:
        json.dump(cfg, f, indent=2)
        f.write("\n")
    os.rename(tmp, str(config_path))


def _remove_workspace_from_profile(profile_dir, workspace_path):
    """Remove a workspace path from a profile's config.json. Idempotent."""
    workspace_path = _normalize_path(workspace_path)
    config_path = profile_dir / "config.json"
    cfg = _read_profile_config(profile_dir)
    current = cfg.get("workspaces") or []
    if not isinstance(current, list):
        return
    filtered = [_normalize_path(p) for p in current
                if isinstance(p, str) and p.strip()
                and _normalize_path(p) != workspace_path]
    cfg["workspaces"] = filtered
    tmp = str(config_path) + ".tmp"
    with open(tmp, "w") as f:
        json.dump(cfg, f, indent=2)
        f.write("\n")
    os.rename(tmp, str(config_path))


def _create_profile(name, workspaces=None):
    """Create a new profile directory with baseline config. Returns the dir.

    Overlap checks run *before* any filesystem mutation so a rejected
    create leaves the `profiles/` tree untouched.
    """
    profiles_dir = WPL_ROOT / "profiles"
    target = profiles_dir / name
    cfg = {}
    normalized = []
    if workspaces:
        normalized = [_normalize_path(w) for w in workspaces]
        for ws in normalized:
            for d in _iter_profile_dirs():
                if ws in _profile_workspaces(d):
                    fail(f"Workspace '{ws}' is already claimed by "
                         f"profile '{d.name}'.")
        cfg["workspaces"] = normalized
    profiles_dir.mkdir(parents=True, exist_ok=True)
    target.mkdir()
    (target / "session").mkdir()
    (target / "session" / "agendas" / "archive").mkdir(parents=True)
    (target / "briefings").mkdir()
    with open(target / "config.json", "w") as f:
        json.dump(cfg, f, indent=2)
        f.write("\n")
    with open(target / "backlog.json", "w") as f:
        f.write('{"schema_version": 1, "items": []}\n')
    # If no active symlink, set this as active (preserves backward compat
    # for anything reading the symlink directly, though resolution no
    # longer relies on it).
    active_link = profiles_dir / "active"
    if not active_link.exists():
        active_link.symlink_to(name)
    return target


def resolve_paths():
    """Return a namespace of resolved paths for the active profile."""
    root = resolve_profile_root()
    class P:
        PROFILE_ROOT = root
        PROFILE_NAME = root.name  # concrete name, not the 'active' alias
        SESSION = root / "session" / "current-session.json"
        CONFIG = root / "config.json"
        BACKLOG = root / "backlog.json"
        UNDO_LOG = root / "undo.jsonl"
        ARCHIVE_DIR = root / "session" / "agendas" / "archive"
        DASHBOARD = root / "session" / "dashboard-view.txt"
        EVENTS = root / "session" / "events.json"
        AGENDAS = root / "session" / "agendas"
        BRIEFINGS = root / "briefings"
        # Session-handoff docs (local narrative) live under the *concrete*
        # profile-name path, never under profiles/active/. This sidesteps the
        # known profile-symlink race (issue #10) for this artifact.
        HANDOFFS = root / "handoffs"
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


# Module-level flag so the deprecation warning fires at most once per process,
# regardless of how many times load_config() is called.
_HANDOFFS_DEPRECATION_WARNED = False


def _warn_deprecated_handoffs_config(config):
    """Emit a one-line stderr warning if legacy config.handoffs.* keys are present.

    Fires at most once per process (module-level flag). The keys themselves are
    left untouched in config.json — users remove them on their own schedule.

    See https://github.com/melek/workplanner/issues/13.
    """
    global _HANDOFFS_DEPRECATION_WARNED
    if _HANDOFFS_DEPRECATION_WARNED:
        return
    handoffs = config.get("handoffs") if isinstance(config, dict) else None
    if not isinstance(handoffs, dict):
        return
    deprecated_keys = ("dir", "carryover_from_handoff", "filename_pattern")
    present = [k for k in deprecated_keys if k in handoffs]
    if not present:
        return
    _HANDOFFS_DEPRECATION_WARNED = True
    # One key per message keeps the signal scannable and matches the
    # "one-line deprecation warning" spec.
    for k in present:
        print(
            f"warning: config.handoffs.{k} is deprecated and has no effect; "
            f"handoffs now live at ~/.workplanner/profiles/<name>/handoffs/. "
            f"See https://github.com/melek/workplanner/issues/13. "
            f"Remove this key from config.json to silence this warning.",
            file=sys.stderr,
        )


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
    _warn_deprecated_handoffs_config(config)
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


# Module-level flag so the stale-session warning fires at most once per
# process, no matter how many times load_session() is called within a
# single invocation.
_STALE_SESSION_WARNED = False


def session_staleness(session, config=None):
    """Return (is_stale, offset_days) for a loaded session.

    `offset_days` is positive when the session's date is older than
    today (the common case), negative if somehow ahead. Missing or
    unparseable `date` returns (False, 0) — no noise, nothing to warn
    about. "Stale" means offset_days >= 1.
    """
    date_str = (session or {}).get("date")
    if not date_str:
        return False, 0
    try:
        session_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return False, 0
    today = local_today(config)
    offset = (today - session_date).days
    return offset >= 1, offset


def load_session():
    """Load current-session.json for the resolved profile.

    On miss, fails with a recovery hint that names `/workplanner:start`
    so an LLM hitting a clean profile knows the next step (issue #18).
    On stale session (date older than today), emits a one-line stderr
    warning — once per process — but does not block. Callers that want
    the staleness state programmatically can call `session_staleness()`.
    """
    global _STALE_SESSION_WARNED
    paths = resolve_paths()
    try:
        with open(paths.SESSION) as f:
            session = json.load(f)
    except FileNotFoundError:
        fail(
            "No session file found. Run /workplanner:start to build today's agenda.",
            next_action="/workplanner:start",
        )
    except json.JSONDecodeError as e:
        fail(f"Session JSON parse error: {e}")
    if backfill_uids(session):
        save_session(session, undo=False)
    # Stale-session warning. Fires once per process; the command still
    # runs (graceful degradation). Emitted to stderr regardless of
    # output format — stderr is a separate channel, so JSON consumers
    # parsing stdout are unaffected. JSON consumers also get `is_stale`
    # and `session_date_offset_days` on the status payload for
    # programmatic detection.
    if not _STALE_SESSION_WARNED:
        try:
            is_stale, offset = session_staleness(session, load_config())
        except SystemExit:
            is_stale, offset = False, 0
        if is_stale:
            _STALE_SESSION_WARNED = True
            date_str = session.get("date", "(unknown)")
            noun = "day" if offset == 1 else "days"
            print(
                f"warning: session is from {date_str} ({offset} {noun} old). "
                f"Run /workplanner:start to open today's session.",
                file=sys.stderr,
            )
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
    """Re-render dashboard after mutation.

    Threads the resolved profile name through to the subprocess via
    $WPL_PROFILE so render_dashboard.py reads/writes the same profile
    this invocation mutated — not whatever the `active` symlink happens
    to point at. Fixes the split-brain described in issue #16.
    """
    env = dict(os.environ)
    try:
        env["WPL_PROFILE"] = resolve_paths().PROFILE_NAME
    except SystemExit:
        # Resolution failed (no profile), let the subprocess inherit
        # whatever ambient env says; it handles missing-profile itself.
        pass
    subprocess.run([sys.executable, str(RENDER)], capture_output=True, env=env)


def fail(msg, **extra):
    # Route through emit_error so JSON mode emits structured errors.
    # Falls back to the historical "error: ..." line in text mode. Extra
    # kwargs (e.g. next_action=...) are attached to the JSON error object;
    # they're ignored in text mode so the human-facing line stays clean.
    emit_error(msg, **extra)


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
    was_tid = False
    if raw.startswith("t") and raw[1:].isdigit():
        target = int(raw[1:]) - 1
        was_tid = True
    elif raw.isdigit():
        target = int(raw)
    else:
        # Try uid lookup
        for i, t in enumerate(tasks):
            if t.get("uid") == raw:
                return i, t
        fail(f"Invalid task target: {raw}. "
             f"Use `wpl status` to see valid IDs and UIDs.")
        return None, None  # unreachable

    if target < 0 or target >= len(tasks):
        # Echo the input in the user's own vocabulary. If they typed `tN`,
        # reply with the valid `tN` range; if they typed a bare integer,
        # reply with the 0-based range they were using.
        if was_tid:
            if tasks:
                valid = f"valid: t1–t{len(tasks)}"
            else:
                valid = "no tasks in this session"
            fail(f"{raw} out of range ({valid}). "
                 f"Use `wpl status` to see valid IDs and UIDs.")
        else:
            if tasks:
                valid = f"0-{len(tasks) - 1}"
            else:
                valid = "no tasks in this session"
            fail(f"Index {target} out of range ({valid}). "
                 f"Use `wpl status` to see valid IDs and UIDs.")
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
    # No in_progress task. Distinguish "all done" from "nothing active but
    # pending remain" so the header doesn't lie after a switch->done
    # sequence that didn't auto-advance. "All tasks complete" fires only
    # when every task landed in `done`; anything else (pending, blocked,
    # deferred left over) gets a "No active task" variant with the
    # residual pending count.
    pending_count = sum(1 for t in tasks if t.get("status") == "pending")
    if total_count and done_count == total_count:
        return (
            f"All tasks complete"
            f" | {date_str}{tz_tag}"
            f" | Done: {done_count}/{total_count}"
            f" | EOD: {eod}"
        )
    if pending_count:
        header = f"No active task — {pending_count} pending"
    elif total_count:
        # Only blocked/deferred tasks left (nothing pending, not all done).
        header = "No active task"
    else:
        header = "No tasks"
    return (
        f"{header}"
        f" | {date_str}{tz_tag}"
        f" | Done: {done_count}/{total_count}"
        f" | ~{fmt_duration(remaining)} left"
        f" | EOD: {eod}"
    )


# ── Output format / shared formatter ─────────────────────────────────

# Set by main() from the top-level --format argument. Commands read it
# to decide between glyph-friendly text output and a structured JSON
# response. Default "text" preserves backward-compatible behaviour.
OUTPUT_FORMAT = "text"


STATUS_GLYPH = {
    "in_progress": "▶",
    "done":        "✓",
    "blocked":     "⚠",
    "deferred":    "↷",
    "pending":     " ",
}


def _task_record(idx, task):
    """Compact dict representation of a task for JSON emission.

    ``defer_reason`` is included only when present so non-deferred tasks
    don't carry a null field (keeps the common case small). This matches
    how the text-mode one-liner only appends the reason when set.
    """
    record = {
        "tid": tid(idx),
        "index": idx,
        "uid": task.get("uid"),
        "title": task.get("title"),
        "status": task.get("status"),
        "estimate_min": task.get("estimate_min"),
        "actual_min": task.get("actual_min"),
        "source": task.get("source"),
        "ref": task.get("ref"),
        "url": task.get("url"),
        "notes": task.get("notes"),
        "started_at": task.get("started_at"),
        "finished_at": task.get("finished_at"),
        "dispatched": bool(task.get("dispatched")),
        "deferral_count": task.get("deferral_count", 0),
        "parent": task.get("parent"),
    }
    defer_reason = task.get("defer_reason")
    if defer_reason:
        record["defer_reason"] = defer_reason
    return record


def session_records(session):
    """Return per-task records list for JSON output."""
    tasks = session.get("tasks", [])
    return [_task_record(i, t) for i, t in enumerate(tasks)]


def format_task_list(session, config=None):
    """Return a list of lines representing the full compact task list.

    Format per Stream A (Q1/Q8): every task on its own line with glyph,
    display ID, UID, title, estimate/actual, and source/ref. Footer line
    matches the existing remaining_summary/status_line shape.

    Shared by every mutating command so the stdout channel carries the
    full post-mutation state. Do not duplicate formatting elsewhere.
    """
    tasks = session.get("tasks", [])
    cur_idx = session.get("current_task_index")
    lines = []

    for i, t in enumerate(tasks):
        status = t.get("status", "pending")
        if i == cur_idx and status == "in_progress":
            glyph = STATUS_GLYPH["in_progress"]
        else:
            glyph = STATUS_GLYPH.get(status, " ")

        uid = (t.get("uid") or "?")[:8]
        title = t.get("title") or "?"

        est = t.get("estimate_min")
        actual = t.get("actual_min")
        if status == "done":
            if actual is not None and est:
                time_str = f"({actual}m/{est}m)"
            elif actual is not None:
                time_str = f"({actual}m)"
            elif est:
                time_str = f"(~{est}m)"
            else:
                time_str = ""
        else:
            time_str = f"(~{est}m)" if est else ""

        ref = t.get("ref")
        source = t.get("source")
        if ref:
            src_str = f" [{ref}]"
        elif source and source != "manual":
            src_str = f" [{source}]"
        else:
            src_str = ""

        dispatch_str = " (dispatched)" if t.get("dispatched") else ""
        parent = t.get("parent")
        parent_str = f" (child of {tid(parent)})" if parent is not None else ""

        line = f"  [{glyph}] {tid(i)}  {uid}  {title}  {time_str}{dispatch_str}{parent_str}{src_str}".rstrip()
        lines.append(line)

    done_count = sum(1 for t in tasks if t.get("status") == "done")
    total_count = len(tasks)
    remaining = sum(
        t.get("estimate_min", 0) or 0
        for t in tasks
        if t.get("status") in ("pending", "in_progress", "blocked")
    )
    eod = session.get("eod_target", "18:00")
    footer = (
        f"  Done: {done_count}/{total_count}"
        f" | ~{fmt_duration(remaining)} left"
        f" | EOD: {eod}"
    )
    if not lines:
        lines.append("  (no tasks)")
    lines.append(footer)
    return lines


def print_task_list(session, config=None):
    """Print the full compact task list to stdout."""
    for line in format_task_list(session, config):
        print(line)


def _status_payload(session, config=None):
    """Build the structured status payload used by --format json."""
    tasks = session.get("tasks", [])
    idx, task = current_task(session)
    eod = session.get("eod_target", "18:00")
    today = local_today(config)
    tz_name = (config or {}).get("timezone", "")

    done_count = sum(1 for t in tasks if t.get("status") == "done")
    total_count = len(tasks)
    remaining = sum(
        t.get("estimate_min", 0) or 0
        for t in tasks
        if t.get("status") in ("pending", "in_progress", "blocked")
    )

    def records_by_status(want):
        return [_task_record(i, t) for i, t in enumerate(tasks)
                if t.get("status") == want]

    current = _task_record(idx, task) if task else None

    # Staleness: expose programmatically so JSON consumers can detect a
    # day-old session without hunting through stderr for the warning
    # load_session() already emitted.
    is_stale, offset_days = session_staleness(session, config)

    return {
        "date": today.isoformat(),
        "session_date": session.get("date"),
        "is_stale": is_stale,
        "session_date_offset_days": offset_days,
        "timezone": tz_name,
        "eod_target": eod,
        "checkpoint": session.get("checkpoint"),
        "current_task": current,
        "current_task_index": idx,
        "tasks": session_records(session),
        "pending": records_by_status("pending"),
        "done": records_by_status("done"),
        "blocked": records_by_status("blocked"),
        "deferred": records_by_status("deferred"),
        "in_progress": records_by_status("in_progress"),
        "counts": {
            "done": done_count,
            "total": total_count,
            "remaining_min": remaining,
        },
    }


def emit_mutation(action, session, affected=None, config=None, human_line=None):
    """Emit a mutation's stdout — text (one-liner + full list) or JSON.

    `action` is the wpl subcommand that ran. `affected` is a list of
    (index, task) tuples whose identity should be echoed in the JSON
    response. `human_line` is the one-line confirmation printed first
    in text mode.
    """
    if OUTPUT_FORMAT == "json":
        payload = {
            "result": "ok",
            "action": action,
            "affected": [
                {"uid": t.get("uid"), "tid": tid(i), "title": t.get("title")}
                for (i, t) in (affected or [])
            ],
            "session": _status_payload(session, config),
        }
        print(json.dumps(payload))
        return
    if human_line:
        print(human_line)
    print_task_list(session, config)


def emit_error(msg, **extra):
    """Emit an error — text to stderr or JSON to stderr, then exit 1."""
    if OUTPUT_FORMAT == "json":
        payload = {"error": msg}
        payload.update(extra)
        print(json.dumps(payload), file=sys.stderr)
    else:
        print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)


def _title_matches(actual, echoed):
    """Case-insensitive, whitespace-tolerant title comparison for --as."""
    def norm(s):
        return " ".join((s or "").lower().split())
    return norm(actual) == norm(echoed)


# ── Commands ─────────────────────────────────────────────────────────


def cmd_done(args):
    session = load_session()
    idx, task = current_task(session)
    if task is None:
        fail("No current task.")
    if task["status"] != "in_progress":
        fail(f"{tid(idx)} is {task['status']}, not in_progress.")

    # Echo-check: --as "<title>" must match the target task's title.
    echoed = getattr(args, "as_title", None)
    if echoed is not None and not _title_matches(task.get("title"), echoed):
        emit_error(
            f"--as echoed title does not match target task. "
            f"Expected \"{task.get('title')}\" (uid {task.get('uid')}), "
            f"got \"{echoed}\".",
            expected_title=task.get("title"),
            expected_uid=task.get("uid"),
            echoed=echoed,
            tid=tid(idx),
        )

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
    human = f"\u2713 {tid(idx)} done ({actual}m/{est}m est). [{remaining_summary(session)}]"
    emit_mutation("done", session, affected=[(idx, task)],
                  config=load_config(), human_line=human)


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
    emit_mutation("blocked", session, affected=[(idx, task)],
                  config=load_config(), human_line=line)


def cmd_defer(args):
    session = load_session()
    idx, task = current_task(session)
    if task is None:
        fail("No current task.")
    if task["status"] not in ("in_progress", "pending"):
        fail(f"{tid(idx)} is {task['status']}, can't defer.")

    reason = getattr(args, "reason", None)
    prior_reason = task.get("defer_reason")

    until = getattr(args, "until", None)
    if until:
        # Defer to backlog with target_date
        target_date = parse_relative_date(until)
        if target_date is None:
            fail(f"Can't parse date: {until}. Use YYYY-MM-DD, tomorrow, monday-sunday, or next-week.")
        # Attach reason before moving so it travels into the backlog item.
        if reason:
            task["defer_reason"] = reason
        _move_task_to_backlog(session, idx, target_date=target_date.isoformat())
        return

    # Track deferral count
    count = task.get("deferral_count", 0) + 1
    task["deferral_count"] = count

    # Attach/refresh the reason if one was provided on this invocation.
    if reason:
        task["defer_reason"] = reason

    # Check reckoning threshold
    config = load_config()
    threshold = (config.get("triage", {})
                 .get("deferrals", {})
                 .get("reckoning_threshold", 3))
    if count >= threshold:
        # Save the incremented count (and reason, if given) *before* emitting
        # so the payload is consistent with what just landed on disk. Status
        # is intentionally unchanged: the caller decides via `wpl reckon`.
        save_session(session, undo=False)

        choices = ["b", "d", "x", "t", "k"]
        prompt_short = "Break down / Delegate / Drop / Timebox / Keep"

        if OUTPUT_FORMAT == "json":
            # Structured payload for programmatic callers. Exit 2 still
            # signals "reckoning needed"; the caller now has the full task
            # record (including defer_reason and deferral_count) to surface
            # the decision. Fixes the Stream A / Stream B contract gap.
            payload = {
                "result": "reckoning-required",
                "action": "defer",
                "task": _task_record(idx, task),
                "threshold": threshold,
                "choices": choices,
                "prompt": prompt_short,
            }
            print(json.dumps(payload))
        else:
            print(f"\u26a0 {tid(idx)} \"{task['title']}\" has been deferred {count} times.")
            # Surface prior context so the reckoning decision has the "why" in hand.
            if reason:
                print(f"  Reason (this defer): {reason}")
            elif prior_reason:
                print(f"  Last reason: {prior_reason}")
            print(f"  What's actually going on?")
            print(f"  [b] Break it down into smaller tasks")
            print(f"  [d] Delegate \u2014 reassign or ask for help")
            print(f"  [x] Drop it \u2014 it's not going to happen")
            print(f"  [t] Timebox \u2014 schedule a dedicated block (sends to backlog with target date)")
            print(f"  [k] Keep deferring \u2014 I'll get to it")
        sys.exit(2)  # Signal to calling skill that reckoning is needed

    was_current = (idx == session.get("current_task_index"))
    task["status"] = "deferred"

    if was_current:
        session["current_task_index"] = None

    save_session(session)
    render()

    reason_tag = f" \u2014 {task['defer_reason']}" if task.get("defer_reason") else ""
    human = f"\u21b7 {tid(idx)} deferred ({count}x){reason_tag}. [{remaining_summary(session)}]"
    emit_mutation("defer", session, affected=[(idx, task)],
                  config=load_config(), human_line=human)


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
    human = f"+ {tid(insert_pos)} added: \"{title}\" (~{est}m){status_tag}{parent_tag}. [{remaining_summary(session)}]"
    emit_mutation("add", session, affected=[(insert_pos, new_task)],
                  config=load_config(), human_line=human)


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
    emit_mutation("switch", session, affected=[(target, task)],
                  config=load_config(), human_line=line)


def cmd_move(args):
    session = load_session()
    source, task = parse_task_target(args.source, session)
    tasks = session.get("tasks", [])
    cur_idx = session.get("current_task_index")

    dest = parse_position(args.to, len(tasks))
    dest = max(0, min(dest, len(tasks)))

    if source == dest:
        human = f"{tid(source)} is already at that position."
        emit_mutation("move", session, affected=[(source, tasks[source])],
                      config=load_config(), human_line=human)
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

    human = f"\u2194 {tid(dest)} \u2014 {moved['title']} (moved from {tid(source)})"
    emit_mutation("move", session, affected=[(dest, moved)],
                  config=load_config(), human_line=human)


def cmd_remove(args):
    """Remove a task entirely from the session."""
    session = load_session()
    target, task = parse_task_target(args.target, session)
    tasks = session.get("tasks", [])
    cur_idx = session.get("current_task_index")

    # Echo-check: --as "<title>" must match the target task's title.
    echoed = getattr(args, "as_title", None)
    if echoed is not None and not _title_matches(task.get("title"), echoed):
        emit_error(
            f"--as echoed title does not match target task. "
            f"Expected \"{task.get('title')}\" (uid {task.get('uid')}), "
            f"got \"{echoed}\".",
            expected_title=task.get("title"),
            expected_uid=task.get("uid"),
            echoed=echoed,
            tid=tid(target),
        )

    title = task["title"]
    removed_snapshot = dict(task)
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
    emit_mutation("remove", session, affected=[(target, removed_snapshot)],
                  config=load_config(), human_line=line)


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

    human = f"\u21c4 {tid(target)} dispatched \u2014 {task['title']}"
    emit_mutation("dispatch", session, affected=[(target, task)],
                  config=load_config(), human_line=human)


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
    # Preserve defer_reason if it was recorded on the task (so carryover keeps context).
    if task.get("defer_reason"):
        item["defer_reason"] = task["defer_reason"]
    # Preserve deferral_count so history-scoped reckoning can reason about it later.
    if task.get("deferral_count"):
        item["deferral_count"] = task["deferral_count"]
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
    human = f"\u21b3 {tid(idx)} \u2192 backlog: \"{title}\"{date_tag}{deadline_tag}. [{remaining_summary(session)}]"
    emit_mutation("backlog:from-session", session, affected=[(idx, task)],
                  config=load_config(), human_line=human)


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
    human = f"+ backlog: \"{title}\" (~{args.est}m){date_str}. [backlog: {len(backlog['items'])} items]"
    # backlog --add does not mutate session; re-load session so the full task list
    # reflects the unchanged plan (LLM feedback loop per Stream A).
    try:
        _session = load_session()
        emit_mutation("backlog:add", _session, affected=[], config=load_config(), human_line=human)
    except SystemExit:
        # No session yet — fall back to just the human line.
        if OUTPUT_FORMAT == "json":
            print(json.dumps({"result": "ok", "action": "backlog:add",
                              "backlog_count": len(backlog['items'])}))
        else:
            print(human)


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
    # Carry defer_reason / deferral_count back into the session task so the "why"
    # travels with the task across the backlog round-trip.
    if item.get("defer_reason"):
        new_task["defer_reason"] = item["defer_reason"]
    if item.get("deferral_count"):
        new_task["deferral_count"] = item["deferral_count"]

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

    human = f"\u2191 {tid(insert_pos)} promoted from backlog: \"{item['title']}\". [{remaining_summary(session)}]"
    emit_mutation("backlog:promote", session, affected=[(insert_pos, new_task)],
                  config=load_config(), human_line=human)


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
    human = f"\u2716 dropped from backlog: \"{dropped['title']}\". [backlog: {len(items)} items]"
    if OUTPUT_FORMAT == "json":
        try:
            _session = load_session()
            emit_mutation("backlog:drop", _session, affected=[], config=load_config(), human_line=human)
        except SystemExit:
            print(json.dumps({"result": "ok", "action": "backlog:drop",
                              "backlog_count": len(items)}))
    else:
        print(human)
        # Session unchanged, but a follow-up compact list keeps the LLM's model warm.
        try:
            _session = load_session()
            print_task_list(_session, load_config())
        except SystemExit:
            pass


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
    human = f"\u270e backlog \"{found['title']}\": {', '.join(changed)}"
    if OUTPUT_FORMAT == "json":
        try:
            _session = load_session()
            emit_mutation("backlog:edit", _session, affected=[], config=load_config(), human_line=human)
        except SystemExit:
            print(json.dumps({"result": "ok", "action": "backlog:edit"}))
    else:
        print(human)
        try:
            _session = load_session()
            print_task_list(_session, load_config())
        except SystemExit:
            pass


def cmd_reckon(args):
    """Apply a reckoning decision to the current task after deferral threshold."""
    session = load_session()
    idx, task = current_task(session)
    if task is None:
        fail("No current task.")

    # If the user passed a --reason, attach/update it now so it surfaces in the
    # post-reckon output line and travels with the task (whether it stays in
    # session, is dropped, or is moved to the backlog).
    new_reason = getattr(args, "reason", None)
    if new_reason:
        task["defer_reason"] = new_reason
    prior_reason = task.get("defer_reason")

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
        reason_tag = f" \u2014 {prior_reason}" if prior_reason else ""
        human = f"\u21b7 {tid(idx)} deferred ({count}x, keeping){reason_tag}. [{remaining_summary(session)}]"
        emit_mutation("reckon:keep", session, affected=[(idx, task)],
                      config=load_config(), human_line=human)

    elif choice in ("x", "drop"):
        title = task["title"]
        tasks = session.get("tasks", [])
        dropped_snapshot = dict(task)
        tasks.pop(idx)
        cur_idx = session.get("current_task_index")
        if cur_idx is not None:
            if cur_idx == idx:
                session["current_task_index"] = None
            elif cur_idx > idx:
                session["current_task_index"] = cur_idx - 1
        save_session(session)
        render()
        human = f"\u2716 {tid(idx)} dropped: \"{title}\". [{remaining_summary(session)}]"
        emit_mutation("reckon:drop", session, affected=[(idx, dropped_snapshot)],
                      config=load_config(), human_line=human)

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
        reason_tag = f" \u2014 {prior_reason}" if prior_reason else ""
        human = f"\u21b7 {tid(idx)} deferred for decomposition{reason_tag}. [{remaining_summary(session)}]"
        emit_mutation("reckon:break", session, affected=[(idx, task)],
                      config=load_config(), human_line=human)

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
        reason_tag = f" \u2014 {prior_reason}" if prior_reason else ""
        human = f"\u21b7 {tid(idx)} deferred (delegate/reassign){reason_tag}. [{remaining_summary(session)}]"
        emit_mutation("reckon:delegate", session, affected=[(idx, task)],
                      config=load_config(), human_line=human)

    else:
        fail(f"Unknown reckoning choice: {choice}. Use b/d/x/t/k.")


def cmd_status(args):
    session = load_session()
    config = load_config()
    if OUTPUT_FORMAT == "json":
        print(json.dumps(_status_payload(session, config)))
    else:
        print(status_line(session, config))
        print_task_list(session, config)


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
    human = f"\u21a9 Undone ({target_str}). Restored state from {last['ts'][:19]}."
    # Re-read current state for full-list feedback (session may or may not have
    # been the thing restored — either way we emit the current session view).
    try:
        _session = load_session()
        emit_mutation("undo", _session, affected=[], config=load_config(), human_line=human)
    except SystemExit:
        if OUTPUT_FORMAT == "json":
            print(json.dumps({"result": "ok", "action": "undo", "target": target_str}))
        else:
            print(human)


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
    """Profile management: list, create, switch, active, delete,
    associate, disassociate, whoami, validate, migrate."""
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
        # Which profile matches cwd right now? (Informational marker only.)
        cwd = _normalize_path(os.getcwd())
        matched_dir, matched_ws = _resolve_by_cwd(cwd)
        matched_name = matched_dir.name if matched_dir else None

        for d in _iter_profile_dirs():
            markers = []
            if d.name == matched_name:
                markers.append("cwd-match")
            if d.name == active_name:
                markers.append("active-symlink")
            marker_str = f" ({', '.join(markers)})" if markers else ""
            wss = _profile_workspaces(d)
            print(f"  {d.name}{marker_str}")
            if wss:
                for ws in wss:
                    arrow = " <-- cwd" if (
                        matched_name == d.name and ws == matched_ws
                    ) else ""
                    print(f"      workspace: {ws}{arrow}")
            else:
                print(f"      (no workspaces declared — run "
                      f"'wpl profile associate {d.name} <path>')")
        # Overlap warnings.
        overlaps = _detect_workspace_overlaps()
        if overlaps:
            print("")
            print("WARNING: workspace overlaps (same path claimed by multiple profiles):")
            for path, names in overlaps:
                print(f"  {path}: {', '.join(names)}")

    elif sub == "create":
        name = args.name
        if not (name.isidentifier() or name.replace("-", "").isalnum()):
            fail(f"Invalid profile name '{name}'. Use letters, numbers, and hyphens.")
        if (profiles_dir / name).exists():
            fail(f"Profile '{name}' already exists.")
        workspaces = list(getattr(args, "workspace", []) or [])
        _create_profile(name, workspaces=workspaces)
        if workspaces:
            print(f"Created profile '{name}' with workspaces: "
                  f"{', '.join(_normalize_path(w) for w in workspaces)}")
        else:
            print(f"Created profile '{name}'. No workspaces declared — "
                  f"run 'wpl profile associate {name} <path>' to enable "
                  f"path-based resolution.")

    elif sub == "associate":
        name = args.name
        path = args.path
        target = _find_profile_by_name(name)
        if target is None:
            fail(f"Profile '{name}' does not exist.")
        _add_workspace_to_profile(target, path)
        print(f"Associated '{_normalize_path(path)}' with profile '{name}'.")

    elif sub == "disassociate":
        name = args.name
        path = args.path
        target = _find_profile_by_name(name)
        if target is None:
            fail(f"Profile '{name}' does not exist.")
        before = _profile_workspaces(target)
        _remove_workspace_from_profile(target, path)
        after = _profile_workspaces(target)
        if len(before) == len(after):
            print(f"'{_normalize_path(path)}' was not associated with '{name}'.")
        else:
            print(f"Disassociated '{_normalize_path(path)}' from profile '{name}'.")

    elif sub == "whoami":
        cwd = _normalize_path(os.getcwd())
        print_root = getattr(args, "print_root", False)
        print_name = getattr(args, "print_name", False)
        # --print-root / --print-name short-circuits to a single line,
        # suitable for shell substitution. Exits non-zero (via fail())
        # when resolution fails so callers can guard with `|| exit 0`.
        if print_root or print_name:
            target = resolve_profile_root()  # fails loudly if unresolved
            if print_root:
                print(str(target))
            else:
                print(target.name)
            return
        override = PROFILE_OVERRIDE or os.environ.get("WPL_PROFILE", "").strip() or None
        if override:
            target = _find_profile_by_name(override)
            if target is None:
                fail(f"Profile '{override}' (from override) does not exist.")
            source = ("--profile flag" if PROFILE_OVERRIDE
                      else "$WPL_PROFILE env var")
            print(f"profile: {target.name}")
            print(f"resolved via: {source}")
            print(f"cwd: {cwd}")
            return
        matched, ws = _resolve_by_cwd(cwd)
        if matched is not None:
            print(f"profile: {matched.name}")
            print(f"resolved via: path match")
            print(f"matched workspace: {ws}")
            print(f"cwd: {cwd}")
            return
        # Single-profile fallback?
        all_profiles = list(_iter_profile_dirs())
        if len(all_profiles) == 1 and not _profile_workspaces(all_profiles[0]):
            print(f"profile: {all_profiles[0].name}")
            print(f"resolved via: single-profile fallback (no workspaces declared)")
            print(f"cwd: {cwd}")
            return
        print(f"profile: (unresolved)")
        print(f"cwd: {cwd}")
        if all_profiles:
            print("known profiles:")
            for d in all_profiles:
                wss = _profile_workspaces(d)
                print(f"  - {d.name}: {', '.join(wss) if wss else '(no workspaces)'}")

    elif sub == "validate":
        # Check for same-path overlaps across profiles.
        overlaps = _detect_workspace_overlaps()
        if overlaps:
            print("Workspace overlaps detected:")
            for path, names in overlaps:
                print(f"  {path}: claimed by {', '.join(names)}")
            sys.exit(1)
        # Warn about profiles without workspaces (unless it's the only one).
        all_profiles = list(_iter_profile_dirs())
        missing = [d.name for d in all_profiles if not _profile_workspaces(d)]
        if missing and len(all_profiles) > 1:
            print("Profiles without declared workspaces "
                  "(path-based resolution disabled):")
            for name in missing:
                print(f"  - {name}")
            print("Run 'wpl profile associate <name> <path>' to fix.")
            sys.exit(1)
        print("OK")

    elif sub == "migrate":
        # Walk the user through associating paths for any profile that
        # lacks workspaces. Interactive only — refuse if not a TTY.
        if not (hasattr(sys.stdin, "isatty") and sys.stdin.isatty()):
            fail("`wpl profile migrate` is interactive; run it from a TTY.")
        missing = [d for d in _iter_profile_dirs() if not _profile_workspaces(d)]
        if not missing:
            print("All profiles have workspaces declared. Nothing to migrate.")
            return
        for d in missing:
            print(f"\nProfile '{d.name}' has no workspaces declared.")
            print(f"Enter one or more absolute paths (blank line to skip this profile).")
            while True:
                try:
                    path = input(f"  workspace for {d.name}: ").strip()
                except (EOFError, KeyboardInterrupt):
                    print("")
                    return
                if not path:
                    break
                try:
                    _add_workspace_to_profile(d, path)
                    print(f"  associated {_normalize_path(path)}")
                except SystemExit:
                    # fail() already emitted the diagnostic.
                    pass
        print("\nMigration complete. Run 'wpl profile list' to review.")

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
        print("")
        print("Note: `wpl profile switch` is preserved for backward compatibility.")
        print("Profile resolution now uses workspace paths declared on each profile.")
        print("Run `wpl profile whoami` to see which profile matches your current directory.")

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
        fail("Unknown profile action. Use: list, create, switch, active, "
             "delete, associate, disassociate, whoami, validate, migrate")


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
    parser.add_argument(
        "--format",
        dest="output_format",
        choices=["text", "json"],
        default="text",
        help="Output format. 'text' (default) is glyph-friendly, LLM- and human-readable. "
             "'json' emits a structured response suitable for programmatic parsing.",
    )
    parser.add_argument(
        "--profile",
        dest="profile_override",
        type=str,
        default=None,
        help="Override profile resolution for this invocation. Bypasses "
             "path-based resolution and the $WPL_PROFILE env var. Use "
             "for scripts running outside a declared workspace or for "
             "deliberate cross-profile inspection.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_done = sub.add_parser("done", help="Mark current task done.")
    p_done.add_argument(
        "--as",
        dest="as_title",
        type=str,
        default=None,
        help="Echo the current task's title. If provided, the CLI refuses the mutation "
             "unless the echoed title matches the target task's title "
             "(case-insensitive, whitespace-tolerant).",
    )

    p_blocked = sub.add_parser("blocked", help="Mark current task blocked.")
    p_blocked.add_argument("reason", nargs="*", help="Blocking reason (optional).")

    p_defer = sub.add_parser("defer", help="Defer current task.")
    p_defer.add_argument("--until", type=str, default=None, help="Target date (YYYY-MM-DD, tomorrow, monday, next-week). Sends to backlog.")
    p_defer.add_argument("--reason", type=str, default=None, help="Why the task is being deferred. Persists on the task and surfaces in reckoning and handoff.")

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
    p_remove.add_argument(
        "--as",
        dest="as_title",
        type=str,
        default=None,
        help="Echo the target task's title. If provided, the CLI refuses the mutation "
             "unless the echoed title matches the target task's title "
             "(case-insensitive, whitespace-tolerant).",
    )

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
    p_reckon.add_argument("--reason", type=str, default=None, help="Update the defer_reason alongside the reckoning decision.")

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
    profile_sub.add_parser("list", help="List profiles with workspaces and cwd match")
    p_create = profile_sub.add_parser(
        "create", help="Create a profile, optionally with workspaces")
    p_create.add_argument("name", help="Profile name")
    p_create.add_argument(
        "--workspace", action="append", default=None,
        help="Filesystem path to pre-associate with the new profile "
             "(repeatable). Normalized via realpath.")
    p_assoc = profile_sub.add_parser(
        "associate", help="Add a workspace path to an existing profile")
    p_assoc.add_argument("name", help="Profile name")
    p_assoc.add_argument("path", help="Absolute path to associate")
    p_disassoc = profile_sub.add_parser(
        "disassociate", help="Remove a workspace path from a profile")
    p_disassoc.add_argument("name", help="Profile name")
    p_disassoc.add_argument("path", help="Absolute path to remove")
    p_whoami = profile_sub.add_parser(
        "whoami",
        help="Show which profile the current cwd resolves to, and how.")
    p_whoami.add_argument(
        "--print-root", action="store_true",
        help="Print only the resolved profile root path (absolute). "
             "Useful from shell scripts that need the concrete directory "
             "without parsing the full whoami output.")
    p_whoami.add_argument(
        "--print-name", action="store_true",
        help="Print only the resolved profile name (no directory).")
    profile_sub.add_parser(
        "validate",
        help="Check for workspace overlaps and missing-workspace warnings.")
    profile_sub.add_parser(
        "migrate",
        help="Interactively associate workspace paths with existing profiles.")
    p_switch = profile_sub.add_parser(
        "switch", help="[deprecated] Update the `active` symlink")
    p_switch.add_argument("name", help="Profile name to switch to")
    profile_sub.add_parser("active", help="Show active-symlink profile name")
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
    # Set module-level output format for this invocation so commands can
    # branch on it (text vs json) without threading it through every call.
    global OUTPUT_FORMAT, PROFILE_OVERRIDE
    OUTPUT_FORMAT = getattr(args, "output_format", "text") or "text"
    # Set module-level profile override (CLI --profile flag). This wins
    # over $WPL_PROFILE env var, which in turn wins over path-based
    # resolution. See resolve_profile_root().
    PROFILE_OVERRIDE = getattr(args, "profile_override", None) or None
    handler = DISPATCH.get(args.command)
    if handler:
        handler(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
