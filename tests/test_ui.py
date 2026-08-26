"""Tests for UI helpers."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from rich.table import Table

from pahebatcher.models import AnimeInfo, EpisodeInfo
from pahebatcher.ui.tables import episode_table, search_results_table, summary_table


class TestTables:
    def test_episode_table(self, sample_anime: AnimeInfo) -> None:
        t = episode_table(sample_anime, sample_anime.episodes, show_audio=True)
        assert isinstance(t, Table)
        # Should have 4 columns (check, Ep, Title, Audio) when show_audio True
        assert len(t.columns) == 4

    def test_episode_table_no_audio(self, sample_anime: AnimeInfo) -> None:
        t = episode_table(sample_anime, sample_anime.episodes, show_audio=False)
        assert len(t.columns) == 3

    def test_episode_table_dedup(self) -> None:
        anime = AnimeInfo(session="s", title="T", host="h", episodes=[
            EpisodeInfo(1, "sess-a", "T1", "F", "jpn", "u"),
            EpisodeInfo(1, "sess-b", "T1", "F", "eng", "u"),
            EpisodeInfo(2, "sess-c", "T2", "F", "jpn", "u"),
        ])
        t = episode_table(anime, anime.episodes)
        # Should dedup by number, so 2 rows
        assert t.row_count == 2

    def test_search_results_table(self) -> None:
        results = [
            {"title": "Anime A", "type": "TV", "year": "2020", "episodes": "12", "score": "8.5"},
            {"title": "Anime B", "type": "Movie", "year": "2021", "episodes": "1", "score": "7.0"},
        ]
        t = search_results_table(results, "Test")
        assert isinstance(t, Table)
        assert t.row_count == 2

    def test_summary_table(self, tmp_path: Path) -> None:
        out = tmp_path / "out.mp4"
        out.write_bytes(b"data")
        results = {"s1": out, "s2": None}
        eps = [
            EpisodeInfo(1, "s1", "T1", "F", "jpn", "u"),
            EpisodeInfo(2, "s2", "T2", "F", "jpn", "u"),
        ]
        t = summary_table(results, eps, str(tmp_path))
        assert isinstance(t, Table)
        assert t.row_count == 2
