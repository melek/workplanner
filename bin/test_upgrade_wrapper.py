#!/usr/bin/env python3
"""Regression check — the `wpl` wrapper survives a plugin upgrade.

`~/.workplanner/bin/wpl` hardcodes the versioned plugin path it was
written from (`.../workplanner/<version>/bin/transition.py`). Claude Code
prunes old version directories after an upgrade, which leaves the wrapper
pointing at a deleted file until something runs the new version's CLI.
The SessionStart hook now runs `$CLAUDE_PLUGIN_ROOT/bin/transition.py`
directly, which rewrites the wrapper, so the first session after an
upgrade repairs it.

Run: `python3 bin/test_upgrade_wrapper.py`
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
HOOK = REPO / "bin" / "session-hook.sh"
TRANSITION = REPO / "bin" / "transition.py"


def main():
    base = tempfile.mkdtemp(prefix="wpl-upgrade-")
    try:
        wpl_root = Path(base) / "wpl"
        profile_dir = wpl_root / "profiles" / "test"
        (profile_dir / "session").mkdir(parents=True)
        (profile_dir / "config.json").write_text('{"workspaces": [], "timezone": "UTC"}\n')
        (profile_dir / "session" / "current-session.json").write_text(json.dumps({
            "date": date.today().isoformat(), "week": "W43",
            "checkpoint": "assembly_complete", "eod_target": "18:00",
            "current_task_index": 0, "tasks": [
                {"uid": "aaaa0001", "title": "Upgraded task", "status": "in_progress",
                 "estimate_min": 30, "source": "manual", "started_at": "09:00"}],
            "inbox_items": [], "headlines": [],
        }))

        # A wrapper left behind by a pruned older version.
        stale = Path(base) / "cache" / "workplanner" / "1.0.0-old" / "bin" / "transition.py"
        wrapper = wpl_root / "bin" / "wpl"
        wrapper.parent.mkdir(parents=True)
        wrapper.write_text(f'#!/bin/bash\nexec python3 {stale} "$@"\n')
        wrapper.chmod(0o755)
        broken = subprocess.run([str(wrapper), "status"], capture_output=True, text=True)
        assert broken.returncode != 0, "U1 setup: stale wrapper should fail"

        env = {k: v for k, v in os.environ.items() if not k.startswith("WPL_")}
        env.update({"WPL_ROOT": str(wpl_root), "CLAUDE_PLUGIN_ROOT": str(REPO)})
        hook = subprocess.run(["bash", str(HOOK)], cwd=base, env=env,
                              input="{}", capture_output=True, text=True, timeout=5)
        assert "Upgraded task" in hook.stdout, \
            f"U1: hook didn't inject the workplan: out={hook.stdout!r} err={hook.stderr!r}"
        assert str(TRANSITION.resolve()) in wrapper.read_text(), \
            f"U1: wrapper not repointed: {wrapper.read_text()!r}"
        fixed = subprocess.run([str(wrapper), "status"], capture_output=True, text=True,
                               cwd=base, env={**env, "WPL_CHILD": "1"})
        assert fixed.returncode == 0 and "Upgraded task" in fixed.stdout, \
            f"U1: repaired wrapper doesn't work: {fixed.stdout!r} {fixed.stderr!r}"
        print("U1: SessionStart hook repairs a wrapper left by a pruned version [OK]")

        print("\nAll U1 scenarios passed.")
        return 0
    finally:
        shutil.rmtree(base, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
