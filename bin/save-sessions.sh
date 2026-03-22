#!/bin/bash
# Save Claude Code session IDs from all tmux panes.
# Run this before exiting/rebooting. It captures the resume ID and
# working directory from each pane running claude.
#
# Strategy: match claude process start time to jsonl file birth time.
# Claude writes conversations to ~/.claude/projects/<encoded-cwd>/<session-id>.jsonl
# The file's creation time correlates within seconds of the process start.
#
# Saves to: ~/.workplanner/sessions.json

set -euo pipefail

STATE_DIR="$HOME/.workplanner"
OUT="$STATE_DIR/sessions.json"
mkdir -p "$STATE_DIR"

# Encode a directory path the way Claude does: /a/b/c → -a-b-c
encode_path() {
    echo "$1" | sed 's|/|-|g'
}

# Find session ID by matching process start time to jsonl birth time
find_session_id() {
    local claude_pid="$1"
    local cwd="$2"

    # Method 1: --resume flag in process args (already resumed session)
    local proc_args
    proc_args=$(ps -p "$claude_pid" -o args= 2>/dev/null) || true
    if echo "$proc_args" | grep -q '\-\-resume'; then
        echo "$proc_args" | grep -oE '\-\-resume [^ ]+' | awk '{print $2}'
        return
    fi

    # Method 2: correlate process start time with jsonl file birth time
    local encoded
    encoded=$(encode_path "$cwd")
    local project_dir="$HOME/.claude/projects/$encoded"

    if [ ! -d "$project_dir" ]; then
        return
    fi

    # Get process start time as epoch seconds
    local proc_start
    # ps lstart format: "Thu Mar  5 09:25:20 2026" (space-padded day)
    local raw_lstart
    raw_lstart=$(ps -p "$claude_pid" -o lstart= 2>/dev/null) || true
    proc_start=$(date -j -f '%a %b %e %H:%M:%S %Y' "$raw_lstart" '+%s' 2>/dev/null) || true
    if [ -z "$proc_start" ]; then
        return
    fi

    # Find the jsonl file whose birth time is closest to process start
    local best_file=""
    local best_diff=999999

    for f in "$project_dir"/*.jsonl; do
        [ -f "$f" ] || continue
        local birth
        birth=$(stat -f '%B' "$f" 2>/dev/null) || continue
        local diff=$(( birth - proc_start ))
        # Absolute value
        [ $diff -lt 0 ] && diff=$(( -diff ))
        # Must be within 30 seconds
        if [ $diff -lt $best_diff ] && [ $diff -lt 30 ]; then
            best_diff=$diff
            best_file="$f"
        fi
    done

    if [ -n "$best_file" ]; then
        basename "$best_file" .jsonl
    fi
}

sessions="[]"
while IFS=$'\t' read -r pane_id pane_title pane_pid pane_cwd; do
    # Check if this pane is running claude (child of pane's shell)
    # Note: pgrep -P is unreliable on macOS; use ps + awk instead
    claude_pid=$(ps -o pid=,ppid=,comm= 2>/dev/null | awk -v ppid="$pane_pid" '$2 == ppid && $3 == "claude" {print $1; exit}') || true
    if [ -z "$claude_pid" ]; then
        continue
    fi

    resume_id=$(find_session_id "$claude_pid" "$pane_cwd")

    if [ -z "$resume_id" ]; then
        echo "Warning: Pane '$pane_title' has claude (PID $claude_pid) but couldn't find session ID — skipping"
        continue
    fi

    # Capture launch flags (e.g. --dangerously-skip-permissions) to replay on restore
    claude_args=$(ps -p "$claude_pid" -o args= 2>/dev/null | sed 's|^claude ||; s| *$||') || true
    # Remove --resume and its value if present (we add our own)
    claude_args=$(echo "$claude_args" | sed 's/--resume [^ ]*//' | sed 's/  */ /g; s/^ *//; s/ *$//')
    # Remove any trailing prompt argument (quoted string at end) — we only want flags
    claude_flags=$(echo "$claude_args" | grep -oE '(--[a-z][a-z-]+ ?[^ -][^ ]*|--[a-z][a-z-]+)' | grep -v '^--resume' | tr '\n' ' ' | sed 's/ *$//')

    sessions=$(echo "$sessions" | python3 -c "
import json, sys
data = json.load(sys.stdin)
data.append({
    'pane_title': '$pane_title',
    'resume_id': '$resume_id',
    'cwd': '$pane_cwd',
    'pane_id': '$pane_id',
    'flags': '$claude_flags'
})
json.dump(data, sys.stdout)
")
done < <(tmux list-panes -a -F $'#{pane_id}\t#{pane_title}\t#{pane_pid}\t#{pane_current_path}')

count=$(echo "$sessions" | python3 -c "import json,sys; print(len(json.load(sys.stdin)))")

echo "$sessions" | python3 -m json.tool > "$OUT"
echo "Saved $count session(s) to $OUT"
cat "$OUT"
