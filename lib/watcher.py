"""File system watcher for automatic incremental index updates."""

from __future__ import annotations

import fnmatch
import os
import signal
import sys
import threading
import time
from pathlib import Path

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from .config import PluginConfig
from .embedder import Embedder
from .indexer import Indexer
from .parser import format_for_embedding, format_symbol_for_embedding, parse_file

PID_FILE = ".kimi-index/watch.pid"


def _should_include(path: str, config: PluginConfig) -> bool:
    ext = Path(path).suffix.lower()
    if ext not in config.include_extensions:
        return False
    for pattern in config.exclude_patterns:
        if fnmatch.fnmatch(path, pattern) or fnmatch.fnmatch(os.path.basename(path), pattern):
            return False
    return True


class _IndexEventHandler(FileSystemEventHandler):
    """Handles file change events and triggers incremental re-indexing."""

    def __init__(self, config: PluginConfig) -> None:
        self.config = config
        self._pending: set[str] = set()
        self._lock = threading.Lock()
        self._timer: threading.Timer | None = None
        self._debounce_seconds = 3.0

    def _schedule_update(self) -> None:
        with self._lock:
            if self._timer:
                self._timer.cancel()
            self._timer = threading.Timer(self._debounce_seconds, self._flush)
            self._timer.daemon = True
            self._timer.start()

    def _flush(self) -> None:
        with self._lock:
            files = list(self._pending)
            self._pending.clear()
            self._timer = None

        if not files:
            return

        print(f"[watch] Incremental update for {len(files)} changed files...", file=sys.stderr)
        try:
            self._reindex_files(files)
        except Exception as e:
            print(f"[watch] Incremental update failed: {e}", file=sys.stderr)
            import traceback
            traceback.print_exc(file=sys.stderr)

    def _reindex_files(self, files: list[str]) -> None:
        """Re-index a batch of changed files (true incremental update)."""
        import numpy as np

        # Create fresh connections per batch (thread-safe)
        indexer = Indexer()
        embedder = Embedder()

        try:
            symbol_paths_set = set(self.config.symbol_paths)
            file_docs = []
            file_texts = []
            need_symbol_update = False
            symbol_files = set()

            for fpath in files:
                if not _should_include(fpath, self.config):
                    continue
                try:
                    doc = parse_file(fpath, max_lines=self.config.file_max_lines)
                    mtime = Path(fpath).stat().st_mtime
                    file_docs.append({
                        "file": doc.file,
                        "language": doc.language,
                        "line_count": doc.line_count,
                        "exports": doc.exports,
                        "text_preview": doc.text_preview,
                        "mtime": mtime,
                    })
                    file_texts.append(format_for_embedding(doc))

                    for sp in symbol_paths_set:
                        if fpath.startswith(f"{sp}/") or fnmatch.fnmatch(fpath, f"{sp}/**"):
                            need_symbol_update = True
                            symbol_files.add(fpath)
                            break
                except Exception as e:
                    print(f"[watch] Failed to parse {fpath}: {e}", file=sys.stderr)

            if not file_docs:
                return

            # --- Incremental file-level vector update ---
            old_vectors = indexer.load_file_vectors()
            all_ids = indexer.get_all_file_doc_ids()

            # Update DB
            new_file_ids = indexer.update_file_docs(file_docs)
            updated_ids = set(new_file_ids)

            # Re-fetch all file docs in id order
            rows = indexer._conn_db().execute(
                "SELECT id, file, text_preview FROM file_docs ORDER BY id"
            ).fetchall()
            total_files = len(rows)

            # Determine which files need re-embedding
            embed_texts = []
            embed_indices = []
            for i, row in enumerate(rows):
                if row["id"] in updated_ids:
                    try:
                        doc = parse_file(row["file"], max_lines=self.config.file_max_lines)
                        embed_texts.append(format_for_embedding(doc))
                    except Exception:
                        embed_texts.append(row["text_preview"] or "")
                    embed_indices.append(i)
                else:
                    embed_texts.append(None)
                    embed_indices.append(None)

            # Embed only changed files
            changed_texts = [t for t in embed_texts if t is not None]
            if changed_texts:
                changed_vectors = embedder.embed_batch(changed_texts)
            else:
                changed_vectors = []

            # Build new vector matrix
            new_vec_list = []
            cv_idx = 0
            for i in range(total_files):
                if embed_indices[i] is not None and cv_idx < len(changed_vectors):
                    new_vec_list.append(changed_vectors[cv_idx])
                    cv_idx += 1
                elif i < len(old_vectors):
                    new_vec_list.append(old_vectors[i].tolist())
                else:
                    new_vec_list.append([0.0] * self.config.embedding_dimensions)

            new_vectors = np.array(new_vec_list, dtype=np.float32)
            indexer.save_file_vectors(new_vectors)

            # --- Symbol-level incremental update ---
            if need_symbol_update and symbol_files:
                indexer.delete_symbols_for_files(symbol_files)
                symbol_docs = []
                symbol_texts = []
                file_id_lookup = indexer.get_all_file_doc_ids()
                for fpath in symbol_files:
                    if fpath not in file_id_lookup:
                        continue
                    parent_id = file_id_lookup[fpath]
                    try:
                        doc = parse_file(fpath, max_lines=self.config.file_max_lines)
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
                    except Exception:
                        pass

                if symbol_texts:
                    sym_vectors = embedder.embed_batch(symbol_texts)
                    indexer.update_symbol_docs(symbol_docs)
                    # Rebuild all symbol vectors
                    all_sym_rows = indexer._conn_db().execute(
                        "SELECT id, file, symbol, line_start, text FROM symbol_docs ORDER BY id"
                    ).fetchall()
                    all_sym_texts = []
                    for row in all_sym_rows:
                        try:
                            doc = parse_file(row["file"], max_lines=self.config.file_max_lines)
                            for sym in doc.symbols:
                                if sym.symbol == row["symbol"] and sym.line_start == row["line_start"]:
                                    all_sym_texts.append(format_symbol_for_embedding(doc, sym))
                                    break
                            else:
                                all_sym_texts.append(row["text"] or "")
                        except Exception:
                            all_sym_texts.append(row["text"] or "")
                    if all_sym_texts:
                        all_sym_vectors = embedder.embed_batch(all_sym_texts)
                        indexer.save_symbol_vectors(
                            np.array(all_sym_vectors, dtype=np.float32)
                        )

            # Update meta
            indexer.save_meta({
                "last_updated": time.strftime("%Y-%m-%d %H:%M:%S"),
                "embedding_model": self.config.embedding_model,
            })
            print(f"[watch] Incremental update complete ({len(file_docs)} files)", file=sys.stderr)
        finally:
            embedder.close()
            indexer.close()

    def on_modified(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        path = str(event.src_path)
        if not _should_include(path, self.config):
            return
        with self._lock:
            self._pending.add(path)
        self._schedule_update()

    def on_created(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        path = str(event.src_path)
        if not _should_include(path, self.config):
            return
        with self._lock:
            self._pending.add(path)
        self._schedule_update()

    def on_deleted(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        path = str(event.src_path)
        try:
            indexer = Indexer()
            all_files = {r["file"] for r in indexer._conn_db().execute("SELECT file FROM file_docs").fetchall()}
            if path in all_files:
                all_files.remove(path)
                indexer.delete_files_not_in(all_files)
            indexer.close()
        except Exception:
            pass


def start_watcher(cwd: str | None = None) -> None:
    """Start the file watcher daemon."""
    config = PluginConfig()

    pid_file = _get_pid_file(cwd)
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    with open(pid_file, "w") as f:
        f.write(str(os.getpid()))

    observer = Observer()
    handler = _IndexEventHandler(config)

    watch_path = str(Path(cwd) if cwd else Path.cwd())
    observer.schedule(handler, watch_path, recursive=True)
    observer.start()

    print(f"[watch] Started watching {watch_path}", file=sys.stderr)

    def _shutdown(_signum: int, _frame: Any) -> None:
        print("[watch] Shutting down...", file=sys.stderr)
        observer.stop()
        observer.join()
        if pid_file.exists():
            pid_file.unlink()
        sys.exit(0)

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    try:
        while observer.is_alive():
            observer.join(1)
    finally:
        observer.stop()
        observer.join()
        if pid_file.exists():
            pid_file.unlink()


def stop_watcher(cwd: str | None = None) -> bool:
    """Stop the running watcher. Returns True if a watcher was stopped."""
    pid_file = _get_pid_file(cwd)
    if not pid_file.exists():
        return False
    try:
        pid = int(pid_file.read_text().strip())
        os.kill(pid, signal.SIGTERM)
        for _ in range(10):
            time.sleep(0.2)
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
        else:
            os.kill(pid, signal.SIGKILL)
        if pid_file.exists():
            pid_file.unlink()
        return True
    except Exception:
        if pid_file.exists():
            pid_file.unlink()
        return False


def is_watcher_running(cwd: str | None = None) -> bool:
    pid_file = _get_pid_file(cwd)
    if not pid_file.exists():
        return False
    try:
        pid = int(pid_file.read_text().strip())
        os.kill(pid, 0)
        return True
    except Exception:
        if pid_file.exists():
            pid_file.unlink()
        return False


def _get_pid_file(cwd: str | None = None) -> Path:
    base = Path(cwd) if cwd else Path.cwd()
    return base / ".kimi-index" / "watch.pid"
