#!/usr/bin/env python3
"""CodeIndexTrace tool — trace symbol callers/callees."""

from __future__ import annotations

import json
import re
import sys

sys.path.insert(0, str(__file__).rsplit("/", 2)[0])

from lib.indexer import Indexer


def _find_callers(symbol: str, indexer: Indexer) -> list[dict]:
    """Find files that reference the given symbol."""
    conn = indexer._conn_db()
    # Get all file text previews
    rows = conn.execute("SELECT file, text_preview FROM file_docs").fetchall()
    callers = []
    sym_name = symbol.split(".")[-1]  # Use short name for matching
    pattern = re.compile(rf"\b{re.escape(sym_name)}\b")
    for row in rows:
        text = row["text_preview"] or ""
        if pattern.search(text):
            callers.append({
                "file": row["file"],
                "symbol": sym_name,
                "relationship": "caller",
            })
    return callers


def _find_callees(symbol: str, indexer: Indexer) -> list[dict]:
    """Find symbols called by the given symbol (approximate via file content)."""
    conn = indexer._conn_db()
    # Find the file containing the symbol
    sym_rows = conn.execute(
        "SELECT file, text FROM symbol_docs WHERE symbol = ?",
        (symbol,),
    ).fetchall()
    if not sym_rows:
        return []

    callees = []
    for row in sym_rows:
        text = row["text"] or ""
        # Find function calls in the text
        call_pattern = re.compile(r"\b(\w+)\s*\(")
        for match in call_pattern.finditer(text):
            called = match.group(1)
            if called not in ("if", "for", "while", "switch", "return", "await", "yield", "new"):
                callees.append({
                    "file": row["file"],
                    "symbol": called,
                    "relationship": "callee",
                })
    return callees


def main() -> None:
    params = json.load(sys.stdin)
    symbol = params.get("symbol", "").strip()
    direction = params.get("direction", "callers")

    if not symbol:
        print(json.dumps({"error": "Missing required parameter: symbol"}))
        sys.exit(1)

    indexer = Indexer()
    try:
        if direction == "callers":
            results = _find_callers(symbol, indexer)
        else:
            results = _find_callees(symbol, indexer)

        # Deduplicate
        seen = set()
        unique = []
        for r in results:
            key = (r["file"], r["symbol"])
            if key not in seen:
                seen.add(key)
                unique.append(r)

        print(json.dumps(unique[:50], ensure_ascii=False))
    finally:
        indexer.close()


if __name__ == "__main__":
    main()
