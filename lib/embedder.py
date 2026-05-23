"""Remote embedding API client with batching, retry, and rate-limit handling."""

from __future__ import annotations

import sys
import time
from typing import Any

import httpx

from .config import PluginConfig

DEFAULT_TIMEOUT = 60.0
MAX_RETRIES = 5
RETRY_DELAY_BASE = 1.0


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

        # Truncate extremely long inputs to avoid token limits
        max_chars = 8000
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

    def close(self) -> None:
        self.client.close()
