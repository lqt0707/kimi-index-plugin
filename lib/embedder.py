"""Remote embedding API client with batching, retry, and rate-limit handling."""

from __future__ import annotations

import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import httpx

from .config import PluginConfig

DEFAULT_TIMEOUT = 60.0
MAX_RETRIES = 5
RETRY_DELAY_BASE = 1.0
DEFAULT_MAX_WORKERS = 8  # 2x concurrency for large projects


def _should_retry(status_code: int) -> bool:
    return status_code in (429, 502, 503, 504) or status_code >= 500


class Embedder:
    """OpenAI-compatible embedding API client."""

    def __init__(self, config: PluginConfig | None = None):
        self.config = config or PluginConfig()
        self.api_key = self.config.api_key
        self.base_url = self.config.base_url or ""
        self.model = self.config.embedding_model
        self.batch_size = self.config.embedding_batch_size
        self.dimensions = self.config.embedding_dimensions
        self.max_workers = getattr(config, 'max_workers', DEFAULT_MAX_WORKERS) if config else DEFAULT_MAX_WORKERS

        if not self.api_key or not self.base_url:
            raise RuntimeError(
                "Missing API credentials. Ensure kimi-cli is logged in or configure api_key/base_url."
            )

        self.client = httpx.Client(
            base_url=self.base_url.rstrip("/"),
            timeout=httpx.Timeout(DEFAULT_TIMEOUT, connect=10.0),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
        )

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts, returning a list of embedding vectors."""
        if not texts:
            return []

        # Truncate long inputs to reduce token usage and API latency
        max_chars = 4000
        trimmed = [t[:max_chars] if len(t) > max_chars else t for t in texts]

        payload = {
            "model": self.model,
            "input": trimmed,
        }

        last_error: Exception | None = None
        for attempt in range(MAX_RETRIES):
            try:
                resp = self.client.post("/embeddings", json=payload)
                if resp.status_code == 200:
                    data = resp.json()
                    results = data.get("data", [])
                    # Sort by index to maintain order
                    results.sort(key=lambda x: x.get("index", 0))
                    embeddings = []
                    for item in results:
                        vec = item.get("embedding", [])
                        if not vec:
                            vec = [0.0] * self.dimensions
                        embeddings.append(vec)
                    # Fill missing with zeros
                    while len(embeddings) < len(texts):
                        embeddings.append([0.0] * self.dimensions)
                    return embeddings

                if _should_retry(resp.status_code) and attempt < MAX_RETRIES - 1:
                    delay = RETRY_DELAY_BASE * (2 ** attempt)
                    print(f"Embedding API rate-limited (status {resp.status_code}), retrying in {delay}s...", file=sys.stderr)
                    time.sleep(delay)
                    continue

                resp.raise_for_status()
            except httpx.HTTPStatusError as e:
                last_error = e
                if attempt < MAX_RETRIES - 1 and _should_retry(e.response.status_code):
                    delay = RETRY_DELAY_BASE * (2 ** attempt)
                    time.sleep(delay)
                    continue
                raise RuntimeError(f"Embedding API error: {e.response.status_code} - {e.response.text}") from e
            except Exception as e:
                last_error = e
                if attempt < MAX_RETRIES - 1:
                    delay = RETRY_DELAY_BASE * (2 ** attempt)
                    time.sleep(delay)
                    continue
                raise RuntimeError(f"Embedding API failed after {MAX_RETRIES} retries: {e}") from e

        raise RuntimeError(f"Embedding API failed: {last_error}")

    def embed_single(self, text: str) -> list[float]:
        """Embed a single text."""
        results = self.embed_batch([text])
        return results[0] if results else [0.0] * self.dimensions

    def _create_client(self) -> httpx.Client:
        """Create a fresh httpx client (thread-safe per-instance)."""
        return httpx.Client(
            base_url=self.base_url.rstrip("/"),
            timeout=httpx.Timeout(DEFAULT_TIMEOUT, connect=10.0),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )

    def embed_batches(self, texts: list[str]) -> list[list[float]]:
        """Embed all texts with concurrent batch calls using per-thread clients.
        
        Each worker thread gets its own httpx client to avoid lock contention
        on the shared connection pool, maximizing throughput for large projects.
        """
        if not texts:
            return []

        batch_size = self.batch_size
        if len(texts) <= batch_size:
            return self.embed_batch(texts)

        # Split into batches
        batches: list[list[str]] = []
        for i in range(0, len(texts), batch_size):
            batches.append(texts[i:i + batch_size])

        # Determine concurrency level
        max_workers = min(self.max_workers, len(batches))
        if max_workers <= 1:
            all_vectors: list[list[float]] = []
            for batch in batches:
                all_vectors.extend(self.embed_batch(batch))
            return all_vectors

        # Thread-local client to avoid lock contention
        import threading
        local = threading.local()

        def _embed_batch_local(batch: list[str]) -> list[list[float]]:
            if not hasattr(local, "client"):
                local.client = self._create_client()
            return self._embed_with_client(batch, local.client)

        results_map: dict[int, list[list[float]]] = {}
        errors: list[Exception] = []

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(_embed_batch_local, batch): idx
                for idx, batch in enumerate(batches)
            }
            for future in as_completed(futures):
                idx = futures[future]
                try:
                    results_map[idx] = future.result()
                except Exception as e:
                    errors.append(e)

        # Close thread-local clients
        if hasattr(local, "client"):
            local.client.close()

        if errors:
            raise RuntimeError(f"Embedding failed for {len(errors)} batch(es): {errors[0]}")

        # Reassemble in order
        all_vectors: list[list[float]] = []
        for i in range(len(batches)):
            all_vectors.extend(results_map[i])
        return all_vectors

    def _embed_with_client(self, texts: list[str], client: httpx.Client) -> list[list[float]]:
        """Embed a batch using the provided client."""
        if not texts:
            return []

        max_chars = 4000
        trimmed = [t[:max_chars] if len(t) > max_chars else t for t in texts]

        payload = {
            "model": self.model,
            "input": trimmed,
        }

        last_error: Exception | None = None
        for attempt in range(MAX_RETRIES):
            try:
                resp = client.post("/embeddings", json=payload)
                if resp.status_code == 200:
                    data = resp.json()
                    results = data.get("data", [])
                    results.sort(key=lambda x: x.get("index", 0))
                    embeddings = []
                    for item in results:
                        vec = item.get("embedding", [])
                        if not vec:
                            vec = [0.0] * self.dimensions
                        embeddings.append(vec)
                    while len(embeddings) < len(texts):
                        embeddings.append([0.0] * self.dimensions)
                    return embeddings

                if _should_retry(resp.status_code) and attempt < MAX_RETRIES - 1:
                    delay = RETRY_DELAY_BASE * (2 ** attempt)
                    time.sleep(delay)
                    continue

                resp.raise_for_status()
            except httpx.HTTPStatusError as e:
                last_error = e
                if attempt < MAX_RETRIES - 1 and _should_retry(e.response.status_code):
                    delay = RETRY_DELAY_BASE * (2 ** attempt)
                    time.sleep(delay)
                    continue
                raise RuntimeError(f"Embedding API error: {e.response.status_code} - {e.response.text}") from e
            except Exception as e:
                last_error = e
                if attempt < MAX_RETRIES - 1:
                    delay = RETRY_DELAY_BASE * (2 ** attempt)
                    time.sleep(delay)
                    continue
                raise RuntimeError(f"Embedding failed after {MAX_RETRIES} retries: {e}") from e

        raise RuntimeError(f"Embedding failed: {last_error}")

    def close(self) -> None:
        self.client.close()
