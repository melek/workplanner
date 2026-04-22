#!/usr/bin/env python3
"""Local-handoff doc read/write for workplanner.

Writes per-day markdown handoff files under
    ~/.workplanner/profiles/<resolved-profile-name>/handoffs/YYYY-MM-DD.md

The file is split into named sections. Each section is further sub-divided
by *session identifier* so concurrent sessions (e.g. dispatched tmux panes,
multiple Claude instances) can each contribute without clobbering the others.

On write, only the invoking session's own sub-sections are rewritten; other
sessions' contributions are preserved verbatim. On read, sections are
aggregated across all session sub-sections.

CLI surface (used by skills):

    # Write / update today's handoff for this session
    python3 handoff.py write \
        --session-id s-xxx \
        --trajectory "bulleted markdown..." \
        --deferred-json '[{"title":"...","uid":"...","reason":"..."}]' \
        --open-questions "..." \
        --context "..."

    # Read today's handoff (or any specific date), print a JSON blob
    python3 handoff.py read [--date YYYY-MM-DD]

    # Print the path for today's handoff (doesn't create it)
    python3 handoff.py path [--date YYYY-MM-DD]

The `write` command is idempotent within a session-id: re-running it
overwrites that session's sub-sections and leaves other sessions alone.

Python 3.9+ stdlib only.
"""

import argparse
import hashlib
import json
import os
import re
import sys
import time
from datetime import date as _date, datetime
from pathlib import Path

# Import minimally from transition.py (same dir) so we share profile
# resolution and timezone logic. Avoid importing anything that mutates state.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from transition import (  # noqa: E402
    resolve_paths,
    load_config,
    local_today,
)


# Ordered list of handoff sections. Writers update only these top-level
# headings; anything outside the recognised set is preserved verbatim
# (a human-authored free-text block, for instance, stays untouched).
SECTIONS = [
    "Session trajectory",
    "Deferred with reasons",
    "Open questions",
    "Context for tomorrow",
]


# ── Session identifier ──────────────────────────────────────────────


def session_id():
    """Pick a stable-ish identifier for this *invocation's* session.

    Priority:
      1. $CLAUDE_SESSION_ID if set (Claude Code exposes this in some
         contexts; workplanner treats it as authoritative when present).
      2. $TMUX_PANE if running inside tmux (stable for the life of the pane).
      3. Hash of python process start time (fallback; will not match across
         re-invocations but prevents collisions with other sessions).

    The ID is for disambiguation only. If it's not stable across
    re-invocations, we accept some duplication over data loss.
    """
    claude = os.environ.get("CLAUDE_SESSION_ID", "").strip()
    if claude:
        # Truncate so section headers stay scannable.
        return f"claude-{claude[:12]}"
    pane = os.environ.get("TMUX_PANE", "").strip()
    if pane:
        # %0, %1, etc. — strip the leading % so the heading reads cleanly.
        return f"pane-{pane.lstrip('%')}"
    # Fallback: hash the start time. Stable across the life of this process.
    try:
        boot = str(time.time()).encode()
        h = hashlib.sha1(boot).hexdigest()[:8]
        return f"proc-{h}"
    except Exception:
        return "proc-unknown"


# ── Path resolution ─────────────────────────────────────────────────


def handoff_path_for(target_date=None):
    """Return the path to the handoff file for the given date (default today).

    Uses the *resolved* profile name, never the `active` alias in the path.
    """
    paths = resolve_paths()
    if target_date is None:
        target_date = local_today(load_config())
    if isinstance(target_date, (datetime, _date)):
        date_str = target_date.isoformat()[:10]
    else:
        date_str = str(target_date)
    return paths.HANDOFFS / f"{date_str}.md"


# ── Parsing ─────────────────────────────────────────────────────────


SECTION_RE = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)
SUBSECTION_RE = re.compile(r"^###\s+(.+?)\s*$", re.MULTILINE)


def _parse_sections(text):
    """Parse the handoff markdown into a dict: section name → raw body.

    Returns (ordered_keys, body_map, preamble). The preamble is whatever
    appears before the first `## ` heading (typically the `# Handoff — DATE`
    line). Unknown sections are retained; only known sections are updated on
    write.
    """
    if not text:
        return [], {}, ""
    # Find all `## Heading` positions
    matches = list(SECTION_RE.finditer(text))
    if not matches:
        return [], {}, text
    preamble = text[: matches[0].start()]
    ordered = []
    body = {}
    for i, m in enumerate(matches):
        name = m.group(1).strip()
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        ordered.append(name)
        body[name] = text[start:end]
    return ordered, body, preamble


def _parse_subsections(section_body):
    """Parse a section body into {session-id-heading: raw_body}.

    Returns (ordered_ids, body_map, preamble). Preamble is any content
    between the section heading and the first `###` sub-heading (usually
    empty or a short note).
    """
    matches = list(SUBSECTION_RE.finditer(section_body))
    if not matches:
        return [], {}, section_body
    preamble = section_body[: matches[0].start()]
    ordered = []
    body = {}
    for i, m in enumerate(matches):
        sid = m.group(1).strip()
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(section_body)
        ordered.append(sid)
        body[sid] = section_body[start:end]
    return ordered, body, preamble


# ── Reading ─────────────────────────────────────────────────────────


def read_handoff(target_date=None):
    """Read a handoff file (default today). Return a dict suitable for skills.

    Shape:
        {
          "path": "/abs/path/YYYY-MM-DD.md",
          "date": "YYYY-MM-DD",
          "exists": True/False,
          "sections": {
            "Session trajectory": {
              "<session-id>": "raw markdown body",
              ...
            },
            ...
          },
          "raw": "...",          # the full file, for fallback
          "deferred": [          # best-effort parse of "Deferred with reasons"
            {"title": "...", "uid": "...", "reason": "...", "session": "..."},
            ...
          ]
        }
    """
    path = handoff_path_for(target_date)
    out = {
        "path": str(path),
        "date": path.stem,
        "exists": path.exists(),
        "sections": {name: {} for name in SECTIONS},
        "raw": "",
        "deferred": [],
    }
    if not path.exists():
        return out
    text = path.read_text()
    out["raw"] = text
    _, bodies, _ = _parse_sections(text)
    for name in SECTIONS:
        if name not in bodies:
            continue
        _, subs, _ = _parse_subsections(bodies[name])
        for sid, body in subs.items():
            out["sections"][name][sid] = body.rstrip() + "\n"

    # Best-effort extraction of deferred items. Pattern per-line:
    #   - **<title>** (<uid>): <reason>
    # Everything else is returned as raw text under sections → skills can
    # still display it as-is if the line doesn't match.
    deferred_section = out["sections"].get("Deferred with reasons", {})
    line_re = re.compile(r"^\s*-\s+\*\*(?P<title>[^*]+)\*\*\s*(?:\(([^)]+)\))?\s*:\s*(?P<reason>.+?)\s*$")
    for sid, body in deferred_section.items():
        for line in body.splitlines():
            m = line_re.match(line)
            if m:
                out["deferred"].append({
                    "title": m.group("title").strip(),
                    "uid": (m.group(2) or "").strip(),
                    "reason": m.group("reason").strip(),
                    "session": sid,
                })
    return out


# ── Writing ─────────────────────────────────────────────────────────


def _render_session_sub(session_id_str, body):
    """Render a single session's sub-section body under a `### <sid>` heading."""
    body = (body or "").strip()
    if not body:
        return ""
    return f"### {session_id_str}\n\n{body}\n\n"


def _render_deferred_body(deferred_items):
    """Turn a list of {title, uid, reason} dicts into the canonical markdown form.

    The parser in read_handoff() pairs with this format — keep them in sync.
    """
    if not deferred_items:
        return ""
    lines = []
    for item in deferred_items:
        title = (item.get("title") or "").strip() or "(untitled)"
        uid = (item.get("uid") or "").strip()
        reason = (item.get("reason") or "").strip() or "(no reason given)"
        uid_tag = f" ({uid})" if uid else ""
        lines.append(f"- **{title}**{uid_tag}: {reason}")
    return "\n".join(lines)


def write_handoff(
    session_id_str,
    trajectory=None,
    deferred=None,
    open_questions=None,
    context=None,
    target_date=None,
):
    """Merge-by-section write for today's handoff.

    Only this session's `### <session_id>` sub-sections are rewritten.
    Other sessions' sub-sections are preserved verbatim. Unknown top-level
    sections (outside SECTIONS) are also preserved — a human could drop a
    free-text note at the end and it would survive future EOD writes.

    Any of trajectory / deferred / open_questions / context that are None
    or empty are treated as "this session has no contribution to that
    section this invocation" — the session's existing sub-section (if any)
    is removed. If the whole section would become empty across all sessions,
    the section is dropped entirely.

    `deferred` may be a list of {title, uid, reason} dicts OR a
    pre-rendered markdown string. Dicts get rendered via
    _render_deferred_body().
    """
    path = handoff_path_for(target_date)
    path.parent.mkdir(parents=True, exist_ok=True)

    existing_text = path.read_text() if path.exists() else ""
    ordered, bodies, preamble = _parse_sections(existing_text)

    # If file is new, build an initial preamble.
    if not preamble and not ordered:
        date_str = path.stem
        preamble = f"# Handoff — {date_str}\n\n"

    # Build the new contributions for this session.
    new_contributions = {
        "Session trajectory": _normalise_body(trajectory),
        "Deferred with reasons": (
            _render_deferred_body(deferred)
            if isinstance(deferred, list) else _normalise_body(deferred)
        ),
        "Open questions": _normalise_body(open_questions),
        "Context for tomorrow": _normalise_body(context),
    }

    # For each known section, splice this session's sub-section in/out.
    # Unknown sections (not in SECTIONS) pass through unchanged.
    updated_ordered = list(ordered)  # preserve existing section order + unknowns
    updated_bodies = dict(bodies)

    for section_name in SECTIONS:
        sub_ids, subs, sub_preamble = _parse_subsections(bodies.get(section_name, ""))
        new_body = new_contributions[section_name]
        if new_body:
            subs[session_id_str] = new_body
            if session_id_str not in sub_ids:
                sub_ids.append(session_id_str)
        else:
            # This session has nothing to say → drop its entry.
            subs.pop(session_id_str, None)
            sub_ids = [s for s in sub_ids if s != session_id_str]

        if not subs:
            # Section now empty; drop it from the file.
            if section_name in updated_bodies:
                del updated_bodies[section_name]
                updated_ordered = [s for s in updated_ordered if s != section_name]
            continue

        # Re-render the section body preserving sub-id order.
        parts = []
        for sid in sub_ids:
            parts.append(_render_session_sub(sid, subs[sid]))
        section_text = (sub_preamble.strip() + "\n\n" if sub_preamble.strip() else "") + "".join(parts)
        updated_bodies[section_name] = "\n" + section_text  # leading newline after the `## Heading`
        if section_name not in updated_ordered:
            updated_ordered.append(section_name)

    # Reorder: put known sections first in canonical order, then any unknowns
    # (anything the writer didn't touch) in their original relative order.
    known_in_file = [s for s in SECTIONS if s in updated_ordered]
    unknown = [s for s in updated_ordered if s not in SECTIONS]
    final_order = known_in_file + unknown

    # Rebuild the full text.
    out = preamble.rstrip() + "\n\n" if preamble.strip() else ""
    for name in final_order:
        body = updated_bodies.get(name, "").rstrip()
        out += f"## {name}\n{body}\n\n"

    # Atomic write.
    target = path.resolve() if path.exists() else path
    tmp = str(target) + ".tmp"
    with open(tmp, "w") as f:
        f.write(out.rstrip() + "\n")
    os.rename(tmp, str(target))
    return path


def _normalise_body(body):
    """Normalise the body to a trimmed string, or empty if None/whitespace."""
    if body is None:
        return ""
    if isinstance(body, str):
        return body.strip()
    # List of lines? Join them.
    if isinstance(body, list):
        return "\n".join(str(x) for x in body).strip()
    return str(body).strip()


# ── CLI ─────────────────────────────────────────────────────────────


def _cmd_write(args):
    deferred = None
    if args.deferred_json:
        try:
            deferred = json.loads(args.deferred_json)
        except json.JSONDecodeError as e:
            print(f"error: --deferred-json is not valid JSON: {e}", file=sys.stderr)
            sys.exit(1)
    elif args.deferred:
        deferred = args.deferred

    sid = args.session_id or session_id()
    path = write_handoff(
        session_id_str=sid,
        trajectory=args.trajectory,
        deferred=deferred,
        open_questions=args.open_questions,
        context=args.context,
        target_date=args.date,
    )
    print(str(path))


def _cmd_read(args):
    data = read_handoff(target_date=args.date)
    print(json.dumps(data, indent=2))


def _cmd_path(args):
    print(str(handoff_path_for(args.date)))


def main():
    parser = argparse.ArgumentParser(
        prog="handoff.py",
        description="workplanner local handoff doc read/write.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    w = sub.add_parser("write", help="Write or update today's handoff for a session.")
    w.add_argument("--session-id", default=None, help="Session ID sub-heading; auto-detected if absent.")
    w.add_argument("--trajectory", default=None, help="Session trajectory markdown body.")
    w.add_argument("--deferred", default=None, help="Deferred-with-reasons markdown body (or use --deferred-json).")
    w.add_argument("--deferred-json", default=None, help="JSON list of {title, uid, reason} dicts.")
    w.add_argument("--open-questions", default=None, help="Open questions markdown body.")
    w.add_argument("--context", default=None, help="Context-for-tomorrow markdown body.")
    w.add_argument("--date", default=None, help="ISO date (YYYY-MM-DD). Default: today in profile timezone.")
    w.set_defaults(func=_cmd_write)

    r = sub.add_parser("read", help="Read a handoff file and print JSON.")
    r.add_argument("--date", default=None, help="ISO date (default: today).")
    r.set_defaults(func=_cmd_read)

    p = sub.add_parser("path", help="Print the path of the handoff file.")
    p.add_argument("--date", default=None, help="ISO date (default: today).")
    p.set_defaults(func=_cmd_path)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
