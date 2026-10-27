#!/usr/bin/env python3
"""Regression suite for issue #59 — handoff writes keep omitted sections.

A session that wrote its handoff in more than one pass used to lose data:
any section flag left out of a later `write` deleted that session's
earlier contribution to it. Now an omitted section is kept, an explicit
empty value clears it, and `--replace` restores the old full-rewrite
behavior (which /eod uses so a re-run can clear a section).

Run: `python3 bin/test_issue_59.py`
"""
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
HANDOFF = REPO / "bin" / "handoff.py"
DATE = "2026-01-05"


def _bootstrap(base):
    wpl_root = Path(base) / "wpl"
    profile_dir = wpl_root / "profiles" / "test"
    (profile_dir / "session").mkdir(parents=True)
    (profile_dir / "config.json").write_text('{"workspaces": [], "timezone": "UTC"}\n')
    (profile_dir / "backlog.json").write_text('{"schema_version": 1, "items": []}\n')
    return wpl_root, profile_dir


def _write(base, env, sid, *args):
    cmd = [sys.executable, str(HANDOFF), "write", "--date", DATE, "--session-id", sid, *args]
    proc = subprocess.run(cmd, cwd=str(base), env={**os.environ, **env},
                          capture_output=True, text=True)
    if proc.returncode != 0:
        raise AssertionError(f"write failed: {cmd}\n{proc.stdout}\n{proc.stderr}")
    return Path(proc.stdout.strip())


def _fresh():
    base = tempfile.mkdtemp(prefix="wpl-issue59-")
    wpl_root, profile_dir = _bootstrap(base)
    env = {"WPL_ROOT": str(wpl_root), "WPL_CHILD": "1"}
    return base, env, profile_dir / "handoffs" / f"{DATE}.md"


def test_partial_write_keeps_other_sections():
    base, env, path = _fresh()
    try:
        _write(base, env, "s1", "--trajectory", "TRAJ-1", "--context", "CTX-1")
        _write(base, env, "s1", "--open-questions", "Q-1")
        text = path.read_text()
        for needle in ("TRAJ-1", "CTX-1", "Q-1"):
            assert needle in text, f"{needle} missing after partial write:\n{text}"
    finally:
        shutil.rmtree(base)


def test_empty_string_clears_section():
    base, env, path = _fresh()
    try:
        _write(base, env, "s1", "--trajectory", "TRAJ-1", "--context", "CTX-1")
        _write(base, env, "s1", "--context", "")
        text = path.read_text()
        assert "CTX-1" not in text, f"explicit empty did not clear:\n{text}"
        assert "## Context for tomorrow" not in text, f"empty section heading kept:\n{text}"
        assert "TRAJ-1" in text, f"trajectory lost:\n{text}"
    finally:
        shutil.rmtree(base)


def test_empty_deferred_clears_section():
    base, env, path = _fresh()
    try:
        _write(base, env, "s1", "--trajectory", "TRAJ-1", "--deferred", "DEF-1")
        _write(base, env, "s1", "--deferred", "")
        text = path.read_text()
        assert "DEF-1" not in text, f"--deferred \"\" did not clear:\n{text}"
        assert "TRAJ-1" in text
    finally:
        shutil.rmtree(base)


def test_replace_drops_omitted():
    base, env, path = _fresh()
    try:
        _write(base, env, "s1", "--trajectory", "TRAJ-1", "--context", "CTX-1")
        _write(base, env, "s1", "--replace", "--trajectory", "TRAJ-2")
        text = path.read_text()
        assert "TRAJ-2" in text and "TRAJ-1" not in text, text
        assert "CTX-1" not in text, f"--replace kept an omitted section:\n{text}"
    finally:
        shutil.rmtree(base)


def test_other_sessions_untouched():
    base, env, path = _fresh()
    try:
        _write(base, env, "s2", "--trajectory", "OTHER-X")
        before = path.read_text()
        s2_block = before[before.index("### s2"):].split("\n\n")[0]
        _write(base, env, "s1", "--replace", "--trajectory", "MINE-Y")
        after = path.read_text()
        assert s2_block in after, f"s2 block changed:\n{after}"
        assert "MINE-Y" in after
    finally:
        shutil.rmtree(base)


def test_unknown_section_preserved():
    base, env, path = _fresh()
    try:
        _write(base, env, "s1", "--trajectory", "TRAJ-1")
        path.write_text(path.read_text().rstrip() + "\n\n## Notes\nfree text\n")
        _write(base, env, "s1", "--open-questions", "Q-1")
        text = path.read_text()
        assert "## Notes" in text and "free text" in text, f"unknown section lost:\n{text}"
        assert "TRAJ-1" in text and "Q-1" in text
    finally:
        shutil.rmtree(base)


TESTS = [
    test_partial_write_keeps_other_sections,
    test_empty_string_clears_section,
    test_empty_deferred_clears_section,
    test_replace_drops_omitted,
    test_other_sessions_untouched,
    test_unknown_section_preserved,
]


def main():
    failed = 0
    for t in TESTS:
        try:
            t()
            print(f"ok   {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL {t.__name__}: {e}")
    print(f"{len(TESTS) - failed}/{len(TESTS)} passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
