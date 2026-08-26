"""Extended scanner tests — cache coherence."""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pahebatcher.extract.scanner import AnimePaheScanner
from pahebatcher.models import EpisodeInfo


class TestCachePath:
    def test_cache_path_sanitize(self, tmp_path: Path) -> None:
        p = AnimePaheScanner._cache_path(tmp_path, "sess123", "My Anime: Test")
        assert p.name == "_scan_cache.json"
        assert "My_Anime_Test_sess123" in str(p)

    def test_load_cache_expired(self, tmp_path: Path) -> None:
        cache_path = tmp_path / "Anime_sess" / "_scan_cache.json"
        cache_path.parent.mkdir(parents=True)
        data = {
            "cached_at": time.time() - 7200,  # 2 hours ago
            "title": "Anime",
            "session": "sess",
            "host": "animepahe.com",
            "total": 1,
            "episodes": [],
        }
        cache_path.write_text(json.dumps(data))
        result = AnimePaheScanner._load_cache(cache_path, cache_ttl=60)
        assert result is None  # expired

    def test_load_cache_fresh(self, tmp_path: Path) -> None:
        cache_path = tmp_path / "Anime_sess" / "_scan_cache.json"
        cache_path.parent.mkdir(parents=True)
        data = {
            "cached_at": time.time(),
            "title": "Anime",
            "session": "sess",
            "host": "animepahe.com",
            "total": 1,
            "episodes": [
                {"number": 1, "session": "s1", "title": "T", "fansub": "F", "audio": "jpn", "play_url": "u"},
            ],
        }
        cache_path.write_text(json.dumps(data))
        result = AnimePaheScanner._load_cache(cache_path, cache_ttl=60)
        assert result is not None
        assert result.title == "Anime"
        assert len(result.episodes) == 1

    def test_load_cache_disabled(self, tmp_path: Path) -> None:
        cache_path = tmp_path / "Anime_sess" / "_scan_cache.json"
        cache_path.parent.mkdir(parents=True)
        cache_path.write_text("{}")
        assert AnimePaheScanner._load_cache(cache_path, cache_ttl=0) is None

    def test_load_cache_invalid_json(self, tmp_path: Path) -> None:
        cache_path = tmp_path / "Anime_sess" / "_scan_cache.json"
        cache_path.parent.mkdir(parents=True)
        cache_path.write_text("not json")
        assert AnimePaheScanner._load_cache(cache_path, cache_ttl=60) is None


class TestScanCacheStable:
    async def test_scan_uses_glob_for_cache(self, tmp_path: Path) -> None:
        # Pre-populate cache with different title folder than placeholder
        real_title = "Real Anime"
        from pahebatcher.utils import sanitize

        safe = sanitize(real_title)
        sess = "test-sess-123"
        cache_file = tmp_path / f"{safe}_{sess}" / "_scan_cache.json"
        cache_file.parent.mkdir(parents=True)
        data = {
            "cached_at": time.time(),
            "title": real_title,
            "session": sess,
            "host": "animepahe.com",
            "total": 1,
            "episodes": [
                {"number": 1, "session": "ep1", "title": "Ep1", "fansub": "F", "audio": "jpn", "play_url": "u"},
            ],
        }
        cache_file.write_text(json.dumps(data))
        solver = MagicMock()
        scanner = AnimePaheScanner(solver, "animepahe.com", sess)
        # Should hit cache without calling fetch
        anime = await scanner.scan(tmp_path, cache_ttl=60)
        assert anime.title == real_title
        assert len(anime.episodes) == 1
        # Ensure no network call attempted (solver not called)
        solver.fetch_json.assert_not_called()  # type: ignore

    async def test_scan_fallback_when_no_cache(self, tmp_path: Path) -> None:
        solver = MagicMock()
        solver.fetch_json = AsyncMock(return_value=None)
        solver.fetch_html = AsyncMock(return_value=None)
        scanner = AnimePaheScanner(solver, "animepahe.com", "no-cache-sess")
        # Mock discover to return session itself, and _fetch_title to return Unknown
        with patch.object(AnimePaheScanner, "discover_all_sessions", new_callable=AsyncMock, return_value=["no-cache-sess"]):
            with patch.object(scanner, "_fetch_title", new_callable=AsyncMock, return_value="Unknown Anime"):
                with patch.object(scanner, "_fetch_page", new_callable=AsyncMock, return_value={"data": [], "last_page": 1}):
                    anime = await scanner.scan(tmp_path, cache_ttl=60)
                    assert anime.title == "Unknown Anime"
                    assert anime.episodes == []
