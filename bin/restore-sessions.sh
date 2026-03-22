#!/bin/bash
# Restore Claude Code sessions from saved state.
# Run this after reboot inside a tmux session.
# Opens each saved session in its own tmux pane.
#
# Reads from: ~/.workplanner/sessions.json

set -euo pipefail

STATE_FILE="$HOME/.workplanner/sessions.json"

if [ ! -f "$STATE_FILE" ]; then
    echo "No saved sessions found at $STATE_FILE"
    echo "Run save-sessions.sh before rebooting."
    exit 1
fi

if [ -z "${TMUX:-}" ]; then
    echo "Not in tmux — launching new tmux session with saved panes..."
    # Start tmux with a shell, then run this script inside it.
    # Can't exec directly because the first pane needs a live shell
    # for send-keys to work.
    exec tmux new-session -s claude "bash -l -c '$0; exec bash -l'"
fi

count=$(python3 -c "import json; print(len(json.load(open('$STATE_FILE'))))")
if [ "$count" -eq 0 ]; then
    echo "No sessions to restore."
    exit 0
fi

echo "Restoring $count session(s)..."

# All sessions get their own pane via split-window.
# The current pane stays as-is (shows restore output, then a shell).
python3 -c "
import json
for s in json.load(open('$STATE_FILE')):
    flags = s.get('flags', '')
    print(f\"{s['pane_title']}\t{s['resume_id']}\t{s['cwd']}\t{flags}\")
" | while IFS=$'\t' read -r title resume_id cwd flags; do
    echo "Opening pane '$title' (cd $cwd) [${flags:-no flags}]"
    tmux split-window -h -c "$cwd" "claude $flags --resume '$resume_id'; exec zsh -l"
    tmux select-pane -T "$title"
done

echo ""
echo "Done. $count session(s) restored. Use Ctrl-b + arrow keys to navigate."
