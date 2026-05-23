#!/usr/bin/env python3
"""CodeIndexWatch tool — start/stop/status the background file watcher."""

from __future__ import annotations

import json
import os
import subprocess
import sys

sys.path.insert(0, str(__file__).rsplit("/", 2)[0])

from lib.watcher import is_watcher_running, start_watcher, stop_watcher


def main() -> None:
    params = json.load(sys.stdin)
    action = params.get("action", "status")

    if action == "start":
        if is_watcher_running():
            print(json.dumps({"status": "already_running"}))
            return
        # Fork to background
        script = os.path.join(os.path.dirname(__file__), "watch_daemon.py")
        proc = subprocess.Popen(
            [sys.executable, script],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        print(json.dumps({"status": "started", "pid": proc.pid}))
    elif action == "stop":
        stopped = stop_watcher()
        print(json.dumps({"status": "stopped" if stopped else "not_running"}))
    else:  # status
        running = is_watcher_running()
        print(json.dumps({"status": "running" if running else "stopped"}))


if __name__ == "__main__":
    main()
