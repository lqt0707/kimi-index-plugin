"""Plugin configuration management."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


DEFAULT_CONFIG = {
    "embedding_model": "moonshot-v3-embedding",
    "embedding_batch_size": 64,
    "embedding_dimensions": 1024,
    "file_max_lines": 200,
    "symbol_paths": [],
    "exclude_patterns": [
        "**/*.test.ts",
        "**/*.spec.ts",
        "**/*.test.tsx",
        "**/*.spec.tsx",
        "**/*.test.js",
        "**/*.spec.js",
        "**/*.test.jsx",
        "**/*.spec.jsx",
        "**/*.d.ts",
        "**/node_modules/**",
        "**/dist/**",
        "**/.git/**",
        "**/.kimi-index/**",
    ],
    "include_extensions": [
        ".ts",
        ".tsx",
        ".js",
        ".jsx",
        ".vue",
        ".py",
        ".go",
        ".rs",
        ".java",
        ".kt",
        ".swift",
        ".rb",
        ".php",
    ],
}


class PluginConfig:
    """Runtime configuration loaded from config.json and env vars."""

    def __init__(self, config_path: Path | None = None):
        self._data: dict[str, Any] = dict(DEFAULT_CONFIG)
        self._config_path = config_path or self._find_config_path()
        self._load()

    def _find_config_path(self) -> Path:
        """Find config.json relative to this file."""
        return Path(__file__).parent.parent / "config.json"

    @staticmethod
    def _load_kimi_credentials() -> str | None:
        """Read fresh access_token from kimi-cli credentials file."""
        import time
        creds_paths = [
            Path.home() / ".kimi" / "credentials" / "kimi-code.json",
            Path.home() / ".config" / "kimi" / "credentials" / "kimi-code.json",
        ]
        for path in creds_paths:
            if not path.exists():
                continue
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                token = data.get("access_token", "")
                expires_at = data.get("expires_at", 0)
                if token and expires_at > time.time():
                    return token
            except Exception:
                pass
        return None

    @staticmethod
    def _is_token_expired(token: str) -> bool:
        """Check if a JWT token is expired."""
        import base64
        import time
        if not token or token.count(".") != 2:
            return True
        try:
            payload_b64 = token.split(".")[1]
            padding = 4 - len(payload_b64) % 4
            if padding != 4:
                payload_b64 += "=" * padding
            payload = json.loads(base64.urlsafe_b64decode(payload_b64))
            exp = payload.get("exp", 0)
            return exp <= time.time()
        except Exception:
            return True

    def _load(self) -> None:
        if self._config_path.exists():
            try:
                with open(self._config_path, "r", encoding="utf-8") as f:
                    user = json.load(f)
                self._data.update(user)
            except Exception:
                pass

        # Env overrides (injected by kimi-cli plugin system)
        if os.environ.get("KIMI_INDEX_API_KEY"):
            self._data["api_key"] = os.environ["KIMI_INDEX_API_KEY"]
        if os.environ.get("KIMI_INDEX_BASE_URL"):
            self._data["base_url"] = os.environ["KIMI_INDEX_BASE_URL"]

        # Refresh token if expired or missing
        current_key = self._data.get("api_key")
        if not current_key or self._is_token_expired(current_key):
            fresh_key = self._load_kimi_credentials()
            if fresh_key:
                self._data["api_key"] = fresh_key

    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    @property
    def api_key(self) -> str | None:
        return self._data.get("api_key")

    @property
    def base_url(self) -> str | None:
        return self._data.get("base_url")

    @property
    def embedding_model(self) -> str:
        return self._data.get("embedding_model", "moonshot-v3-embedding")

    @property
    def embedding_batch_size(self) -> int:
        return self._data.get("embedding_batch_size", 64)

    @property
    def embedding_dimensions(self) -> int:
        return self._data.get("embedding_dimensions", 1024)

    @property
    def file_max_lines(self) -> int:
        return self._data.get("file_max_lines", 200)

    @property
    def symbol_paths(self) -> list[str]:
        return self._data.get("symbol_paths", [])

    @property
    def exclude_patterns(self) -> list[str]:
        return self._data.get("exclude_patterns", [])

    @property
    def include_extensions(self) -> list[str]:
        return self._data.get("include_extensions", [])
