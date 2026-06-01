"""Async-safe TTL cache with LRU eviction."""

import asyncio
import time
from typing import Any


class TTLCache:
    def __init__(self, ttl: float = 120.0, max_size: int = 512) -> None:
        self._data: dict[str, tuple[float, Any]] = {}
        self._lock = asyncio.Lock()
        self._ttl = ttl
        self._max = max_size

    async def get(self, key: str) -> Any:
        async with self._lock:
            if entry := self._data.get(key):
                if time.monotonic() - entry[0] < self._ttl:
                    return entry[1]
                del self._data[key]
        return None

    async def set(self, key: str, val: Any) -> None:
        async with self._lock:
            if len(self._data) >= self._max:
                oldest = min(self._data, key=lambda k: self._data[k][0])
                del self._data[oldest]
            self._data[key] = (time.monotonic(), val)

    async def clear(self) -> None:
        async with self._lock:
            self._data.clear()

    def __len__(self) -> int:
        return len(self._data)
