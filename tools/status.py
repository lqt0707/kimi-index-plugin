#!/usr/bin/env python3
"""CodeIndexStatus tool — show index status."""

from __future__ import annotations

import json
import sys

sys.path.insert(0, str(__file__).rsplit("/", 2)[0])

from lib.indexer import Indexer
from lib.watcher import is_watcher_running


def _format_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes} B"
    if size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    return f"{size_bytes / (1024 * 1024):.1f} MB"


def main() -> None:
    indexer = Indexer()
    try:
        stats = indexer.get_stats()
        watcher_running = is_watcher_running()

        if stats["file_count"] == 0:
            print("No index found. Run CodeIndexBuild to create an index.")
            return

        lines = [
            "# Code Index Status",
            "",
            f"**File-level documents:** {stats['file_count']}",
            f"**Symbol-level documents:** {stats['symbol_count']}",
        ]
        if stats["last_updated"]:
            lines.append(f"**Last updated:** {stats['last_updated']}")
        if stats["embedding_model"]:
            lines.append(f"**Embedding model:** {stats['embedding_model']}")
        lines.append(f"**Database size:** {_format_size(stats['db_size_bytes'])}")
        lines.append(f"**Vector matrix size:** {_format_size(stats['vector_size_bytes'])}")
        lines.append(f"**Background watcher:** {'running ✅' if watcher_running else 'stopped ❌'}")
        if not watcher_running:
            lines.append("💡 Tip: Run CodeIndexWatch('start') to enable automatic incremental updates on file changes.")
        print("\n".join(lines))
    finally:
        indexer.close()


if __name__ == "__main__":
    main()
