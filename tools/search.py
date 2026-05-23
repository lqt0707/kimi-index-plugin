#!/usr/bin/env python3
"""CodeIndexSearch tool — semantic search via stdin JSON params."""

from __future__ import annotations

import json
import sys

sys.path.insert(0, str(__file__).rsplit("/", 2)[0])

from lib.embedder import Embedder
from lib.indexer import Indexer


def main() -> None:
    params = json.load(sys.stdin)
    query = params.get("query", "").strip()
    limit = params.get("limit", 10)
    granularity = params.get("granularity", "auto")
    file_pattern = params.get("file_pattern")

    if not query:
        print(json.dumps({"error": "Missing required parameter: query"}))
        sys.exit(1)

    indexer = Indexer()
    embedder = Embedder()
    try:
        qvec = embedder.embed_single(query)

        if granularity == "file":
            results = indexer.search_files(qvec, limit=limit, file_pattern=file_pattern)
            output = [
                {
                    "file": r["file"],
                    "line": 1,
                    "symbol": None,
                    "score": round(r["score"], 4),
                    "snippet": r.get("text_preview", "")[:500],
                }
                for r in results
            ]
        elif granularity == "symbol":
            results = indexer.search_symbols(qvec, limit=limit, file_pattern=file_pattern)
            output = [
                {
                    "file": r["file"],
                    "line": r["line_start"],
                    "symbol": r["symbol"],
                    "score": round(r["score"], 4),
                    "snippet": r.get("text", "")[:500],
                }
                for r in results
            ]
        else:  # auto
            output = indexer.search_combined(qvec, limit=limit, file_pattern=file_pattern)
            for r in output:
                r["score"] = round(r["score"], 4)

        print(json.dumps(output, ensure_ascii=False))
    finally:
        embedder.close()
        indexer.close()


if __name__ == "__main__":
    main()
