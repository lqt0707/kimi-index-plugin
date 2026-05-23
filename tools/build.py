#!/usr/bin/env python3
"""CodeIndexBuild tool — build or update the code index."""

from __future__ import annotations

import fnmatch
import json
import os
import sys
import time
from pathlib import Path

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
    """Collect source files with their mtimes."""
    files: dict[str, float] = {}
    search_paths = paths if paths else ["."]
    for sp in search_paths:
        p = Path(sp)
        if p.is_file():
            if _should_include(str(p), config):
                files[str(p)] = p.stat().st_mtime
        elif p.is_dir():
            for root, _dirs, filenames in os.walk(p):
                root_path = Path(root)
                # Skip common directories quickly
                if any(part.startswith(".") and part in (".git", ".kimi-index", "node_modules", "dist", ".venv") for part in root_path.parts):
                    continue
                for fname in filenames:
                    fpath = str(root_path / fname)
                    if _should_include(fpath, config):
                        try:
                            files[fpath] = (root_path / fname).stat().st_mtime
                        except OSError:
                            pass
    return files


def _batch_embed(texts: list[str], embedder: Embedder) -> list[list[float]]:
    """Embed texts in batches."""
    all_vectors: list[list[float]] = []
    batch_size = embedder.batch_size
    for i in range(0, len(texts), batch_size):
        batch = texts[i : i + batch_size]
        vectors = embedder.embed_batch(batch)
        all_vectors.extend(vectors)
        # Progress to stderr
        print(f"  Embedded batch {i // batch_size + 1}/{(len(texts) - 1) // batch_size + 1} ({len(batch)} items)", file=sys.stderr)
    return all_vectors


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
            existing = indexer.get_all_file_doc_ids()
            # Keep files whose mtime matches or are new
            to_update = {
                f: mtime for f, mtime in all_files.items()
                if f not in existing or True  # Simple: re-index all for now; mtime check can be added
            }
            # For simplicity in v1, we rebuild file-level vectors from scratch when any file changes
            # A more optimized version would do per-file vector updates
            files_to_index = list(all_files.keys())
        else:
            files_to_index = list(all_files.keys())

        # Clear old data if rebuilding
        if force_rebuild:
            # Delete old index data
            indexer.delete_files_not_in(set())
            if indexer.file_vec_path.exists():
                indexer.file_vec_path.unlink()
            if indexer.symbol_vec_path.exists():
                indexer.symbol_vec_path.unlink()

        # --- File-level indexing ---
        print(f"Building file-level index for {len(files_to_index)} files...", file=sys.stderr)
        file_docs: list[dict] = []
        file_texts: list[str] = []
        file_symbols_map: dict[str, list] = {}

        for fpath in files_to_index:
            doc = parse_file(fpath, max_lines=config.file_max_lines)
            file_docs.append({
                "file": doc.file,
                "language": doc.language,
                "line_count": doc.line_count,
                "exports": doc.exports,
                "text_preview": doc.text_preview,
                "mtime": all_files.get(fpath, 0.0),
            })
            file_texts.append(format_for_embedding(doc))
            file_symbols_map[fpath] = doc.symbols

        # Embed file texts
        file_vectors = _batch_embed(file_texts, embedder)

        # Update DB and save vectors
        file_ids = indexer.update_file_docs(file_docs)
        file_vec_array = (
            np.array(file_vectors, dtype=np.float32) if file_vectors else np.array([]).reshape(0, 0)
        )
        indexer.save_file_vectors(file_vec_array)

        # --- Symbol-level indexing (optional) ---
        symbol_docs: list[dict] = []
        symbol_texts: list[str] = []
        symbol_count = 0

        if symbol_paths:
            # Determine which files are in symbol_paths
            symbol_files = set()
            for sp in symbol_paths:
                for fpath in files_to_index:
                    if fnmatch.fnmatch(fpath, f"{sp}/**") or fpath.startswith(f"{sp}/"):
                        symbol_files.add(fpath)

            # Clear old symbols for these files
            indexer.delete_symbols_for_files(symbol_files)

            # Build file_id map
            file_id_map = {doc["file"]: fid for doc, fid in zip(file_docs, file_ids)}

            print(f"Building symbol-level index for {len(symbol_files)} files...", file=sys.stderr)
            for fpath in symbol_files:
                if fpath not in file_id_map:
                    continue
                parent_id = file_id_map[fpath]
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
                sym_vec_array = (
                    np.array(symbol_vectors, dtype=np.float32) if symbol_vectors else np.array([]).reshape(0, 0)
                )
                indexer.save_symbol_vectors(sym_vec_array)

        # Save metadata
        indexer.save_meta({
            "last_updated": time.strftime("%Y-%m-%d %H:%M:%S"),
            "embedding_model": config.embedding_model,
            "file_count": len(file_docs),
            "symbol_count": symbol_count,
        })

        elapsed = time.time() - start_time
        print(json.dumps({
            "files_indexed": len(file_docs),
            "symbols_indexed": symbol_count,
            "api_batches": (len(file_texts) - 1) // config.embedding_batch_size + 1 + (
                (len(symbol_texts) - 1) // config.embedding_batch_size + 1 if symbol_texts else 0
            ),
            "elapsed_seconds": round(elapsed, 1),
        }, ensure_ascii=False))

    finally:
        embedder.close()
        indexer.close()


if __name__ == "__main__":
    main()
