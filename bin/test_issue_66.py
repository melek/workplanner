#!/usr/bin/env python3
"""Regression suite for issue #66 — long text from a file or stdin.

`handoff.py write --from-json PATH|-` takes the sections as one JSON object;
`wpl add --notes-file` and `wpl edit --notes-file / --append-notes-file` read
notes from a file or stdin. Text with quotes, backticks and `$` must arrive
verbatim.

Run: `python3 bin/test_issue_66.py`
"""
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_issue_60_61 import Ctx, _write_session, _task  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
HANDOFF = REPO / "bin" / "handoff.py"
TRANSITION = REPO / "bin" / "transition.py"
DATE = "2026-01-05"
TRICKY = "It's `wpl status` and $HOME, \"quoted\", $(not run) and a [link](https://x.y/?a=1&b=2)"


def _handoff(c, *args, stdin=None):
    proc = subprocess.run([sys.executable, str(HANDOFF), *args], cwd=c.base, input=stdin,
                          env={**os.environ, **c.env}, capture_output=True, text=True)
    return proc.returncode, proc.stdout, proc.stderr


def _wpl(c, *args, stdin=None):
    proc = subprocess.run([sys.executable, str(TRANSITION), *args], cwd=c.base, input=stdin,
                          env={**os.environ, **c.env}, capture_output=True, text=True)
    return proc.returncode, proc.stdout, proc.stderr


def _hpath(c):
    return c.profile_dir / "handoffs" / f"{DATE}.md"


def test_from_json_stdin_is_verbatim():
    with Ctx() as c:
        payload = json.dumps({"trajectory": TRICKY, "context": "line 1\nline 2",
                              "deferred": [{"title": "T", "uid": "u1", "reason": "waiting on 'legal'"}],
                              "open_questions": "Q?"})
        rc, out, err = _handoff(c, "write", "--date", DATE, "--session-id", "s1", "--replace",
                                "--from-json", "-", stdin=payload)
        assert rc == 0, err
        text = _hpath(c).read_text()
        for needle in (TRICKY, "line 1\nline 2", "waiting on 'legal'", "Q?"):
            assert needle in text, f"{needle!r} missing:\n{text}"


def test_from_json_file_keeps_and_clears():
    with Ctx() as c:
        _handoff(c, "write", "--date", DATE, "--session-id", "s1",
                 "--trajectory", "TRAJ", "--context", "CTX", "--open-questions", "Q")
        f = Path(c.base) / "h.json"
        f.write_text(json.dumps({"context": "NEW-CTX", "open_questions": ""}))
        rc, _, err = _handoff(c, "write", "--date", DATE, "--session-id", "s1", "--from-json", str(f))
        assert rc == 0, err
        text = _hpath(c).read_text()
        assert "TRAJ" in text and "NEW-CTX" in text and "CTX\n" not in text.replace("NEW-CTX", "")
        assert "## Open questions" not in text, text


def test_from_json_replace_drops_absent():
    with Ctx() as c:
        _handoff(c, "write", "--date", DATE, "--session-id", "s1", "--trajectory", "TRAJ", "--context", "CTX")
        rc, _, err = _handoff(c, "write", "--date", DATE, "--session-id", "s1", "--replace",
                              "--from-json", "-", stdin='{"context": "ONLY"}')
        assert rc == 0, err
        text = _hpath(c).read_text()
        assert "ONLY" in text and "TRAJ" not in text, text


def test_from_json_rejects_bad_input():
    with Ctx() as c:
        cases = [("not json", "not valid JSON"), ("[1]", "must be a JSON object"),
                 ('{"contxt": "typo"}', "unknown keys"), ('{"context": 5}', "must be a string")]
        for stdin, msg in cases:
            rc, _, err = _handoff(c, "write", "--date", DATE, "--from-json", "-", stdin=stdin)
            assert rc != 0 and msg in err, (stdin, err)
        rc, _, err = _handoff(c, "write", "--date", DATE, "--context", "x", "--from-json", "-", stdin="{}")
        assert rc != 0 and "can't be combined" in err, err
        assert not _hpath(c).exists()


def test_add_notes_file_stdin():
    with Ctx() as c:
        _write_session(c.profile_dir, [_task("aaaa0001", "A")], 0)
        rc, out, err = _wpl(c, "add", "New task", "--notes-file", "-", stdin=TRICKY + "\n")
        assert rc == 0, err
        new = [t for t in c.tasks() if t["title"] == "New task"][0]
        assert new["notes"] == TRICKY, new


def test_edit_notes_files():
    with Ctx() as c:
        _write_session(c.profile_dir, [_task("aaaa0001", "A", notes="old")], 0)
        f = Path(c.base) / "n.md"
        f.write_text("first\nsecond\n")
        rc, _, err = _wpl(c, "edit", "t1", "--append-notes-file", str(f))
        assert rc == 0, err
        assert c.tasks()[0]["notes"] == "old | first\nsecond"
        rc, _, err = _wpl(c, "edit", "t1", "--notes-file", "-", stdin=TRICKY)
        assert rc == 0, err
        assert c.tasks()[0]["notes"] == TRICKY


def test_notes_file_conflicts_and_missing():
    with Ctx() as c:
        _write_session(c.profile_dir, [_task("aaaa0001", "A")], 0)
        rc, _, err = _wpl(c, "edit", "t1", "--notes", "x", "--notes-file", "-", stdin="y")
        assert rc != 0, "notes and notes-file together should be refused"
        rc, out, err = _wpl(c, "edit", "t1", "--notes-file", "/nonexistent/nope.md")
        assert rc != 0 and "Can't read --notes-file" in (out + err), (out, err)
        assert "notes" not in c.tasks()[0]


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"ok  {t.__name__}")
    print(f"{len(tests)} passed")
