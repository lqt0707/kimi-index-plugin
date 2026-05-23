#!/usr/bin/env python3
"""Background daemon for file watching. Started by watch.py."""

from __future__ import annotations

import sys
import os

# Redirect stderr to log file for debugging
log_path = os.path.join(os.getcwd(), ".kimi-index", "watch.log")
os.makedirs(os.path.dirname(log_path), exist_ok=True)
# Note: we can't easily redirect stderr after imports, but we can log to file

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib.watcher import start_watcher

if __name__ == "__main__":
    start_watcher()
