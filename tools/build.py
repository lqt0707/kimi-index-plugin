#!/usr/bin/env python3
"""CodeIndexBuild tool — build or update the code index."""

from __future__ import annotations

import fnmatch
import json
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np

sys.path.insert(0, str(__file__).rsplit("/", 2)[0])

from lib.config import PluginConfig
from lib.embedder import Embedder
from lib.indexer import Indexer
from lib.parser import FileDoc, format_for_embedding, format_symbol_for_embedding, parse_file


def _should_include(path: str, config: PluginConfig) -> bool:
    """Check if a file should be indexed."""
    # Check extension
    ext = Path(path).suffix.lower()
    if ext not in config.include_extensions:
        return False

    # Check exclude patterns
    for pattern in config.exclude_patterns:
        if fnmatch.fnmatch(path, pattern) or fnmatch.fnmatch(os.path.basename(path), pattern):
            return False
    return True


def _collect_files(paths: list[str] | None, config: PluginConfig) -> dict[str, float]:
    """Collect source files with their mtimes using os.scandir for speed."""
    files: dict[str, float] = {}
    search_paths = paths if paths else ["."]
    skip_dirs = {".git", ".kimi-index", "node_modules", "dist", ".venv", "__pycache__", ".tox", ".eggs"}

    def _scandir(dir_path: Path) -> None:
        try:
            with os.scandir(dir_path) as it:
                for entry in it:
                    if entry.name.startswith(".") and entry.name in skip_dirs:
                        continue
                    if entry.name in skip_dirs:
                        continue
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            _scandir(Path(entry.path))
                        elif entry.is_file(follow_symlinks=False):
                            fpath = entry.path
                            if _should_include(fpath, config):
                                files[fpath] = entry.stat().st_mtime
                    except OSError:
                        pass
        except OSError:
            pass

    for sp in search_paths:
        p = Path(sp)
        if p.is_file():
            if _should_include(str(p), config):
                files[str(p)] = p.stat().st_mtime
        elif p.is_dir():
            _scandir(p)
    return files


def _build_caller_index(indexer: Indexer, changed_files: list[str]) -> None:
    """Build reverse caller index by scanning file previews for symbol references.
    
    For incremental builds, only re-scans changed files and removes stale entries.
    """
    import re

    conn = indexer._conn_db()

    # For incremental: remove stale caller entries for changed files
    if changed_files:
        for fpath in changed_files:
            conn.execute("DELETE FROM caller_index WHERE caller_file = ?", (fpath,))
        conn.commit()

    # Get all known symbols (from file exports + symbol docs)
    rows = conn.execute("SELECT DISTINCT symbol FROM symbol_docs WHERE symbol IS NOT NULL").fetchall()
    known_symbols = {r["symbol"] for r in rows if r["symbol"]}

    if not known_symbols:
        return

    # Build a combined regex for fast multi-symbol matching
    # Sort by length descending to prefer longer matches
    sorted_syms = sorted(known_symbols, key=len, reverse=True)
    # Batch into regexes to avoid "too many groups" errors
    batch_size = 200
    symbol_batches = [sorted_syms[i:i + batch_size] for i in range(0, len(sorted_syms), batch_size)]
    patterns = []
    for batch in symbol_batches:
        escaped = [re.escape(s) for s in batch if s]
        if escaped:
            patterns.append(re.compile(r"\b(?:" + "|".join(escaped) + r")\b"))

    # Scan changed files
    file_rows = conn.execute(
        "SELECT file, text_preview FROM file_docs WHERE file IN ({})".format(
            ",".join("?" * len(changed_files))
        ),
        changed_files,
    ).fetchall() if changed_files else conn.execute("SELECT file, text_preview FROM file_docs").fetchall()

    entries = []
    for row in file_rows:
        fpath = row["file"]
        text = row["text_preview"] or ""
        found = set()
        for pat in patterns:
            for m in pat.finditer(text):
                sym = m.group(0)
                if sym in known_symbols and sym != fpath:
                    found.add(sym)
        for sym in found:
            entries.append({
                "target": sym,
                "caller_file": fpath,
                "caller_symbol": None,
                "line_number": 0,
            })

    if entries:
        indexer.add_caller_entries(entries)


def _batch_embed(texts: list[str], embedder: Embedder) -> list[list[float]]:
    """Embed texts using concurrent batch calls for maximum throughput."""
    if not texts:
        return []

    batch_size = embedder.batch_size
    total_batches = (len(texts) - 1) // batch_size + 1
    if total_batches <= 1:
        vectors = embedder.embed_batch(texts)
        print(f"  Embedded 1 batch ({len(texts)} items)", file=sys.stderr)
        return vectors

    # Use concurrent batch embedding
    print(f"  Embedding {len(texts)} items in {total_batches} concurrent batches...", file=sys.stderr)
    t0 = time.time()
    vectors = embedder.embed_batches(texts)
    elapsed = time.time() - t0
    print(f"  Embedded {total_batches} batches ({len(texts)} items) in {elapsed:.1f}s", file=sys.stderr)
    return vectors


_parse_cache: dict[str, tuple[dict, str, list]] = {}


def _parse_one(fpath: str, max_lines: int) -> tuple[str, dict, str, list]:
    """Parse a single file — designed for process pool pickling."""
    doc = parse_file(fpath, max_lines=max_lines)
    return (
        fpath,
        {
            "file": doc.file,
            "language": doc.language,
            "line_count": doc.line_count,
            "exports": doc.exports,
            "text_preview": doc.text_preview,
        },
        format_for_embedding(doc),
        doc.symbols,
    )


def _parse_files_parallel(file_paths: list[str], max_lines: int, max_workers: int = 8) -> list[tuple[str, dict, str, list]]:
    """Parse multiple files in parallel using process pool for CPU-bound work.
    
    Uses process pool to bypass Python GIL for regex-heavy symbol extraction.
    Falls back to sequential for small batches.
    """
    if len(file_paths) == 0:
        return []
    if len(file_paths) == 1:
        return [_parse_one(file_paths[0], max_lines)]

    # Check cache first
    results: list[Any] = [None] * len(file_paths)
    uncached: list[tuple[int, str]] = []
    for idx, fpath in enumerate(file_paths):
        cached = _parse_cache.get(fpath)
        if cached:
            doc_dict, embed_text, symbols = cached
            results[idx] = (fpath, doc_dict, embed_text, symbols)
        else:
            uncached.append((idx, fpath))

    if uncached:
        workers = min(max_workers, len(uncached))
        # Use thread pool for I/O-bound file reading + CPU-bound regex
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(_parse_one, fpath, max_lines): idx
                for idx, fpath in uncached
            }
            for future in as_completed(futures):
                idx = futures[future]
                try:
                    res = future.result()
                    results[idx] = res
                    # Cache for reuse (e.g. symbol-level indexing)
                    _parse_cache[res[0]] = (res[1], res[2], res[3])
                except Exception as e:
                    fpath = file_paths[idx]
                    print(f"  Failed to parse {fpath}: {e}", file=sys.stderr)
                    results[idx] = (fpath, {}, "", [])

    return [r for r in results if r is not None]


def main() -> None:
    params = json.load(sys.stdin)
    force_rebuild = params.get("force_rebuild", False)
    paths = params.get("paths")
    symbol_paths = params.get("symbol_paths")

    config = PluginConfig()
    indexer = Indexer()
    embedder = Embedder()

    try:
        start_time = time.time()

        # Collect files
        print("Scanning source files...", file=sys.stderr)
        all_files = _collect_files(paths, config)
        print(f"Found {len(all_files)} source files to index.", file=sys.stderr)

        if not all_files:
            print(json.dumps({"message": "No source files found."}))
            return

        # Determine which files need updating
        if not force_rebuild:
            existing = indexer.get_all_file_docs_with_mtime()
            files_to_remove = set(existing.keys()) - set(all_files.keys())
            files_to_add = {f: mtime for f, mtime in all_files.items() if f not in existing}
            files_to_update = {
                f: mtime for f, mtime in all_files.items()
                if f in existing and existing[f]["mtime"] < mtime
            }
            files_to_index = list(files_to_add.keys()) + list(files_to_update.keys())
            is_incremental = not files_to_remove and len(files_to_index) < len(all_files)
        else:
            files_to_remove = set()
            files_to_add = dict(all_files)
            files_to_update = {}
            files_to_index = list(all_files.keys())
            is_incremental = False

        # Clear old data if rebuilding
        if force_rebuild:
            indexer.delete_files_not_in(set())
            if indexer.file_vec_path.exists():
                indexer.file_vec_path.unlink()
            if indexer.symbol_vec_path.exists():
                indexer.symbol_vec_path.unlink()
        elif files_to_remove:
            indexer.delete_files_not_in(set(all_files.keys()))

        # Early exit if nothing changed
        if not force_rebuild and not files_to_index and not files_to_remove:
            print(json.dumps({
                "message": "Index is up to date.",
                "files_indexed": 0,
                "symbols_indexed": 0,
                "api_batches": 0,
                "elapsed_seconds": round(time.time() - start_time, 1),
            }, ensure_ascii=False))
            return

        # --- File-level indexing ---
        if is_incremental:
            print(f"Incremental update: {len(files_to_add)} new, {len(files_to_update)} changed files...", file=sys.stderr)
        else:
            print(f"Building file-level index for {len(files_to_index)} files...", file=sys.stderr)

        # Parse files in parallel
        t_parse = time.time()
        parsed_results = _parse_files_parallel(files_to_index, config.file_max_lines)
        print(f"  Parsed {len(parsed_results)} files in {time.time() - t_parse:.2f}s", file=sys.stderr)

        file_docs: list[dict] = []
        file_texts: list[str] = []
        file_symbols_map: dict[str, list] = {}

        for fpath, doc_dict, embed_text, symbols in parsed_results:
            doc_dict["mtime"] = all_files.get(fpath, 0.0)
            file_docs.append(doc_dict)
            file_texts.append(embed_text)
            file_symbols_map[fpath] = symbols

        # Embed file texts (only changed files) - concurrent batches
        file_vectors = _batch_embed(file_texts, embedder)

        # Update DB
        file_ids = indexer.update_file_docs(file_docs)

        # Incremental vector matrix rebuild
        if is_incremental and files_to_index:
            old_vectors = indexer.load_file_vectors()
            all_rows = indexer._conn_db().execute(
                "SELECT id, file FROM file_docs ORDER BY id"
            ).fetchall()
            if all_rows:
                max_id = all_rows[-1]["id"]
                dim = config.embedding_dimensions
                new_vec_list = [[0.0] * dim for _ in range(max_id)]

                # Map changed files to their new vectors
                changed_vec_map = {}
                for doc, vec in zip(file_docs, file_vectors):
                    changed_vec_map[doc["file"]] = vec

                for row in all_rows:
                    doc_id = row["id"]
                    fpath = row["file"]
                    idx = doc_id - 1
                    if fpath in changed_vec_map:
                        new_vec_list[idx] = changed_vec_map[fpath]
                    elif 0 <= idx < len(old_vectors) and len(old_vectors[idx]) == dim:
                        new_vec_list[idx] = old_vectors[idx].tolist()

                file_vec_array = np.array(new_vec_list, dtype=np.float32)
            else:
                file_vec_array = np.array([]).reshape(0, 0)
        else:
            file_vec_array = (
                np.array(file_vectors, dtype=np.float32) if file_vectors else np.array([]).reshape(0, 0)
            )

        indexer.save_file_vectors(file_vec_array)

        # --- Symbol-level indexing (optional) ---
        symbol_docs: list[dict] = []
        symbol_texts: list[str] = []
        symbol_count = 0

        if symbol_paths:
            # Determine which files are in symbol_paths AND have changed
            symbol_files = set()
            for sp in symbol_paths:
                for fpath in files_to_index:
                    if fnmatch.fnmatch(fpath, f"{sp}/**") or fpath.startswith(f"{sp}/"):
                        symbol_files.add(fpath)

            if symbol_files:
                # Clear old symbols for these files
                indexer.delete_symbols_for_files(symbol_files)

                # Build file_id map (need all files for parent_id lookup)
                all_doc_ids = indexer.get_all_file_doc_ids()

                # Reuse cached parse results or parse fresh
                print(f"Building symbol-level index for {len(symbol_files)} changed files...", file=sys.stderr)
                for fpath in symbol_files:
                    parent_id = all_doc_ids.get(fpath)
                    if parent_id is None:
                        continue
                    cached = _parse_cache.get(fpath)
                    if cached:
                        doc_dict, _, symbols = cached
                        doc = type('obj', (object,), {
                            'file': doc_dict['file'],
                            'symbols': symbols
                        })()
                    else:
                        doc = parse_file(fpath, max_lines=config.file_max_lines)
                    for sym in doc.symbols:
                        symbol_docs.append({
                            "file": fpath,
                            "symbol": sym.symbol,
                            "symbol_type": sym.symbol_type,
                            "line_start": sym.line_start,
                            "line_end": sym.line_end,
                            "text": sym.text,
                            "parent_file_doc_id": parent_id,
                        })
                        symbol_texts.append(format_symbol_for_embedding(doc, sym))
                        symbol_count += 1

                if symbol_texts:
                    symbol_vectors = _batch_embed(symbol_texts, embedder)
                    indexer.update_symbol_docs(symbol_docs)

                    # Incremental symbol vector rebuild
                    if is_incremental:
                        old_sym_vectors = indexer.load_symbol_vectors()
                        all_sym_rows = indexer._conn_db().execute(
                            "SELECT id, file, symbol, line_start, text FROM symbol_docs ORDER BY id"
                        ).fetchall()
                        if all_sym_rows:
                            max_sym_id = all_sym_rows[-1]["id"]
                            dim = config.embedding_dimensions
                            new_sym_vec_list = [[0.0] * dim for _ in range(max_sym_id)]

                            changed_sym_vec_map = {}
                            for sym_doc, vec in zip(symbol_docs, symbol_vectors):
                                key = (sym_doc["file"], sym_doc["symbol"], sym_doc["line_start"])
                                changed_sym_vec_map[key] = vec

                            for row in all_sym_rows:
                                sym_id = row["id"]
                                key = (row["file"], row["symbol"], row["line_start"])
                                idx = sym_id - 1
                                if key in changed_sym_vec_map:
                                    new_sym_vec_list[idx] = changed_sym_vec_map[key]
                                elif 0 <= idx < len(old_sym_vectors) and len(old_sym_vectors[idx]) == dim:
                                    new_sym_vec_list[idx] = old_sym_vectors[idx].tolist()

                            sym_vec_array = np.array(new_sym_vec_list, dtype=np.float32)
                        else:
                            sym_vec_array = np.array([]).reshape(0, 0)
                    else:
                        sym_vec_array = (
                            np.array(symbol_vectors, dtype=np.float32) if symbol_vectors else np.array([]).reshape(0, 0)
                        )
                    indexer.save_symbol_vectors(sym_vec_array)

        # Get total counts for metadata
        total_file_count = indexer._conn_db().execute("SELECT COUNT(*) FROM file_docs").fetchone()[0]
        total_symbol_count = indexer._conn_db().execute("SELECT COUNT(*) FROM symbol_docs").fetchone()[0]

        # --- Build caller index for fast trace lookups ---
        print("Building caller index...", file=sys.stderr)
        t_caller = time.time()
        _build_caller_index(indexer, files_to_index if is_incremental else list(all_files.keys()))
        print(f"  Caller index built in {time.time() - t_caller:.2f}s", file=sys.stderr)

        # Save metadata
        total_caller_count = indexer._conn_db().execute("SELECT COUNT(*) FROM caller_index").fetchone()[0]
        indexer.save_meta({
            "last_updated": time.strftime("%Y-%m-%d %H:%M:%S"),
            "embedding_model": config.embedding_model,
            "file_count": total_file_count,
            "symbol_count": total_symbol_count,
            "caller_count": total_caller_count,
        })

        elapsed = time.time() - start_time
        print(json.dumps({
            "files_indexed": len(file_docs),
            "total_files": total_file_count,
            "symbols_indexed": symbol_count,
            "total_symbols": total_symbol_count,
            "api_batches": (len(file_texts) - 1) // config.embedding_batch_size + 1 + (
                (len(symbol_texts) - 1) // config.embedding_batch_size + 1 if symbol_texts else 0
            ),
            "elapsed_seconds": round(elapsed, 1),
            "incremental": is_incremental,
        }, ensure_ascii=False))

    finally:
        embedder.close()
        indexer.close()


if __name__ == "__main__":
    main()
