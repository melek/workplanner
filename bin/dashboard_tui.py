#!/usr/bin/env python3
"""Workplanner dashboard TUI — container process for the dashboard pane."""

import os
import select
import signal
import sys
import termios
import time
import tty
from pathlib import Path

# Import renderer (same directory)
sys.path.insert(0, str(Path(__file__).parent))
import render_dashboard as rd

CLEAR = "\033[3J\033[H\033[2J"


def _watch_files():
    """Return current list of files to watch, resolved from active profile."""
    paths = rd.get_paths()
    if paths is None:
        return []
    return [paths["session"], paths["events"], paths["dashboard"]]


class DashboardTUI:
    def __init__(self):
        self.view = "dashboard"  # "dashboard" | "alerts"
        self.running = True
        self.mtimes = {}
        self.last_minute = ""
        self.old_termios = None
        self.idle_periods = []
        self.last_idle_fetch = 0
        self.session_cache = None
        self.session_mtime = 0

    def run(self):
        self._setup_terminal()
        self._setup_signals()
        try:
            self._rerender()
            self._display()
            while self.running:
                # Block up to 0.5s waiting for keypress — zero CPU when idle
                ready, _, _ = select.select([sys.stdin], [], [], 0.05)
                if ready:
                    self._handle_input()
                if self._check_changes():
                    self._display()
        finally:
            self._restore_terminal()

    # -- Terminal management --

    def _setup_terminal(self):
        if sys.stdin.isatty():
            self.old_termios = termios.tcgetattr(sys.stdin)
            tty.setraw(sys.stdin, when=termios.TCSANOW)
            # Re-enable output processing (raw disables it, breaking \n)
            attrs = termios.tcgetattr(sys.stdin)
            attrs[1] |= termios.OPOST
            termios.tcsetattr(sys.stdin, termios.TCSANOW, attrs)

    def _restore_terminal(self):
        if self.old_termios:
            termios.tcsetattr(sys.stdin, termios.TCSADRAIN, self.old_termios)

    def _setup_signals(self):
        signal.signal(signal.SIGINT, lambda *_: self._quit())
        signal.signal(signal.SIGTERM, lambda *_: self._quit())
        signal.signal(signal.SIGWINCH, lambda *_: self._display())

    def _quit(self):
        self.running = False

    # -- Input --

    def _handle_input(self):
        if not sys.stdin.isatty():
            return
        key = sys.stdin.read(1)
        if key == "\x03" or key == "\x1b":  # Ctrl-C or Esc
            self._quit()
            return
        if self.view == "alerts":
            self.view = "dashboard"
            self._display()
        elif key in ("a", "A"):
            self.view = "alerts"
            self._display()

    # -- File watching --

    def _get_mtime(self, path):
        try:
            return os.stat(path).st_mtime
        except OSError:
            return 0

    def _check_changes(self):
        changed = False
        current_minute = time.strftime("%H:%M")

        for path in _watch_files():
            mt = self._get_mtime(path)
            if mt != self.mtimes.get(str(path)):
                self.mtimes[str(path)] = mt
                changed = True

        if current_minute != self.last_minute:
            if self.last_minute:  # skip first tick
                self._rerender()
                changed = True
            self.last_minute = current_minute

        return changed

    # -- Rendering --

    def _fetch_idle_periods(self, date_str):
        """Fetch idle periods, cached for 60s."""
        now = time.time()
        if now - self.last_idle_fetch > 60:
            self.idle_periods = rd._get_idle_periods(date_str)
            self.last_idle_fetch = now
        return self.idle_periods

    def _rerender(self):
        """Re-run the renderer to update dashboard-view.txt and events."""
        try:
            session = self._load_session()
            if not session:
                return
            paths = rd.get_paths()
            if paths is None:
                return
            idle_periods = self._fetch_idle_periods(session.get("date", ""))
            events = rd._load_events()
            new_events = rd._detect_alerts(session, events, idle_periods)
            if new_events:
                events.extend(new_events)
            events = rd._prune_expired(events)
            rd._save_events(events)
            rendered = rd.render(session, idle_periods)
            dashboard_path = paths["dashboard"]
            dashboard_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = str(dashboard_path) + ".tmp"
            with open(tmp, "w") as f:
                f.write(rendered)
            os.rename(tmp, str(dashboard_path))
        except Exception:
            pass

    def _load_session(self):
        try:
            paths = rd.get_paths()
            if paths is None:
                return None
            session_path = paths["session"]
            mt = os.stat(session_path).st_mtime
            if mt != self.session_mtime or self.session_cache is None:
                import json
                with open(session_path) as f:
                    self.session_cache = json.load(f)
                self.session_mtime = mt
            return self.session_cache
        except (OSError, Exception):
            return None

    def _display(self):
        cols = self._terminal_width()
        os.environ["COLUMNS"] = str(cols)

        sys.stdout.write(CLEAR)

        if self.view == "alerts":
            session = self._load_session()
            if session:
                sys.stdout.write(rd.render_alert_log(session, self.idle_periods))
            else:
                sys.stdout.write("No session\n")
        else:
            paths = rd.get_paths()
            dashboard_path = paths["dashboard"] if paths else None
            if dashboard_path and os.path.isfile(dashboard_path):
                with open(dashboard_path) as f:
                    sys.stdout.write(f.read())
            else:
                sys.stdout.write("Waiting for session...\n")

        # Status bar
        sys.stdout.write("\n")
        if self.view == "dashboard":
            sys.stdout.write(f" {rd.DIM}a: alert log{rd.RESET}")
        else:
            sys.stdout.write(f" {rd.DIM}any key: back{rd.RESET}")

        sys.stdout.flush()

    def _terminal_width(self):
        try:
            return os.get_terminal_size().columns
        except OSError:
            return 50


if __name__ == "__main__":
    DashboardTUI().run()
