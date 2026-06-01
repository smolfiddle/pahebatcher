"""Test fixtures for pahebatcher."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pahebatcher.models import AnimeInfo, AppContext, EpisodeInfo, StreamInfo


@pytest.fixture
def sample_episodes() -> list[EpisodeInfo]:
    return [
        EpisodeInfo(1, "s1", "Episode 1", "SubsPlease", "jpn", "https://animepahe.com/play/uuid/s1"),
        EpisodeInfo(2, "s2", "Episode 2", "SubsPlease", "jpn", "https://animepahe.com/play/uuid/s2"),
        EpisodeInfo(3, "s3", "Episode 3", "SubsPlease", "jpn", "https://animepahe.com/play/uuid/s3"),
        EpisodeInfo(4, "s4", "Episode 4", "SubsPlease", "jpn", "https://animepahe.com/play/uuid/s4"),
    ]


@pytest.fixture
def sample_anime(sample_episodes: list[EpisodeInfo]) -> AnimeInfo:
    return AnimeInfo(
        session="test-session",
        title="Test Anime",
        host="animepahe.com",
        total=4,
        episodes=sample_episodes,
        has_session=False,
    )


@pytest.fixture
def sample_stream_info() -> StreamInfo:
    return StreamInfo(
        url="http://test.m3u8",
        cookies=[{"name": "test", "value": "val"}],
        user_agent="TestUA/1.0",
        referer="http://ref",
        title="Test Episode",
        audio="jpn",
        fansub="SubsPlease",
    )


@pytest.fixture
def app_context(tmp_path: Path) -> AppContext:
    return AppContext(
        output_dir=str(tmp_path / "downloads"),
        cache_dir=tmp_path / "cache",
        quality=1080,
        audio_lang="jpn",
        max_parallel=2,
        hls_workers=8,
        keep_temp=False,
        list_only=False,
        flaresolverr_url="http://localhost:8191/v1",
    )


@pytest.fixture
def mock_solver() -> MagicMock:
    solver = MagicMock()
    solver.request = AsyncMock()
    solver.fetch_json = AsyncMock()
    solver.fetch_html = AsyncMock()
    solver.ping = AsyncMock(return_value=True)
    solver.start = AsyncMock()
    solver.close = AsyncMock()
    solver.destroy_session = AsyncMock()
    return solver


@pytest.fixture
def mock_http() -> MagicMock:
    http = MagicMock()
    http.get = AsyncMock(return_value=b"fake segment data")
    http.start = AsyncMock()
    http.close = AsyncMock()
    return http


@pytest.fixture
def sample_m3u8_content() -> str:
    return (
        "#EXTM3U\n"
        "#EXT-X-VERSION:3\n"
        "#EXT-X-TARGETDURATION:10\n"
        "#EXT-X-MEDIA-SEQUENCE:0\n"
        "#EXTINF:10.0,\n"
        "segment1.ts\n"
        "#EXTINF:10.0,\n"
        "segment2.ts\n"
    )


@pytest.fixture
def sample_m3u8_aes() -> str:
    return (
        "#EXTM3U\n"
        "#EXT-X-VERSION:3\n"
        "#EXT-X-TARGETDURATION:10\n"
        "#EXT-X-MEDIA-SEQUENCE:0\n"
        "#EXT-X-KEY:METHOD=AES-128,URI=\"https://key.url\",IV=0x1234567890ABCDEF1234567890ABCDEF\n"
        "#EXTINF:10.0,\n"
        "segment1.ts\n"
    )
