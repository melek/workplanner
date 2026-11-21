#!/usr/bin/env python3
"""Regression suite for issues #64, #65 and #69.

- #64: the SessionStart hint reads as a fallback, not a PATH setup step.
- #65: the post-wpl hook doesn't repeat the task list, and fires only on
  real wpl calls, not on commands whose arguments mention wpl paths.
- #69: `wpl backlog --edit` keeps the estimate when --est is omitted.

Run: `python3 bin/test_issue_64_65_69.py`
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_issue_60_61 import Ctx, _write_session, _task  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
HOOK = REPO / "bin" / "post-tool-use-hook.sh"
SESSION_HOOK = REPO / "bin" / "session-hook.sh"
TRANSITION = REPO / "bin" / "transition.py"


def _hook(c, command):
    wrapper = Path(c.base) / "wpl-bin"
    wrapper.write_text(f"#!/bin/sh\nexec {sys.executable} {TRANSITION} \"$@\"\n")
    wrapper.chmod(0o755)
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": command}})
    proc = subprocess.run(["bash", str(HOOK)], input=payload, cwd=c.base,
                          env={**os.environ, **c.env, "WPL_BIN": str(wrapper)},
                          capture_output=True, text=True)
    return proc.stdout


def test_64_hint_is_a_fallback():
    text = (SESSION_HOOK.parent / "session_context.py").read_text()  # hint moved there in #82
    assert "add ~/.workplanner/bin to PATH" not in text
    assert "if it is not found, call ~/.workplanner/bin/wpl" in text


def test_65_hook_does_not_repeat_list():
    if not shutil.which("jq"):
        print("     (skipped: jq not installed)")
        return
    with Ctx() as c:
        _write_session(c.profile_dir, [_task("aaaa0001", "Alpha"), _task("bbbb0002", "Beta")], 0)
        out = _hook(c, "wpl status")
        assert "Done: 0/2" in out, out
        assert "Beta" not in out, f"task list repeated:\n{out}"


def test_65_hook_matches_real_calls_only():
    if not shutil.which("jq"):
        print("     (skipped: jq not installed)")
        return
    with Ctx() as c:
        _write_session(c.profile_dir, [_task("aaaa0001", "Alpha")], 0)
        fires = ["wpl done t1", "cd /tmp && wpl status", "FOO=1 ~/.workplanner/bin/wpl status",
                 "python3 /x/workplanner/bin/transition.py status", "true; wpl status",
                 "echo hi\nwpl status"]
        silent = ["gh issue create --body 'call ~/.workplanner/bin/wpl here'",
                  "sed -n 1,5p /x/workplanner/bin/transition.py",
                  "git commit -m 'wpl status fix'", "echo wpl",
                  # Quoted text and heredoc bodies are data, even with separators inside.
                  "git commit -m 'fix; wpl status'",
                  "git commit -q -F - <<'MSG'\nobject; wpl add --notes-file\nMSG\ngit push",
                  "cat <<EOF\nwpl done t1\nEOF"]
        for cmd in fires:
            assert "Done:" in _hook(c, cmd), f"should fire: {cmd}"
        for cmd in silent:
            assert _hook(c, cmd).strip() == "", f"should stay silent: {cmd}"


def test_69_backlog_edit_keeps_estimate():
    with Ctx() as c:
        _write_session(c.profile_dir, [])
        c.ok("backlog", "Example item", "--est", "10", "--target", "2099-01-10")
        items = json.loads((c.profile_dir / "backlog.json").read_text())["items"]
        uid = items[0]["uid"]
        c.ok("backlog", "--edit", uid, "--target", "2099-01-12")
        item = json.loads((c.profile_dir / "backlog.json").read_text())["items"][0]
        assert item["estimate_min"] == 10, item
        assert item["target_date"] == "2099-01-12", item
        c.ok("backlog", "--edit", uid, "--est", "45")
        assert json.loads((c.profile_dir / "backlog.json").read_text())["items"][0]["estimate_min"] == 45


def test_69_backlog_add_default_still_30():
    with Ctx() as c:
        _write_session(c.profile_dir, [])
        c.ok("backlog", "No estimate given")
        assert json.loads((c.profile_dir / "backlog.json").read_text())["items"][0]["estimate_min"] == 30


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"ok  {t.__name__}")
    print(f"{len(tests)} passed")
