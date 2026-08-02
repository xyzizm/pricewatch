"""
Remembering the last price we saw.

Kept behind a tiny interface so the JSON file can be swapped for Redis,
SQLite or anything else without touching the rest of the code.
"""

from __future__ import annotations

import json
import os
from typing import Dict, Optional, Protocol


class Store(Protocol):
    def get(self, key: str) -> Optional[float]: ...
    def set(self, key: str, value: float) -> None: ...
    def save(self) -> None: ...


class MemoryStore:
    """In-memory only. Used by tests, and by anyone who wants no files."""

    def __init__(self, initial: Optional[Dict[str, float]] = None):
        self._data: Dict[str, float] = dict(initial or {})

    def get(self, key: str) -> Optional[float]:
        return self._data.get(key)

    def set(self, key: str, value: float) -> None:
        self._data[key] = float(value)

    def save(self) -> None:
        pass  # nothing to persist

    @property
    def data(self) -> Dict[str, float]:
        return dict(self._data)


class JsonStore:
    """Persists to a JSON file. Writes atomically so a crash cannot corrupt it."""

    def __init__(self, path: str):
        self.path = path
        self._data: Dict[str, float] = {}
        self._load()

    def _load(self) -> None:
        if not os.path.exists(self.path):
            return
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                loaded = json.load(fh)
            if isinstance(loaded, dict):
                self._data = {k: float(v) for k, v in loaded.items()}
        except (json.JSONDecodeError, OSError, TypeError, ValueError):
            print(f"! state file unreadable ({self.path}), starting fresh")

    def get(self, key: str) -> Optional[float]:
        return self._data.get(key)

    def set(self, key: str, value: float) -> None:
        self._data[key] = float(value)

    def save(self) -> None:
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self._data, fh)
        os.replace(tmp, self.path)  # atomic on POSIX and Windows
