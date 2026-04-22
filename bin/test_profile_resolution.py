#!/usr/bin/env python3
"""Verification script for path-based profile resolution (issue #10).

Runs T1–T10 from the issue brief against an isolated WPL_ROOT under
/tmp. Exit 0 on success, 1 on any failed assertion.

Usage:
    python3 bin/test_profile_resolution.py

The script spawns the CLI as a subprocess with a sanitized environment
so it never touches the user's real ~/.workplanner state.
"""

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


BIN_DIR = Path(__file__).resolve().parent
TRANSITION = BIN_DIR / "transition.py"


def _run(cwd, env, *args, check_exit=None):
    """Invoke transition.py as a subprocess. Returns (rc, stdout, stderr)."""
    full_env = dict(os.environ)
    # Drop any ambient WPL_* vars that might leak from the parent shell.
    for k in [k for k in full_env if k.startswith("WPL_")]:
        del full_env[k]
    full_env.update(env)
    # Force non-interactive: no TTY, WPL_CHILD=1 — the CLI must not prompt.
    full_env.setdefault("WPL_CHILD", "1")
    proc = subprocess.run(
        [sys.executable, str(TRANSITION), *args],
        cwd=str(cwd),
        env=full_env,
        capture_output=True,
        text=True,
    )
    if check_exit is not None:
        assert proc.returncode == check_exit, (
            f"expected exit {check_exit}, got {proc.returncode}\n"
            f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
        )
    return proc.returncode, proc.stdout, proc.stderr


def _whoami_profile(stdout):
    """Parse the 'profile: NAME' line from `wpl profile whoami` output."""
    for line in stdout.splitlines():
        if line.startswith("profile:"):
            return line.split(":", 1)[1].strip()
    return None


def main():
    # Isolate: build a throwaway root tree under /tmp.
    base = tempfile.mkdtemp(prefix="wpl-issue10-")
    try:
        wpl_root = Path(base) / ".wpl"
        hal_dir = Path(base) / "hal"
        home_dir = Path(base) / "home"
        hal_sub = hal_dir / "subfolder"
        deep_dir = Path(base) / "deep" / "path"
        hal_extra = Path(base) / "hal-extra"
        for p in (hal_sub, home_dir, deep_dir, hal_extra):
            p.mkdir(parents=True, exist_ok=True)
        # Resolve the realpath so comparisons match the resolver's output.
        base_real = os.path.realpath(base)

        env = {"WPL_ROOT": str(wpl_root)}

        # Bootstrap: two profiles pinned to their own workspaces.
        _run(Path(base), env, "profile", "create", "hal",
             "--workspace", str(hal_dir), check_exit=0)
        _run(Path(base), env, "profile", "create", "home",
             "--workspace", str(home_dir), check_exit=0)

        # ── T1: cwd-based resolution picks the right profile ─────────
        _, out, _ = _run(hal_sub, env, "profile", "whoami", check_exit=0)
        assert _whoami_profile(out) == "hal", f"T1a: got {out!r}"
        _, out, _ = _run(home_dir, env, "profile", "whoami", check_exit=0)
        assert _whoami_profile(out) == "home", f"T1b: got {out!r}"
        print("T1: cwd-based resolution [OK]")

        # ── T2: no match fails with diagnostic naming known profiles ─
        rc, out, err = _run(Path("/tmp"), env, "status")
        assert rc == 1, f"T2: expected rc=1, got {rc}"
        assert "is not associated with any profile" in err, \
            f"T2: expected diagnostic in stderr, got {err!r}"
        assert "hal" in err and "home" in err, \
            f"T2: expected profile names in stderr, got {err!r}"
        print("T2: no-match diagnostic [OK]")

        # ── T3: longest-prefix wins over a catch-all workspace ───────
        _run(Path(base), env, "profile", "create", "system",
             "--workspace", "/", check_exit=0)
        # Before adding 'user', /tmp/.../deep/path resolves to 'system'.
        _, out, _ = _run(deep_dir, env, "profile", "whoami", check_exit=0)
        assert _whoami_profile(out) == "system", f"T3 pre: got {out!r}"
        _run(Path(base), env, "profile", "create", "user",
             "--workspace", str(Path(base)), check_exit=0)
        # Now 'user' (more specific than '/') should win.
        _, out, _ = _run(deep_dir, env, "profile", "whoami", check_exit=0)
        assert _whoami_profile(out) == "user", f"T3: got {out!r}"
        print("T3: longest-prefix wins [OK]")

        # ── T4: identical-path overlap is rejected ───────────────────
        _run(Path(base), env, "profile", "create", "bar",
             "--workspace", "/tmp/issue10/x/y", check_exit=0)
        rc, out, err = _run(Path(base), env, "profile", "create", "foo",
                            "--workspace", "/tmp/issue10/x/y")
        assert rc == 1, f"T4: expected rc=1, got {rc}"
        assert "already claimed" in err, f"T4: got stderr {err!r}"
        assert not (wpl_root / "profiles" / "foo").exists(), \
            "T4: failed create left a partial profile on disk"
        print("T4: overlap rejected atomically [OK]")

        # ── T5: WPL_PROFILE env var overrides path resolution ────────
        env5 = dict(env)
        env5["WPL_PROFILE"] = "home"
        _, out, _ = _run(hal_dir, env5, "profile", "whoami", check_exit=0)
        assert _whoami_profile(out) == "home", f"T5: got {out!r}"
        print("T5: $WPL_PROFILE override [OK]")

        # ── T6: whoami reports match + workspace path ────────────────
        _, out, _ = _run(hal_dir, env, "profile", "whoami", check_exit=0)
        assert "profile: hal" in out
        assert "resolved via: path match" in out
        assert f"matched workspace: {os.path.realpath(hal_dir)}" in out, \
            f"T6: got {out!r}"
        print("T6: whoami reports matched workspace [OK]")

        # ── T7: `switch` still flips the symlink and prints deprecation
        _, out, _ = _run(Path(base), env, "profile", "switch", "home",
                         check_exit=0)
        assert "Switched to profile 'home'" in out
        assert "deprecat" in out.lower() or "backward compat" in out.lower(), \
            f"T7: expected deprecation note, got {out!r}"
        active = wpl_root / "profiles" / "active"
        assert active.is_symlink(), "T7: active is not a symlink"
        assert os.readlink(str(active)) == "home", \
            f"T7: symlink points to {os.readlink(str(active))!r}"
        print("T7: switch + deprecation note [OK]")

        # ── T8: path-component matching — hal-extra must not match hal
        # To isolate: remove 'user' workspace so nothing broader than
        # '/' could mask a bad match.
        _run(Path(base), env, "profile", "disassociate", "user",
             str(Path(base)), check_exit=0)
        _, out, _ = _run(hal_extra, env, "profile", "whoami", check_exit=0)
        name = _whoami_profile(out)
        assert name == "system", \
            f"T8: {hal_extra} must not match 'hal'; got {name!r}"
        # Restore for subsequent tests.
        _run(Path(base), env, "profile", "associate", "user",
             str(Path(base)), check_exit=0)
        print("T8: path-component matching [OK]")

        # ── T9: single-profile fallback ──────────────────────────────
        solo_root = Path(base) / ".wpl-solo"
        env9 = {"WPL_ROOT": str(solo_root)}
        _run(Path(base), env9, "profile", "create", "solo", check_exit=0)
        _, out, _ = _run(Path("/tmp"), env9, "profile", "whoami",
                         check_exit=0)
        assert _whoami_profile(out) == "solo", f"T9: got {out!r}"
        assert "single-profile fallback" in out, f"T9: reason? {out!r}"
        print("T9: single-profile fallback [OK]")

        # ── T10: concurrent-session isolation — switch doesn't leak ──
        # Session A resolves inside hal. Session B (from home dir)
        # flips the active symlink. Session A's next call must still
        # resolve to hal.
        _, out, _ = _run(hal_dir, env, "profile", "whoami", check_exit=0)
        assert _whoami_profile(out) == "hal", f"T10 pre: got {out!r}"
        _run(home_dir, env, "profile", "switch", "hal", check_exit=0)
        # Without path-based resolution, home's next call would now
        # resolve to hal via the symlink. With path-based, it stays home.
        _, out, _ = _run(home_dir, env, "profile", "whoami", check_exit=0)
        assert _whoami_profile(out) == "home", \
            f"T10: concurrent-session isolation broken; got {out!r}"
        print("T10: concurrent-session isolation [OK]")

        # ── Bonus: --profile CLI flag beats env var ──────────────────
        env_b = dict(env)
        env_b["WPL_PROFILE"] = "system"
        _, out, _ = _run(hal_dir, env_b, "--profile", "home",
                         "profile", "whoami", check_exit=0)
        assert _whoami_profile(out) == "home"
        assert "--profile flag" in out
        print("Bonus: --profile flag > $WPL_PROFILE [OK]")

        print("\nAll scenarios passed.")
        return 0
    finally:
        shutil.rmtree(base, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
