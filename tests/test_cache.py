"""Tests for async TTLCache."""

from __future__ import annotations

import asyncio
import time

import pytest

from pahebatcher.cache import TTLCache


class TestTTLCache:
    async def test_set_and_get(self) -> None:
        cache = TTLCache(max_size=10)
        await cache.set("key1", "value1")
        assert await cache.get("key1") == "value1"

    async def test_miss(self) -> None:
        cache = TTLCache(max_size=10)
        assert await cache.get("missing") is None

    async def test_eviction_on_max_size(self) -> None:
        cache = TTLCache(max_size=2, ttl=9999)
        await cache.set("k1", "v1")
        await cache.set("k2", "v2")
        await cache.set("k3", "v3")
        assert await cache.get("k1") is None
        assert await cache.get("k2") == "v2"
        assert await cache.get("k3") == "v3"

    async def test_expiry(self) -> None:
        cache = TTLCache(ttl=0.01, max_size=10)
        await cache.set("key", "value")
        assert await cache.get("key") == "value"
        await asyncio.sleep(0.02)
        assert await cache.get("key") is None

    async def test_clear(self) -> None:
        cache = TTLCache(max_size=10)
        await cache.set("k1", "v1")
        await cache.set("k2", "v2")
        await cache.clear()
        assert await cache.get("k1") is None
        assert await cache.get("k2") is None

    async def test_len(self) -> None:
        cache = TTLCache(max_size=10)
        await cache.set("k1", "v1")
        await cache.set("k2", "v2")
        assert len(cache) == 2

    async def test_overwrite(self) -> None:
        cache = TTLCache(max_size=10)
        await cache.set("key", "old")
        await cache.set("key", "new")
        assert await cache.get("key") == "new"
        assert len(cache) == 1

    async def test_concurrent_access(self) -> None:
        cache = TTLCache(max_size=100)
        async def worker(i: int) -> None:
            await cache.set(f"key{i}", f"value{i}")
            val = await cache.get(f"key{i}")
            assert val == f"value{i}"
        await asyncio.gather(*(worker(i) for i in range(20)))
        assert len(cache) == 20
