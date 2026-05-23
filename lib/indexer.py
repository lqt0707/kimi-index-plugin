"""Index storage: SQLite for metadata + NumPy for vectors."""

from __future__ import annotations

import json
import os
import sqlite3
import struct
import time
from pathlib import Path
from typing import Any

import numpy as np

INDEX_DIR_NAME = ".kimi-index"
DB_FILE = "index.db"
FILE_VECTORS = "file_vectors.npy"
SYMBOL_VECTORS = "symbol_vectors.npy"
META_FILE = "meta.json"


def _get_index_dir(cwd: str | None = None) -> Path:
    base = Path(cwd) if cwd else Path.cwd()
    return base / INDEX_DIR_NAME


def _cosine_similarity(query: np.ndarray, vectors: np.ndarray) -> np.ndarray:
    """Compute cosine similarity between query and all vectors."""
    if vectors.size == 0:
        return np.array([])
    # Normalize
    q_norm = query / (np.linalg.norm(query) + 1e-10)
    v_norm = vectors / (np.linalg.norm(vectors, axis=1, keepdims=True) + 1e-10)
    return np.dot(v_norm, q_norm)


class Indexer:
    """Manages file-level and symbol-level indexes."""

    def __init__(self, cwd: str | None = None):
        self.index_dir = _get_index_dir(cwd)
        self.index_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.index_dir / DB_FILE
        self.file_vec_path = self.index_dir / FILE_VECTORS
        self.symbol_vec_path = self.index_dir / SYMBOL_VECTORS
        self.meta_path = self.index_dir / META_FILE
        self._conn: sqlite3.Connection | None = None
        self._init_db()

    def _conn_db(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def _init_db(self) -> None:
        conn = self._conn_db()
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS file_docs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file TEXT NOT NULL UNIQUE,
                language TEXT,
                line_count INTEGER,
                exports TEXT,
                text_preview TEXT,
                mtime REAL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS symbol_docs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file TEXT NOT NULL,
                symbol TEXT,
                symbol_type TEXT,
                line_start INTEGER,
                line_end INTEGER,
                text TEXT,
                parent_file_doc_id INTEGER,
                FOREIGN KEY (parent_file_doc_id) REFERENCES file_docs(id)
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_symbol_file ON symbol_docs(file)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_symbol_name ON symbol_docs(symbol)"
        )
        conn.commit()

    def save_meta(self, meta: dict[str, Any]) -> None:
        with open(self.meta_path, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)

    def load_meta(self) -> dict[str, Any]:
        if self.meta_path.exists():
            try:
                with open(self.meta_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    # ------------------------------------------------------------------
    # File-level index operations
    # ------------------------------------------------------------------

    def update_file_docs(self, docs: list[dict[str, Any]]) -> list[int]:
        """Insert or update file docs. Returns list of doc ids."""
        conn = self._conn_db()
        ids: list[int] = []
        for doc in docs:
            file_path = doc["file"]
            mtime = doc.get("mtime", 0.0)
            # Check existing
            row = conn.execute(
                "SELECT id FROM file_docs WHERE file = ?", (file_path,)
            ).fetchone()
            if row:
                doc_id = row["id"]
                conn.execute(
                    """
                    UPDATE file_docs
                    SET language = ?, line_count = ?, exports = ?, text_preview = ?, mtime = ?
                    WHERE id = ?
                    """,
                    (
                        doc.get("language", ""),
                        doc.get("line_count", 0),
                        json.dumps(doc.get("exports", []), ensure_ascii=False),
                        doc.get("text_preview", ""),
                        mtime,
                        doc_id,
                    ),
                )
            else:
                cur = conn.execute(
                    """
                    INSERT INTO file_docs (file, language, line_count, exports, text_preview, mtime)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        file_path,
                        doc.get("language", ""),
                        doc.get("line_count", 0),
                        json.dumps(doc.get("exports", []), ensure_ascii=False),
                        doc.get("text_preview", ""),
                        mtime,
                    ),
                )
                doc_id = cur.lastrowid
            ids.append(doc_id)
        conn.commit()
        return ids

    def delete_files_not_in(self, keep_files: set[str]) -> None:
        """Remove file docs and their symbols for files not in keep_files."""
        conn = self._conn_db()
        # Get ids to delete
        all_files = conn.execute("SELECT id, file FROM file_docs").fetchall()
        to_delete = [row["id"] for row in all_files if row["file"] not in keep_files]
        if not to_delete:
            return
        placeholders = ",".join("?" * len(to_delete))
        conn.execute(f"DELETE FROM symbol_docs WHERE parent_file_doc_id IN ({placeholders})", to_delete)
        conn.execute(f"DELETE FROM file_docs WHERE id IN ({placeholders})", to_delete)
        conn.commit()

    def get_all_file_doc_ids(self) -> dict[str, int]:
        """Return mapping of file path -> doc id."""
        conn = self._conn_db()
        rows = conn.execute("SELECT id, file FROM file_docs").fetchall()
        return {row["file"]: row["id"] for row in rows}

    def get_file_doc(self, doc_id: int) -> dict[str, Any] | None:
        conn = self._conn_db()
        row = conn.execute("SELECT * FROM file_docs WHERE id = ?", (doc_id,)).fetchone()
        if not row:
            return None
        return dict(row)

    # ------------------------------------------------------------------
    # Symbol-level index operations
    # ------------------------------------------------------------------

    def update_symbol_docs(self, symbols: list[dict[str, Any]]) -> list[int]:
        """Insert symbol docs. Existing symbols for same file should be cleared first."""
        conn = self._conn_db()
        ids: list[int] = []
        for sym in symbols:
            cur = conn.execute(
                """
                INSERT INTO symbol_docs (file, symbol, symbol_type, line_start, line_end, text, parent_file_doc_id)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    sym["file"],
                    sym["symbol"],
                    sym["symbol_type"],
                    sym["line_start"],
                    sym["line_end"],
                    sym["text"],
                    sym.get("parent_file_doc_id"),
                ),
            )
            ids.append(cur.lastrowid)
        conn.commit()
        return ids

    def delete_symbols_for_files(self, files: set[str]) -> None:
        if not files:
            return
        conn = self._conn_db()
        placeholders = ",".join("?" * len(files))
        conn.execute(f"DELETE FROM symbol_docs WHERE file IN ({placeholders})", list(files))
        conn.commit()

    def get_symbol_doc(self, doc_id: int) -> dict[str, Any] | None:
        conn = self._conn_db()
        row = conn.execute("SELECT * FROM symbol_docs WHERE id = ?", (doc_id,)).fetchone()
        if not row:
            return None
        return dict(row)

    # ------------------------------------------------------------------
    # Vector operations
    # ------------------------------------------------------------------

    def save_file_vectors(self, vectors: np.ndarray) -> None:
        np.save(self.file_vec_path, vectors)

    def load_file_vectors(self) -> np.ndarray:
        if not self.file_vec_path.exists():
            return np.array([]).reshape(0, 0)
        return np.load(self.file_vec_path)

    def save_symbol_vectors(self, vectors: np.ndarray) -> None:
        np.save(self.symbol_vec_path, vectors)

    def load_symbol_vectors(self) -> np.ndarray:
        if not self.symbol_vec_path.exists():
            return np.array([]).reshape(0, 0)
        return np.load(self.symbol_vec_path)

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def search_files(
        self,
        query_vector: list[float],
        limit: int = 10,
        file_pattern: str | None = None,
    ) -> list[dict[str, Any]]:
        """Search file-level index."""
        vectors = self.load_file_vectors()
        if vectors.size == 0:
            return []

        q = np.array(query_vector, dtype=np.float32)
        scores = _cosine_similarity(q, vectors)

        conn = self._conn_db()
        rows = conn.execute("SELECT id, file, language, line_count, exports, text_preview FROM file_docs").fetchall()
        file_map = {row["id"]: dict(row) for row in rows}

        results = []
        for idx in range(len(vectors)):
            doc_id = idx + 1  # SQLite auto-increment starts at 1, and we maintain order
            if doc_id not in file_map:
                continue
            info = file_map[doc_id]
            if file_pattern and not self._match_pattern(info["file"], file_pattern):
                continue
            results.append({
                "id": doc_id,
                "score": float(scores[idx]),
                **info,
            })

        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:limit]

    def search_symbols(
        self,
        query_vector: list[float],
        limit: int = 10,
        file_pattern: str | None = None,
    ) -> list[dict[str, Any]]:
        """Search symbol-level index."""
        vectors = self.load_symbol_vectors()
        if vectors.size == 0:
            return []

        q = np.array(query_vector, dtype=np.float32)
        scores = _cosine_similarity(q, vectors)

        conn = self._conn_db()
        rows = conn.execute(
            "SELECT id, file, symbol, symbol_type, line_start, line_end, text FROM symbol_docs"
        ).fetchall()
        sym_map = {row["id"]: dict(row) for row in rows}

        results = []
        for idx in range(len(vectors)):
            doc_id = idx + 1
            if doc_id not in sym_map:
                continue
            info = sym_map[doc_id]
            if file_pattern and not self._match_pattern(info["file"], file_pattern):
                continue
            results.append({
                "id": doc_id,
                "score": float(scores[idx]),
                **info,
            })

        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:limit]

    def search_combined(
        self,
        query_vector: list[float],
        limit: int = 10,
        file_pattern: str | None = None,
    ) -> list[dict[str, Any]]:
        """Auto granularity: file-level first, then symbol-level within top files."""
        file_results = self.search_files(query_vector, limit=limit * 3, file_pattern=file_pattern)
        if not file_results:
            return []

        top_files = {r["file"] for r in file_results[:limit]}

        # Try symbol search but only for symbols in top files
        sym_results = self.search_symbols(query_vector, limit=limit * 2, file_pattern=file_pattern)
        filtered_syms = [r for r in sym_results if r["file"] in top_files]

        # Merge: symbols first (more precise), then files
        seen_files = set()
        merged: list[dict[str, Any]] = []
        for r in filtered_syms[:limit]:
            merged.append({
                "file": r["file"],
                "line": r["line_start"],
                "symbol": r["symbol"],
                "score": r["score"],
                "snippet": r["text"][:500] if r["text"] else "",
            })
            seen_files.add(r["file"])

        # Fill remaining slots with file-level results
        for r in file_results:
            if len(merged) >= limit:
                break
            if r["file"] in seen_files:
                continue
            merged.append({
                "file": r["file"],
                "line": 1,
                "symbol": None,
                "score": r["score"],
                "snippet": r["text_preview"][:500] if r["text_preview"] else "",
            })
            seen_files.add(r["file"])

        return merged

    @staticmethod
    def _match_pattern(file_path: str, pattern: str) -> bool:
        """Simple glob-like matching."""
        import fnmatch
        return fnmatch.fnmatch(file_path, pattern) or fnmatch.fnmatch(file_path, "*/" + pattern)

    def get_stats(self) -> dict[str, Any]:
        conn = self._conn_db()
        file_count = conn.execute("SELECT COUNT(*) FROM file_docs").fetchone()[0]
        symbol_count = conn.execute("SELECT COUNT(*) FROM symbol_docs").fetchone()[0]
        meta = self.load_meta()
        db_size = self.db_path.stat().st_size if self.db_path.exists() else 0
        vec_size = (
            (self.file_vec_path.stat().st_size if self.file_vec_path.exists() else 0)
            + (self.symbol_vec_path.stat().st_size if self.symbol_vec_path.exists() else 0)
        )
        return {
            "file_count": file_count,
            "symbol_count": symbol_count,
            "last_updated": meta.get("last_updated"),
            "embedding_model": meta.get("embedding_model"),
            "db_size_bytes": db_size,
            "vector_size_bytes": vec_size,
        }

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None
