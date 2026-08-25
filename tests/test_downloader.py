"""Tests for downloader orchestrator."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pahebatcher.downloader import BatchOrchestrator, EpisodeDownloader
from pahebatcher.models import AnimeInfo, AppContext, EpisodeInfo, StreamInfo
from pahebatcher.ui.dashboard import Dashboard


class TestBatchOrchestratorFindExisting:
    def test_not_exists(self, tmp_path: Path, app_context: AppContext, sample_anime: AnimeInfo) -> None:
        app_context.output_dir = str(tmp_path / "nonexistent")
        orch = BatchOrchestrator(app_context, sample_anime, MagicMock(), MagicMock())
        ep = EpisodeInfo(1, "s1", "T", "F", "jpn", "url")
        assert orch._find_existing(ep) is None

    def test_finds_existing_file(self, tmp_path: Path, app_context: AppContext, sample_anime: AnimeInfo) -> None:
        out = tmp_path / "downloads"
        out.mkdir()
        app_context.output_dir = str(out)
        # file named Ep 001 - Title.mp4
        f = out / "Ep 001 - Something.mp4"
        f.write_bytes(b"data")
        orch = BatchOrchestrator(app_context, sample_anime, MagicMock(), MagicMock())
        ep = EpisodeInfo(1, "s1", "T", "F", "jpn", "url")
        result = orch._find_existing(ep)
        assert result == f

    def test_ignores_empty_file(self, tmp_path: Path, app_context: AppContext, sample_anime: AnimeInfo) -> None:
        out = tmp_path / "downloads"
        out.mkdir()
        app_context.output_dir = str(out)
        f = out / "Ep 001 - Empty.mp4"
        f.write_bytes(b"")
        orch = BatchOrchestrator(app_context, sample_anime, MagicMock(), MagicMock())
        ep = EpisodeInfo(1, "s1", "T", "F", "jpn", "url")
        assert orch._find_existing(ep) is None

    def test_ignores_wrong_extension(self, tmp_path: Path, app_context: AppContext, sample_anime: AnimeInfo) -> None:
        out = tmp_path / "downloads"
        out.mkdir()
        app_context.output_dir = str(out)
        f = out / "Ep 001 - Something.mkv"
        f.write_bytes(b"data")
        orch = BatchOrchestrator(app_context, sample_anime, MagicMock(), MagicMock())
        ep = EpisodeInfo(1, "s1", "T", "F", "jpn", "url")
        assert orch._find_existing(ep) is None


class TestEpisodeDownloader:
    async def test_already_exists_skips_download(
        self, tmp_path: Path, app_context: AppContext, sample_anime: AnimeInfo,
    ) -> None:
        app_context.output_dir = str(tmp_path / "out")
        (tmp_path / "out").mkdir(parents=True)
        # Create existing file that matches ep_prefix 001
        from pahebatcher.utils import sanitize, ep_prefix

        ep = EpisodeInfo(1, "s1", "Existing Title", "F", "jpn", "url")
        prefix = ep_prefix(ep.ep_str)
        fname = sanitize(f"Ep {prefix} - Existing Title") or f"ep_{ep.ep_str}"
        out = Path(app_context.output_dir) / f"{fname}.mp4"
        out.write_bytes(b"existing")

        dash = MagicMock(spec=Dashboard)
        http = MagicMock()
        solver = MagicMock()
        dl = EpisodeDownloader(app_context, sample_anime, dash, http, solver)
        info = StreamInfo("https://cdn.m3u8", [], "UA", "ref", title="T")
        result = await dl.run(ep, info)
        assert result == out
        dash.mark_done.assert_called()

    async def test_download_flow_mocked(
        self, tmp_path: Path, app_context: AppContext, sample_anime: AnimeInfo,
    ) -> None:
        app_context.output_dir = str(tmp_path / "out")
        app_context.cache_dir = tmp_path / "cache"
        app_context.hls_workers = 2
        dash = MagicMock(spec=Dashboard)
        http = MagicMock()
        solver = MagicMock()

        # Mock m3u8 fetch
        m3u8_content = "#EXTM3U\n#EXTINF:10.0,\nseg0.ts\n#EXTINF:10.0,\nseg1.ts\n"
        with patch("pahebatcher.downloader.fetch_m3u8", new_callable=AsyncMock, return_value=m3u8_content):
            with patch("pahebatcher.downloader.resolve_m3u8", new_callable=AsyncMock) as mock_resolve:
                mock_resolve.return_value = [
                    {"url": "https://cdn/seg0.ts", "key_url": None, "iv": None},
                    {"url": "https://cdn/seg1.ts", "key_url": None, "iv": None},
                ]
                http.get = AsyncMock(return_value=b"segmentdata")
                # Mock store assemble
                with patch("pahebatcher.downloader.SegmentStore") as MockStore:
                    mock_store_inst = MagicMock()
                    mock_store_inst.done_indices.return_value = set()
                    mock_store_inst.save_metadata = MagicMock()
                    mock_store_inst.write_seg = MagicMock()
                    mock_store_inst.assemble.return_value = True
                    mock_store_inst.cleanup = MagicMock()
                    MockStore.return_value = mock_store_inst

                    ep = EpisodeInfo(1, "s1", "New Ep", "F", "jpn", "https://play/url")
                    info = StreamInfo("https://cdn.m3u8", [], "UA", "ref", title="Play Title")
                    dl = EpisodeDownloader(app_context, sample_anime, dash, http, solver)
                    result = await dl.run(ep, info)
                    assert result is not None
                    assert http.get.call_count >= 2


class TestBatchOrchestratorDownload:
    async def test_resolver_marks_existing(
        self, tmp_path: Path, app_context: AppContext, sample_anime: AnimeInfo,
    ) -> None:
        out = tmp_path / "out"
        out.mkdir()
        app_context.output_dir = str(out)
        app_context.cache_dir = tmp_path / "cache"
        # create existing file for ep 1
        f = out / "Ep 001 - Episode 1.mp4"
        f.write_bytes(b"data")

        http = MagicMock()
        http.start = AsyncMock()
        http.close = AsyncMock()
        solver = MagicMock()
        solver.request = AsyncMock()

        anime = AnimeInfo(
            session="sess", title="Test", host="h",
            episodes=[
                EpisodeInfo(1, "s1", "Episode 1", "F", "jpn", "https://play/s1"),
                EpisodeInfo(2, "s2", "Episode 2", "F", "jpn", "https://play/s2"),
            ],
            total=2,
        )
        orch = BatchOrchestrator(app_context, anime, http, solver)
        # Patch dashboard and extract_stream to avoid real network
        with patch("pahebatcher.downloader.Dashboard") as MockDash:
            mock_dash = MagicMock()
            MockDash.return_value = mock_dash
            # Mock extract_stream for ep 2
            mock_info = StreamInfo("https://cdn.m3u8", [], "UA", "ref")
            with patch("pahebatcher.downloader.extract_stream", new_callable=AsyncMock, return_value=mock_info):
                # Mock EpisodeDownloader.run to avoid http
                with patch("pahebatcher.downloader.EpisodeDownloader") as MockDL:
                    mock_dl_inst = MagicMock()
                    mock_dl_inst.run = AsyncMock(return_value=Path("/tmp/out.mp4"))
                    MockDL.return_value = mock_dl_inst
                    chosen = [anime.episodes[0], anime.episodes[1]]
                    results = await orch.download(chosen)
                    # First ep should be existing file
                    assert results["s1"] == f
                    # second should be mocked
                    assert results["s2"] == Path("/tmp/out.mp4")
