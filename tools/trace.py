#!/usr/bin/env python3
"""CodeIndexTrace tool — trace symbol callers/callees using caller_index."""

from __future__ import annotations

import json
import re
import sys

sys.path.insert(0, str(__file__).rsplit("/", 2)[0])

from lib.indexer import Indexer


def _find_callers_fallback(symbol: str, indexer: Indexer) -> list[dict]:
    """Fallback: SQLite LIKE pre-filter + regex scan."""
    conn = indexer._conn_db()
    sym_name = symbol.split(".")[-1]
    like_pattern = f"%{sym_name}%"
    rows = conn.execute(
        "SELECT file, text_preview FROM file_docs WHERE text_preview LIKE ?",
        (like_pattern,),
    ).fetchall()
    callers = []
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


def _find_callees_fallback(symbol: str, indexer: Indexer) -> list[dict]:
    """Fallback: scan symbol text for function calls."""
    conn = indexer._conn_db()
    sym_rows = conn.execute(
        "SELECT file, text FROM symbol_docs WHERE symbol = ?",
        (symbol,),
    ).fetchall()
    if not sym_rows:
        return []

    callees = []
    seen = set()
    call_pattern = re.compile(r"\b(\w+)\s*\(")
    for row in sym_rows:
        text = row["text"] or ""
        for match in call_pattern.finditer(text):
            called = match.group(1)
            if called not in ("if", "for", "while", "switch", "return", "await", "yield", "new"):
                key = (row["file"], called)
                if key not in seen:
                    seen.add(key)
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
        sym_short = symbol.split(".")[-1]

        if direction == "callers":
            # Fast path: use caller_index if available
            results = indexer.search_callers(sym_short, limit=50)
            if not results:
                # Fallback to LIKE + regex scan
                results = _find_callers_fallback(symbol, indexer)
        else:
            # Fast path: use caller_index reverse lookup
            results = indexer.search_callees(sym_short, limit=50)
            if not results:
                results = _find_callees_fallback(symbol, indexer)

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
