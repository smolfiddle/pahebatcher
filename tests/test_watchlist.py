"""Tests for watchlist reset-all, download history record, and wizard browser."""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from pahebatcher.watchlist import (
    WatchlistEntry,
    WatchlistManager,
    cli_add,
    cli_reset,
    record_watchlist_download,
    run_watchlist_browser,
)


def _entry(session: str, title: str = "Title", downloaded: list[float] | None = None) -> WatchlistEntry:
    return WatchlistEntry(
        url=f"https://animepahe.pw/anime/{session}",
        session=session,
        host="animepahe.pw",
        title=title,
        quality=1080,
        audio_lang="jpn",
        output_dir="./downloads",
        max_parallel=2,
        hls_workers=24,
        keep_temp=False,
        auto_retry=2,
        added_at=time.time(),
        downloaded=list(downloaded or []),
    )


def _seed(path: Path, entries: list[WatchlistEntry]) -> None:
    WatchlistManager.save(entries, path)


class TestRecordDownload:
    def test_merges_and_sorts(self, tmp_path: Path) -> None:
        p = tmp_path / "wl.json"
        _seed(p, [_entry("s1", downloaded=[3.0, 1.0])])
        assert record_watchlist_download("s1", "Real Title", [2.0, 3.0], p) is True
        loaded = WatchlistManager.load(p)
        assert loaded[0].downloaded == [1.0, 2.0, 3.0]
        assert loaded[0].title == "Real Title"
        assert loaded[0].last_checked is not None

    def test_skips_placeholder_title(self, tmp_path: Path) -> None:
        p = tmp_path / "wl.json"
        _seed(p, [_entry("s1", title="Real Title", downloaded=[])])
        assert record_watchlist_download("s1", "Unknown Anime", [1.0], p) is True
        assert WatchlistManager.load(p)[0].title == "Real Title"

    def test_unknown_session(self, tmp_path: Path) -> None:
        p = tmp_path / "wl.json"
        _seed(p, [_entry("s1")])
        assert record_watchlist_download("nope", "Title", [1.0], p) is False
        assert WatchlistManager.load(p)[0].downloaded == []


class TestResetAll:
    def test_single(self, tmp_path: Path) -> None:
        p = tmp_path / "wl.json"
        _seed(p, [_entry("s1", downloaded=[1.0, 2.0]), _entry("s2", downloaded=[3.0])])
        cli_reset("1", path=p)
        loaded = WatchlistManager.load(p)
        assert loaded[0].downloaded == []
        assert loaded[1].downloaded == [3.0]

    def test_flag_all(self, tmp_path: Path) -> None:
        p = tmp_path / "wl.json"
        _seed(p, [_entry("s1", downloaded=[1.0]), _entry("s2", downloaded=[2.0, 3.0])])
        cli_reset(None, path=p, reset_all=True)
        assert all(e.downloaded == [] for e in WatchlistManager.load(p))

    def test_positional_all(self, tmp_path: Path) -> None:
        p = tmp_path / "wl.json"
        _seed(p, [_entry("s1", downloaded=[1.0]), _entry("s2", downloaded=[2.0])])
        cli_reset("all", path=p)
        assert all(e.downloaded == [] for e in WatchlistManager.load(p))

    def test_missing_identifier_exits_2(self, tmp_path: Path) -> None:
        p = tmp_path / "wl.json"
        _seed(p, [_entry("s1")])
        with pytest.raises(SystemExit) as exc:
            cli_reset(None, path=p)
        assert exc.value.code == 2

    def test_empty_is_noop(self, tmp_path: Path) -> None:
        p = tmp_path / "wl.json"
        cli_reset(None, path=p, reset_all=True)  # must not raise


class TestBrowser:
    def test_empty_returns_none(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("WATCHLIST_PATH", str(tmp_path / "wl.json"))
        with patch("rich.prompt.Prompt.ask", return_value=""):
            assert WatchlistManager.load() == []
            import asyncio

            assert asyncio.run(run_watchlist_browser()) == (None, None)

    def test_back_returns_none(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        p = tmp_path / "wl.json"
        _seed(p, [_entry("s1")])
        monkeypatch.setenv("WATCHLIST_PATH", str(p))
        with patch("rich.prompt.Prompt.ask", return_value="b"):
            import asyncio

            assert asyncio.run(run_watchlist_browser()) == (None, None)

    def test_open_returns_entry(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        p = tmp_path / "wl.json"
        _seed(p, [_entry("s1", title="Solo")])
        monkeypatch.setenv("WATCHLIST_PATH", str(p))
        with patch("rich.prompt.Prompt.ask", side_effect=["1", "d"]):
            import asyncio

            url, entry = asyncio.run(run_watchlist_browser())
            assert url is not None and "s1" in url
            assert entry is not None and entry.title == "Solo"

    def test_invalid_then_back(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        p = tmp_path / "wl.json"
        _seed(p, [_entry("s1")])
        monkeypatch.setenv("WATCHLIST_PATH", str(p))
        with patch("rich.prompt.Prompt.ask", side_effect=["xyz", "b"]):
            import asyncio

            assert asyncio.run(run_watchlist_browser()) == (None, None)

    def test_check_all_runs_and_returns_to_browser(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        p = tmp_path / "wl.json"
        _seed(p, [_entry("s1")])
        monkeypatch.setenv("WATCHLIST_PATH", str(p))
        with (
            patch("rich.prompt.Prompt.ask", side_effect=["c", "b"]),
            patch("pahebatcher.watchlist.run_watchlist_check", new_callable=AsyncMock) as mock_check,
        ):
            import asyncio

            assert asyncio.run(run_watchlist_browser()) == (None, None)
            mock_check.assert_called_once_with()


URL1 = "https://animepahe.pw/anime/11111111-1111-1111-1111-111111111111"


class TestAddSearch:
    def test_url_stays_offline(self, tmp_path: Path) -> None:
        p = tmp_path / "wl.json"
        with patch(
            "pahebatcher.watchlist._discover_add_url",
            side_effect=AssertionError("must not search for a URL"),
        ):
            cli_add(URL1, None, None, None, None, None, False, None, path=p)
        loaded = WatchlistManager.load(p)
        assert len(loaded) == 1
        assert loaded[0].session == "11111111-1111-1111-1111-111111111111"

    def test_no_url_searches(self, tmp_path: Path) -> None:
        p = tmp_path / "wl.json"
        with patch("pahebatcher.watchlist._discover_add_url", return_value=URL1) as mock_d:
            cli_add(None, None, None, None, None, None, False, None, path=p)
        mock_d.assert_called_once_with(None)
        assert len(WatchlistManager.load(p)) == 1

    def test_no_url_search_quit_is_graceful(self, tmp_path: Path) -> None:
        p = tmp_path / "wl.json"
        with patch("pahebatcher.watchlist._discover_add_url", return_value=None):
            cli_add(None, None, None, None, None, None, False, None, path=p)
        assert WatchlistManager.load(p) == []

    def test_query_searches(self, tmp_path: Path) -> None:
        p = tmp_path / "wl.json"
        with patch("pahebatcher.watchlist._discover_add_url", return_value=URL1) as mock_d:
            cli_add("Frieren", None, None, None, None, None, False, None, path=p)
        mock_d.assert_called_once_with("Frieren")
        assert len(WatchlistManager.load(p)) == 1

    def test_query_quit_is_graceful(self, tmp_path: Path) -> None:
        p = tmp_path / "wl.json"
        with patch("pahebatcher.watchlist._discover_add_url", return_value=None):
            cli_add("Frieren", None, None, None, None, None, False, None, path=p)
        assert WatchlistManager.load(p) == []
