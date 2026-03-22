# Workplanner Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Separate engine from methodology, add profile system, scrub private context, and prepare workplanner for public release.

**Architecture:** Three-layer separation (engine / methodology / integrations). Profile-based multi-context support via `~/.workplanner/profiles/`. Decision log for methodology customization tracking. All changes preserve the existing single-file CLI architecture.

**Tech Stack:** Python 3.9+ stdlib only. Flat JSON files. Atomic writes. Markdown docs/skills.

**Spec:** `docs/superpowers/specs/2026-03-21-workplanner-refactor-design.md`

---

## File Map

### Files to Modify
| File | Change |
|------|--------|
| `bin/transition.py` | Replace hardcoded paths with `resolve_root()`, add profile/decision/config commands |
| `bin/render_dashboard.py` | Update paths, add profile name to header |
| `bin/dashboard_tui.py` | Update paths |
| `bin/write_event.py` | Update paths |
| `bin/session-hook.sh` | Update paths to profile-aware structure |
| `bin/save-sessions.sh` | Update paths |
| `bin/restore-sessions.sh` | Update paths |
| `skills/start/SKILL.md` | Update paths, rewrite first-run setup, scrub private context |
| `skills/eod/SKILL.md` | Update paths, scrub private context |
| `skills/pickup/SKILL.md` | Update paths |
| `skills/dispatch/SKILL.md` | Update paths |
| `skills/pre-plan/SKILL.md` | Update paths, scrub private context |
| `skills/horizon/SKILL.md` | Update paths, scrub private context |
| `skills/freeze/SKILL.md` | Update paths |
| `docs/inbox-runbooks.md` | Scrub private context, parameterize by config |
| `docs/triage-framework.md` | Scrub private context, generic examples |
| `docs/task-transitions.md` | Update paths, scrub private context (HAPAI-1267) |
| `CHANGELOG.md` | Scrub private context (context-a8c ref), add refactor entry |
| `.claude-plugin/plugin.json` | Verify repo URL is correct for public repo (melek/workplanner is intended) |
| `.gitignore` | Update stale `~/work-planning/` comment |
| `docs/morning-assembly.md` | Scrub private context, generic examples |
| `docs/eod-consolidation.md` | Scrub private context |
| `docs/state-schema.md` | Scrub private context, add user.json and decision-log schemas |
| `SPECIFICATION.md` | Rewrite to reflect new architecture |
| `CLAUDE.md` | Rewrite to reflect new architecture |
| `README.md` | Rewrite for public release |
| `ISSUES.md` | Remove (replaced by GitHub Issues) |

### Files to Create
| File | Purpose |
|------|---------|
| `docs/methodology.md` | Core philosophy document — the seven principles |
| `docs/reference-config.md` | Annotated power-user config example |
| `docs/future-work.md` | Deferred features (cross-profile tasks, pattern reckoning, pre-plan in /start) |

---

## Task 1: Engine Path Migration — resolve_root()

**Files:**
- Modify: `bin/transition.py:20-26` (path constants)

This is the foundation. Replace the five hardcoded path constants with a `resolve_root()` function that follows `~/.workplanner/profiles/active/`.

**Decisions:**
- `transition.py` keeps its current name (no rename to `engine.py` — the wrapper is `wpl`, users never see the filename).
- `resolve_paths()` is called fresh on each invocation (no caching). CLI invocations are short-lived and profile switches mid-invocation aren't possible.
- Backward compatibility: if `~/work-planning/` exists and `~/.workplanner/` doesn't, offer to migrate. Implemented as a check in `main()` before any command runs.

- [ ] **Step 1: Add the WPL_ROOT constant and resolve_root() function**

Add after the imports (line 18), replacing lines 20-26:

```python
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
```

- [ ] **Step 2: Update load_config() to support profile + user.json inheritance**

```python
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
    # Inherit from user.json if not overridden
    user = load_user_json()
    for key in ("timezone", "eod_target"):
        if key not in config and key in user:
            config[key] = user[key]
    return config
```

- [ ] **Step 3: Update load_session() and save_session() to use resolve_paths()**

Replace the direct `SESSION` references. `load_session()` becomes:

```python
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
```

Apply the same pattern to `save_session()`, `save_undo()`, `load_backlog()`, `save_backlog()`, and `render()`.

- [ ] **Step 4: Update ensure_wrapper() for new paths**

The wrapper now lives at `~/.workplanner/bin/wpl`. Update `ensure_wrapper()` to:
- Create `~/.workplanner/bin/` directory
- Write `wpl` wrapper pointing to `${CLAUDE_PLUGIN_ROOT}/bin/transition.py`
- Write `wpl-render` wrapper pointing to `${CLAUDE_PLUGIN_ROOT}/bin/render_dashboard.py`
- Remove legacy symlinks at `~/work-planning/wpl` if they exist

- [ ] **Step 5: Add backward compatibility migration in main()**

At the top of `main()`, before command dispatch:

```python
# Backward compatibility: migrate ~/work-planning/ to ~/.workplanner/
old_root = Path.home() / "work-planning"
if old_root.is_dir() and not WPL_ROOT.is_dir():
    print(f"Found legacy data at {old_root}/")
    print(f"Migrating to {WPL_ROOT}/ ...")
    WPL_ROOT.mkdir(parents=True)
    (WPL_ROOT / "profiles").mkdir()
    profile_dir = WPL_ROOT / "profiles" / "work"
    # Move the old flat structure into a "work" profile
    import shutil
    shutil.copytree(old_root, profile_dir, dirs_exist_ok=True)
    # Create session subdirectory structure
    session_dir = profile_dir / "session"
    session_dir.mkdir(exist_ok=True)
    # Move session files into session/
    for fname in ["current-session.json", "dashboard-view.txt", "events.json"]:
        src = profile_dir / fname
        if src.exists():
            src.rename(session_dir / fname)
    agendas = profile_dir / "agendas"
    if agendas.exists():
        agendas.rename(session_dir / "agendas")
    # Set active symlink
    (WPL_ROOT / "profiles" / "active").symlink_to("work")
    # Create bin directory
    (WPL_ROOT / "bin").mkdir(exist_ok=True)
    print(f"Migration complete. Old data preserved at {old_root}/")
    print("You can remove it after verifying everything works.")
```

- [ ] **Step 6: Verify the migration compiles and runs**

Run: `python3 bin/transition.py --help`
Expected: No import errors, help text displayed.

- [ ] **Step 7: Commit**

```bash
git add bin/transition.py
git commit -m "refactor: replace hardcoded paths with profile-aware resolve_root()

Engine now resolves state paths through ~/.workplanner/profiles/active/ symlink.
Adds resolve_profile_root(), resolve_paths(), load_user_json().
Updates all state I/O functions to use resolved paths.
Wrapper creation moved to ~/.workplanner/bin/."
```

---

## Task 2: Engine Path Migration — Supporting Scripts

**Files:**
- Modify: `bin/render_dashboard.py:19-22`
- Modify: `bin/write_event.py:11`
- Modify: `bin/session-hook.sh:5-8`
- Modify: `bin/save-sessions.sh`
- Modify: `bin/restore-sessions.sh`
- Modify: `bin/dashboard.sh`

- [ ] **Step 1: Update render_dashboard.py path constants**

Replace the four path constants (lines 19-22) with profile-aware resolution:

```python
WPL_ROOT = Path.home() / ".workplanner"

def resolve_active_profile():
    active = WPL_ROOT / "profiles" / "active"
    if active.is_symlink() or active.is_dir():
        return active.resolve()
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
    }
```

Update all references to `SESSION`, `CONFIG`, `DASHBOARD`, `EVENTS` throughout the file.

- [ ] **Step 2: Update write_event.py path constant**

Change line 11 from `Path.home() / "work-planning" / "events.json"` to resolve through the active profile.

- [ ] **Step 3: Update session-hook.sh**

The file already uses a `profiles/active/session/` structure but with the old `work-planning` root. Change the root prefix from `work-planning` to `.workplanner`. Also update the user-facing message on line 56 (`add ~/work-planning to PATH` → `add ~/.workplanner/bin to PATH`).

```bash
SESSION="$HOME/.workplanner/profiles/active/session/current-session.json"
[ -f "$SESSION" ] || exit 0
```

- [ ] **Step 4: Update save-sessions.sh and restore-sessions.sh**

Replace `~/work-planning/` references with `~/.workplanner/`.

- [ ] **Step 5: Confirm dashboard_tui.py path inheritance**

`dashboard_tui.py` imports path constants from `render_dashboard.py` (`rd.SESSION`, `rd.EVENTS`, `rd.DASHBOARD`). Verify that after Task 2 Step 1, the TUI inherits the updated paths automatically. If `dashboard_tui.py` has any direct `~/work-planning/` references, update them too.

- [ ] **Step 6: Update dashboard.sh if it has any hardcoded paths**

- [ ] **Step 7: Verify render works**

Run: `python3 bin/render_dashboard.py --help` (or just run it — it should exit gracefully if no session exists).

- [ ] **Step 8: Commit**

```bash
git add bin/render_dashboard.py bin/dashboard_tui.py bin/write_event.py bin/session-hook.sh bin/save-sessions.sh bin/restore-sessions.sh bin/dashboard.sh
git commit -m "refactor: update supporting scripts for ~/.workplanner/ paths

render_dashboard.py, dashboard_tui.py, write_event.py, session-hook.sh,
save/restore-sessions.sh all now resolve paths through profiles/active/ symlink."
```

---

## Task 3: Engine Path Migration — All Skills

**Files:**
- Modify: All 7 files in `skills/*/SKILL.md`
- Modify: `docs/task-transitions.md`

- [ ] **Step 1: Update all `~/work-planning/` references in skills to `~/.workplanner/`**

Search and replace across all skill files. The key patterns:
- `~/work-planning/wpl` → `~/.workplanner/bin/wpl` (or just `wpl` if on PATH)
- `~/work-planning/current-session.json` → `~/.workplanner/profiles/active/session/current-session.json`
- `~/work-planning/config.json` → `~/.workplanner/profiles/active/config.json`
- `~/work-planning/backlog.json` → `~/.workplanner/profiles/active/backlog.json`
- `~/work-planning/briefings/` → `~/.workplanner/profiles/active/briefings/`
- `~/work-planning/dashboard-view.txt` → `~/.workplanner/profiles/active/session/dashboard-view.txt`
- `~/work-planning/agendas/` → `~/.workplanner/profiles/active/session/agendas/`
- `~/work-planning/handoffs/` → `~/.workplanner/profiles/active/handoffs/`

Use `wpl` (short form, assuming PATH) where the skill is giving CLI examples. Use full paths where the skill reads files directly.

- [ ] **Step 2: Verify no `~/work-planning` references remain**

Run: `grep -r "~/work-planning" skills/ docs/task-transitions.md`
Expected: No matches.

- [ ] **Step 3: Commit**

```bash
git add skills/ docs/task-transitions.md
git commit -m "refactor: update all skills and docs for ~/.workplanner/ paths"
```

---

## Task 4: Engine Expansion — Profile Commands

**Files:**
- Modify: `bin/transition.py` (add profile commands and parser entries)

- [ ] **Step 1: Add cmd_profile() function**

```python
def cmd_profile(args):
    """Profile management: list, create, switch, active, delete."""
    sub = args.profile_action
    profiles_dir = WPL_ROOT / "profiles"

    if sub == "list":
        if not profiles_dir.is_dir():
            print("No profiles. Run /workplanner:start to set up.")
            return
        active = (profiles_dir / "active").resolve().name if (profiles_dir / "active").exists() else None
        for d in sorted(profiles_dir.iterdir()):
            if d.is_dir() and d.name != "active":
                marker = " (active)" if d.name == active else ""
                print(f"  {d.name}{marker}")

    elif sub == "create":
        name = args.name
        target = profiles_dir / name
        if target.exists():
            fail(f"Profile '{name}' already exists.")
        target.mkdir(parents=True)
        (target / "session").mkdir()
        (target / "session" / "agendas" / "archive").mkdir(parents=True)
        (target / "briefings").mkdir()
        # Write empty config and backlog
        for fname, content in [("config.json", "{}"), ("backlog.json", '{"schema_version": 1, "items": []}')]:
            p = target / fname
            with open(p, "w") as f:
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
        active_link.symlink_to(name)
        print(f"Switched to profile '{name}'.")

    elif sub == "active":
        active_link = profiles_dir / "active"
        if active_link.exists():
            print(active_link.resolve().name)
        else:
            print("No active profile.")

    elif sub == "delete":
        name = args.name
        target = profiles_dir / name
        if not target.is_dir():
            fail(f"Profile '{name}' does not exist.")
        active_link = profiles_dir / "active"
        if active_link.exists() and active_link.resolve().name == name:
            fail(f"Cannot delete active profile '{name}'. Switch to another profile first.")
        all_profiles = [d for d in profiles_dir.iterdir() if d.is_dir() and d.name != "active"]
        if len(all_profiles) <= 1:
            fail("Cannot delete the last remaining profile.")
        import shutil
        shutil.rmtree(target)
        # If this was the default_profile, warn
        user = load_user_json()
        if user.get("default_profile") == name:
            print(f"Warning: deleted profile '{name}' was your default_profile. "
                  f"Update with: wpl config set default_profile <name> --user --rationale '...'")
        print(f"Deleted profile '{name}'.")
```

- [ ] **Step 2: Add profile subparser to build_parser()**

```python
profile_parser = subparsers.add_parser("profile", help="Profile management")
profile_sub = profile_parser.add_subparsers(dest="profile_action")
profile_sub.add_parser("list", help="List profiles")
p_create = profile_sub.add_parser("create", help="Create a profile")
p_create.add_argument("name")
p_switch = profile_sub.add_parser("switch", help="Switch active profile")
p_switch.add_argument("name")
profile_sub.add_parser("active", help="Show active profile")
p_delete = profile_sub.add_parser("delete", help="Delete a profile")
p_delete.add_argument("name")
profile_parser.set_defaults(func=cmd_profile)
```

- [ ] **Step 3: Verify profile commands work**

Run:
```bash
mkdir -p ~/.workplanner/profiles
python3 bin/transition.py profile create test-profile
python3 bin/transition.py profile list
python3 bin/transition.py profile active
python3 bin/transition.py profile delete test-profile
```

Expected: Profile created, listed with `(active)` marker, shown as active, then deleted (or refused if it's the last one).

- [ ] **Step 4: Commit**

```bash
git add bin/transition.py
git commit -m "feat: add profile management commands (list/create/switch/active/delete)"
```

---

## Task 5: Engine Expansion — Decision Log and Config Commands

**Files:**
- Modify: `bin/transition.py` (add decision/config commands)

- [ ] **Step 1: Add decision log utilities**

```python
def load_decision_log():
    try:
        with open(DECISION_LOG) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []

def save_decision_log(log):
    tmp = str(DECISION_LOG) + ".tmp"
    with open(tmp, "w") as f:
        json.dump(log, f, indent=2)
        f.write("\n")
    os.rename(tmp, str(DECISION_LOG))
```

- [ ] **Step 2: Add cmd_decision() function**

Implement `add`, `list`, `remove`, `explain` subcommands per the spec. Each entry gets an auto-generated `d-{8char}` ID.

- [ ] **Step 3: Add cmd_config() function**

Implement `get`, `set`, `diff` subcommands. `set` always writes a decision log entry (requires `--rationale`). `--user` flag targets `user.json` instead of profile config. `diff` iterates config keys and compares against a `METHODOLOGY_DEFAULTS` dict defined in the engine. Decision log entries created by `config set` default to `source: "user-requested"`. The LLM can pass `--source system-suggested` when making automated recommendations.

- [ ] **Step 4: Define METHODOLOGY_DEFAULTS**

```python
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
```

- [ ] **Step 5: Add decision and config subparsers to build_parser()**

- [ ] **Step 6: Verify commands work**

Run:
```bash
python3 bin/transition.py decision add --key triage.filter.task_cap --value 6 --rationale "testing"
python3 bin/transition.py decision list
python3 bin/transition.py decision explain triage.filter.task_cap
python3 bin/transition.py decision remove <id-from-above>
python3 bin/transition.py config get eod_target --user
python3 bin/transition.py config diff
```

- [ ] **Step 7: Commit**

```bash
git add bin/transition.py
git commit -m "feat: add decision log and config management commands

wpl decision add/list/remove/explain for methodology deviation tracking.
wpl config get/set/diff with mandatory decision logging.
METHODOLOGY_DEFAULTS dict defines reference values for diff."
```

---

## Task 6: Private Context Scrub

**Files:**
- Modify: All files listed in spec section "Private Context Scrub"

This is a systematic find-and-replace across the codebase. No structural changes.

- [ ] **Step 1: Run comprehensive grep to inventory all private references**

```bash
grep -rn "lioneld\|lioneldaniel\|ceres\|HAPAI\|WOOPUBR\|WOODOCS\|context-a8c\|updateomattic\|ceresp2\|aihappy\|melek/" --include="*.md" --include="*.sh" --include="*.py" .
```

Record every hit. Ignore `.git/` and the spec file itself.

- [ ] **Step 2: Scrub skills/start/SKILL.md**

This is the largest file. Key changes:
- Replace `@lioneld` with `config.slack_handle` references
- Replace `lioneldaniel` with `config.github_username`
- Replace team-specific Slack handles with `config.inbox_slack_team_handles`
- Replace `updateomattic.wordpress.com` with config-driven announcement source
- Replace "P2" references with "feed/blog" generalization
- Replace "Ceres weekly focus" with generic `config.weekly_focus.label`
- Replace all `HAPAI-*`, `WOOPUBR-*` with `PROJ-123` style placeholders
- Replace `#ceres` with `#team-general`
- Replace `context-a8c` with "organizational context MCP"

- [ ] **Step 3: Scrub docs/inbox-runbooks.md**

- Replace `@lioneld` (line 79) with `config.slack_handle`
- Replace `lioneldaniel` (lines 134-135) with `config.github_username`
- Replace `melek/` skip rule with `config.github_skip_orgs`
- Replace team handle examples with generic examples
- Replace `mgs` provider references with "content/blog provider"

- [ ] **Step 4: Scrub docs/triage-framework.md**

- Replace all `@lioneld`, `#ceres`, `HAPAI-*`, `WOOPUBR-*` in examples
- Replace example agenda with generic task names

- [ ] **Step 5: Scrub docs/morning-assembly.md**

- Replace all private references in example agendas
- Replace `ceresp2` with generic blog reference

- [ ] **Step 6: Scrub docs/state-schema.md**

- Replace `HAPAI-2660`, `HAPAI-2610` example refs with `PROJ-123`
- Replace `["HAPAI", "WOOPUBR"]` with `["TEAM-A", "TEAM-B"]`
- Replace `"Ceres weekly focus"` with generic label
- Replace `"@ceres-porter", "@ceres"` with generic handles
- Replace `"ceresp2.wordpress.com", "aihappy.wordpress.com"` with generic domains

- [ ] **Step 7: Scrub remaining files**

- `docs/eod-consolidation.md`
- `docs/task-transitions.md` (line 48: `HAPAI-1267`)
- `skills/eod/SKILL.md`
- `skills/horizon/SKILL.md` (line 55: `HAPAI-2700`)
- `skills/pre-plan/SKILL.md` (lines 192, 239: `HAPAI-2680`)
- `CHANGELOG.md` (line 21: `context-a8c`)
- `.gitignore` (line 12: stale `~/work-planning/` comment)
- `bin/session-hook.sh` (line 56: stale `~/work-planning` in user message, if not already fixed in Task 2)
- `SPECIFICATION.md`
- `README.md`

Note: `.claude-plugin/plugin.json` contains `melek/workplanner` — this is the correct public repo URL and should be kept.

- [ ] **Step 8: Verify no private references remain**

Run: `grep -rn "lioneld\|HAPAI\|WOOPUBR\|WOODOCS\|ceres\|context-a8c\|updateomattic\|aihappy\|melek/" --include="*.md" --include="*.sh" --include="*.py" . | grep -v ".git/" | grep -v "specs/"`

Expected: No matches (excluding the spec file).

- [ ] **Step 9: Commit**

```bash
git add -A
git commit -m "scrub: remove all private/org-specific references

Replace hardcoded usernames, team handles, Linear project keys,
MCP server names, and org-specific examples with config-driven
references and generic placeholders throughout all skills, docs,
and runbooks."
```

---

## Task 7: Methodology Document

**Files:**
- Create: `docs/methodology.md`

- [ ] **Step 1: Write docs/methodology.md**

Content is fully specified in the design spec, section "Methodology Document." Copy the seven principles, neurodivergent design rationale, and customization path into a standalone document. Add an introduction explaining what this document is and how it relates to the engine and skills.

Structure:
```markdown
# Workplanner Methodology

## What This Document Is
## Core Principles
### 1. Capture Exhaustively, Decide Once
### 2. Two Bookends, Nothing Between
### 3. Timebox, Don't Estimate
### 4. Carryover Earns Its Place
### 5. Force the Reckoning
### 6. Deterministic Plumbing, Flexible Policy
### 7. Graceful Degradation Everywhere
## Neurodivergent Design Rationale
## Customization
## Framework Lineage
```

- [ ] **Step 2: Commit**

```bash
git add docs/methodology.md
git commit -m "docs: add methodology document — the seven principles

Articulates the productivity philosophy, neurodivergent design
rationale, framework lineage (GTD, Pomodoro, bullet journal),
and customization path."
```

---

## Task 8: Setup Interview Redesign

**Files:**
- Modify: `skills/start/SKILL.md` (rewrite First-Run Setup section)

- [ ] **Step 1: Rewrite the First-Run Setup section**

Replace the current step-by-step questionnaire (lines ~99-153 of the current SKILL.md) with the GTD-principled interview from the spec. Key changes:

- Add Pre-Interview Environment Probe (MCP testing + enhanced heuristics)
- Replace specific questions ("Linear user ID?", "Slack handle?") with principled questions ("Where does work get assigned to you?")
- Add Phase 4: Terminal Environment (tmux recommendation)
- Add Phase 5: Confirmation (show derived config in plain language)
- Profile creation integrated: "What should we call this work context?"
- Write `user.json` (schema: spec lines 136-166, fields: `schema_version`, `display_name`, `timezone`, `eod_target`, `default_profile`, `non_workday_profile`, `workday_schedule`, `tmux_recommended`) and profile `config.json` during Step 3

- [ ] **Step 2: Add auto-selection logic to the routing section**

In the Routing section, before checking session state, add the auto-selection algorithm from the spec:

```
if multiple profiles exist:
  1. Read user.json workday_schedule for today
  2. workday=true → select default_profile
  3. workday=false → select non_workday_profile (or default_profile)
  4. Update active symlink if needed
```

- [ ] **Step 3: Verify the skill reads cleanly**

Read through the full SKILL.md to check for internal consistency, no leftover private references, and correct path references.

- [ ] **Step 4: Commit**

```bash
git add skills/start/SKILL.md
git commit -m "feat: redesign setup interview — GTD-principled inbox discovery

Conversational setup with environment probing, principled inbox
questions, tmux recommendation, and profile creation. Replaces
the step-by-step questionnaire that assumed specific integrations."
```

---

## Task 9: Dashboard Updates

**Files:**
- Modify: `bin/render_dashboard.py:467-478`
- Modify: `bin/dashboard_tui.py` (if it has its own header rendering)

- [ ] **Step 1: Add profile name to dashboard header**

In `render_dashboard.py`, modify the header line (~line 475):

```python
# Get active profile name
profile_name = ""
active_link = WPL_ROOT / "profiles" / "active"
if active_link.exists():
    profile_name = f" [{active_link.resolve().name}]"

header = f" WORKPLAN  {header_date} \u2014 {week}{profile_name}"
```

- [ ] **Step 2: Verify dashboard renders with profile name**

Run: `python3 bin/render_dashboard.py` (with an active session)
Expected: Header shows `WORKPLAN  Fri 21 Mar -- W12 [work]`

- [ ] **Step 3: Commit**

```bash
git add bin/render_dashboard.py bin/dashboard_tui.py
git commit -m "feat: show active profile name in dashboard header"
```

---

## Task 10: Reference Config, Future Work, and Schema Updates

**Files:**
- Create: `docs/reference-config.md`
- Create: `docs/future-work.md`
- Modify: `docs/state-schema.md`

- [ ] **Step 1: Write docs/reference-config.md**

Annotated example of a fully configured power-user profile. Show:
- Multiple messaging platforms (Slack workspaces, Teams)
- Project management integrations (Linear, GitHub)
- Calendar + email
- Custom triage weights
- Protected blocks
- Non-default methodology values with explanations

This is documentation, not a template. Comments explain each field.

- [ ] **Step 2: Write docs/future-work.md**

Three sections:
1. **Cross-profile task references** — Tasks tagged with source profile, artifact routing
2. **Pattern reckoning** — Historical analysis of archived sessions for config suggestions
3. **Pre-plan auto-integration** — Running `/pre-plan` as part of `/start`

For each: describe the feature, why it's deferred, and what prerequisites are needed.

- [ ] **Step 3: Add user.json and decision-log schemas to docs/state-schema.md**

Add two new sections to the existing schema doc:
- `user.json` schema (from spec lines 136-166)
- `decision-log.json` schema (from spec lines 329-356)
- Update the `config.json` section to note profile inheritance from user.json

- [ ] **Step 4: Commit**

```bash
git add docs/reference-config.md docs/future-work.md docs/state-schema.md
git commit -m "docs: add reference config, future work, and updated schemas

reference-config.md: annotated power-user example.
future-work.md: cross-profile tasks, pattern reckoning, pre-plan in /start.
state-schema.md: user.json and decision-log.json schemas added."
```

---

## Task 11: Top-Level Documentation

**Files:**
- Modify: `CLAUDE.md`
- Modify: `SPECIFICATION.md`
- Modify: `README.md`
- Modify: `CHANGELOG.md`
- Delete: `ISSUES.md`

- [ ] **Step 1: Rewrite CLAUDE.md**

Update to reflect:
- New data root (`~/.workplanner/`)
- Profile-based architecture
- Three-layer separation (engine / methodology / integrations)
- New CLI commands (profile, decision, config)
- Decision log and methodology defaults
- No private context references

- [ ] **Step 2: Rewrite SPECIFICATION.md**

Update to reflect the new architecture. The spec document in `docs/superpowers/specs/` is the detailed design; SPECIFICATION.md is the concise public-facing version. Key updates:
- Profile system
- Decision log
- Setup interview approach
- Methodology reference
- Config inheritance model

- [ ] **Step 3: Rewrite README.md**

Clean public-facing documentation:
- What workplanner is (one paragraph)
- Install instructions
- Usage (skills + wpl CLI)
- Profile system
- Architecture overview (brief)
- Dependencies
- Development section (link to GitHub Issues)
- No private references

- [ ] **Step 4: Update CHANGELOG.md**

Add an entry for this refactor (engine/methodology separation, profile system, private context scrub, decision log, setup redesign).

- [ ] **Step 5: Remove ISSUES.md**

The issues have been transferred or are tracked on GitHub.

- [ ] **Step 6: Final verification**

Run the full grep check one more time:
```bash
grep -rn "lioneld\|HAPAI\|WOOPUBR\|WOODOCS\|ceres\|context-a8c\|updateomattic\|aihappy\|~/work-planning" --include="*.md" --include="*.sh" --include="*.py" . | grep -v ".git/" | grep -v "specs/"
```

Expected: No matches.

- [ ] **Step 7: Commit**

```bash
git add CLAUDE.md SPECIFICATION.md README.md CHANGELOG.md
git rm ISSUES.md
git commit -m "docs: rewrite top-level documentation for public release

CLAUDE.md: reflects profile architecture and new CLI surface.
SPECIFICATION.md: updated for engine/methodology/integration separation.
README.md: clean public-facing documentation.
CHANGELOG.md: refactor entry added.
ISSUES.md: removed (tracked on GitHub)."
```

---

## Checkpoint: Final Review

After all tasks are complete:

- [ ] **Run full private context grep** — verify zero matches
- [ ] **Run `python3 bin/transition.py --help`** — verify all new commands appear
- [ ] **Run `python3 bin/transition.py profile list`** — verify profile system works
- [ ] **Run `python3 bin/transition.py config diff`** — verify defaults are defined
- [ ] **Read through each skill** — verify paths are correct and no private context remains
- [ ] **Read README.md** — verify it reads cleanly for a new user
