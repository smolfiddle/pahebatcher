"""Tests for auto-retry (3 attempts) — episode, resolver, batch."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pahebatcher.downloader import BatchOrchestrator, EpisodeDownloader
from pahebatcher.models import AnimeInfo, AppContext, EpisodeInfo, StreamInfo
from pahebatcher.ui.dashboard import Dashboard


class TestEpisodeRetry:
    async def test_retry_success_on_second_attempt(
        self, tmp_path: Path, app_context: AppContext, sample_anime: AnimeInfo,
    ) -> None:
        app_context.output_dir = str(tmp_path / "out")
        app_context.cache_dir = tmp_path / "cache"
        app_context.auto_retry = 2
        app_context.hls_workers = 2
        dash = MagicMock(spec=Dashboard)
        http = MagicMock()
        solver = MagicMock()

        m3u8_content = "#EXTM3U\n#EXTINF:10.0,\nseg0.ts\n"
        # First http.get for m3u8 succeeds, then segment fetch fails once then succeeds
        call_count = 0

        async def fake_get(url, headers=None, timeout=60):
            nonlocal call_count
            call_count += 1
            if "seg0.ts" in url and call_count == 2:
                # First segment fetch fails
                raise ConnectionError("transient")
            return b"segmentdata"

        http.get = AsyncMock(side_effect=fake_get)

        with patch("pahebatcher.downloader.fetch_m3u8", new_callable=AsyncMock, return_value=m3u8_content):
            with patch("pahebatcher.downloader.resolve_m3u8", new_callable=AsyncMock) as mock_resolve:
                mock_resolve.return_value = [
                    {"url": "https://cdn/seg0.ts", "key_url": None, "iv": None},
                ]
                with patch("pahebatcher.downloader.SegmentStore") as MockStore:
                    mock_store = MagicMock()
                    mock_store.done_indices.return_value = set()
                    mock_store.save_metadata = MagicMock()
                    mock_store.write_seg = MagicMock()
                    mock_store.assemble.return_value = True
                    mock_store.cleanup = MagicMock()
                    MockStore.return_value = mock_store
                    # Make second attempt succeed by not failing on retry
                    # We need to make fake_get succeed on retry — after first failure, next call succeeds
                    # Reset call_count logic: use side_effect list
                    http.get = AsyncMock(side_effect=[b"segmentdata", b"segmentdata"])
                    # Actually for episode retry, we need to mock the whole segment fetch to fail first attempt
                    # Simulate first attempt's gather fails, second succeeds
                    original_gather = asyncio.gather

                    ep = EpisodeInfo(1, "s1", "Retry Ep", "F", "jpn", "https://play/url")
                    info = StreamInfo("https://cdn.m3u8", [], "UA", "ref", title="T")
                    dl = EpisodeDownloader(app_context, sample_anime, dash, http, solver)
                    # First call to http.get for m3u8 is mocked, second is segment, we want segment to fail once
                    # Use patch to make http.get fail first segment then succeed
                    http.get.reset_mock()
                    http.get.side_effect = [b"segmentdata", ConnectionError("fail"), b"segmentdata"]
                    # But our EpisodeDownloader does fetch_m3u8 via http.get indirectly? We mocked fetch_m3u8
                    # So next http.get is segment
                    # To test retry, we make assemble fail first then succeed? Simpler: make http.get for segment fail
                    # Let's just test that retry is attempted by making http.get raise once
                    # We'll patch asyncio.sleep to avoid delay
                    with patch("asyncio.sleep", new_callable=AsyncMock):
                        # Need to handle m3u8 and segment: we have mocked fetch_m3u8, so only segment matters
                        # Make segment fail first attempt, succeed second
                        seq = [ConnectionError("transient"), b"segmentdata"]

                        async def seg_get(url, headers=None, timeout=60):
                            if not hasattr(seg_get, "called"):
                                seg_get.called = 0  # type: ignore
                            seg_get.called += 1  # type: ignore
                            if seg_get.called == 1:  # type: ignore
                                raise ConnectionError("transient")
                            return b"segmentdata"

                        http.get = AsyncMock(side_effect=seg_get)
                        result = await dl.run(ep, info)
                        # With retry, should succeed on second attempt
                        assert result is not None
                        assert http.get.call_count >= 2

    async def test_no_retry_on_permanent_error(
        self, tmp_path: Path, app_context: AppContext, sample_anime: AnimeInfo,
    ) -> None:
        app_context.output_dir = str(tmp_path / "out")
        app_context.cache_dir = tmp_path / "cache"
        app_context.auto_retry = 2
        dash = MagicMock(spec=Dashboard)
        http = MagicMock()
        solver = MagicMock()
        http.get = AsyncMock(side_effect=RuntimeError("No Kwik link found"))
        with patch("pahebatcher.downloader.fetch_m3u8", new_callable=AsyncMock, side_effect=RuntimeError("No Kwik link found")):
            ep = EpisodeInfo(1, "s1", "T", "F", "jpn", "url")
            info = StreamInfo("https://cdn.m3u8", [], "UA", "ref")
            dl = EpisodeDownloader(app_context, sample_anime, dash, http, solver)
            with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
                result = await dl.run(ep, info)
                assert result is None
                # Should not sleep (no retry) for permanent error
                mock_sleep.assert_not_called()


class TestResolverRetry:
    async def test_resolver_retries_on_timeout(
        self, tmp_path: Path, app_context: AppContext, sample_anime: AnimeInfo,
    ) -> None:
        app_context.output_dir = str(tmp_path / "out")
        app_context.cache_dir = tmp_path / "cache"
        app_context.auto_retry = 2
        http = MagicMock()
        http.start = AsyncMock()
        http.close = AsyncMock()
        solver = MagicMock()

        anime = AnimeInfo(
            session="sess", title="Test", host="h",
            episodes=[EpisodeInfo(1, "s1", "Ep1", "F", "jpn", "https://play/s1")],
            total=1,
        )
        orch = BatchOrchestrator(app_context, anime, http, solver)
        # First extract_stream fails with TimeoutError, second succeeds
        mock_info = StreamInfo("https://cdn.m3u8", [], "UA", "ref")
        call = 0

        async def fake_extract(*args, **kwargs):
            nonlocal call
            call += 1
            if call == 1:
                raise TimeoutError("timeout")
            return mock_info

        with patch("pahebatcher.downloader.Dashboard") as MockDash:
            mock_dash = MagicMock()
            MockDash.return_value = mock_dash
            with patch("pahebatcher.downloader.extract_stream", side_effect=fake_extract):
                with patch("pahebatcher.downloader.EpisodeDownloader") as MockDL:
                    mock_dl = MagicMock()
                    mock_dl.run = AsyncMock(return_value=Path("/tmp/out.mp4"))
                    MockDL.return_value = mock_dl
                    with patch("asyncio.sleep", new_callable=AsyncMock):
                        results = await orch.download(anime.episodes)
                        assert results["s1"] == Path("/tmp/out.mp4")
                        assert call == 2


class TestBatchRetry:
    async def test_batch_retries_failed_episodes(
        self, tmp_path: Path, app_context: AppContext, sample_anime: AnimeInfo,
    ) -> None:
        app_context.output_dir = str(tmp_path / "out")
        app_context.cache_dir = tmp_path / "cache"
        app_context.auto_retry = 1  # one batch retry
        (tmp_path / "out").mkdir(parents=True, exist_ok=True)
        http = MagicMock()
        solver = MagicMock()
        anime = AnimeInfo(
            session="sess", title="Test", host="h",
            episodes=[
                EpisodeInfo(1, "s1", "Ep1", "F", "jpn", "https://play/s1"),
                EpisodeInfo(2, "s2", "Ep2", "F", "jpn", "https://play/s2"),
            ],
            total=2,
        )
        orch = BatchOrchestrator(app_context, anime, http, solver)
        mock_info = StreamInfo("https://cdn.m3u8", [], "UA", "ref")
        # First pass: ep1 fails, ep2 succeeds. Second pass (batch retry): ep1 succeeds
        call_counts = {"s1": 0, "s2": 0}

        async def fake_run(ep, info):
            # ep.session determines
            call_counts[ep.session] += 1
            if ep.session == "s1" and call_counts[ep.session] == 1:
                return None
            return Path(f"/tmp/{ep.session}.mp4")

        with patch("pahebatcher.downloader.Dashboard") as MockDash:
            mock_dash = MagicMock()
            MockDash.return_value = mock_dash
            with patch("pahebatcher.downloader.extract_stream", new_callable=AsyncMock, return_value=mock_info):
                with patch("pahebatcher.downloader.EpisodeDownloader") as MockDL:
                    mock_dl = MagicMock()
                    mock_dl.run = AsyncMock(side_effect=fake_run)
                    MockDL.return_value = mock_dl
                    with patch("asyncio.sleep", new_callable=AsyncMock):
                        results = await orch.download(anime.episodes)
                        # Both should succeed after batch retry
                        assert results["s1"] is not None
                        assert results["s2"] is not None
                        assert call_counts["s1"] == 2  # retried
                        assert call_counts["s2"] == 1
