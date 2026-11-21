#!/usr/bin/env python3
"""Decide whether a Bash command calls wpl (issue #65). Prints 1 or 0.

Read by bin/post-tool-use-hook.sh. A command counts only when one of its
simple commands starts with `wpl`, `*/wpl` or `python3 .../transition.py`.
Quoted strings and heredoc bodies are data: a commit message or issue body
that mentions wpl, or `sed` over transition.py, does not count.
"""
import re, shlex, sys
cmd = sys.stdin.read()
# Heredoc bodies are data, not commands: drop the lines between a <<WORD
# opener and its WORD terminator.
lines, out, end = cmd.split("\n"), [], None
for line in lines:
    if end is not None:
        if line.strip() == end:
            end = None
        continue
    out.append(line)
    m = re.search(r"<<-?\s*['\"]?([A-Za-z_][A-Za-z0-9_]*)['\"]?", line)
    if m:
        end = m.group(1)
cmd = "\n".join(out)
lex = shlex.shlex(cmd, posix=True, punctuation_chars="();<>|&\n")
lex.whitespace = " \t\r"
lex.whitespace_split = True
try:
    tokens = list(lex)
except ValueError:
    tokens = cmd.split()
ops = {"&&", "||", ";", "|", "&", "\n", "(", ")", ";;"}
start, found = True, False
words = []
def check(ws):
    while ws and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", ws[0]):
        ws = ws[1:]
    if not ws:
        return False
    w0 = ws[0]
    if w0 == "wpl" or w0.endswith("/wpl"):
        return True
    return bool(re.match(r"^(.*/)?python3?$", w0) and len(ws) > 1 and ws[1].endswith("/transition.py"))
for tok in tokens + ["\n"]:
    if tok in ops or set(tok) <= set("();<>|&\n"):
        if check(words):
            found = True
            break
        words = []
    else:
        words.append(tok)
print(1 if found else 0)
