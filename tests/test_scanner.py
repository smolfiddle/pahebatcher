"""Tests for AnimePahe scanner and URL parsing."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pahebatcher.extract.scanner import AnimePaheScanner, parse_anime_url
from pahebatcher.models import AnimeInfo, EpisodeInfo


class TestParseAnimeUrl:
    def test_valid_url(self) -> None:
        host, uuid = parse_anime_url(
            "https://animepahe.pw/anime/540562da-0708-2e0f-2178-01306c59b207"
        )
        assert host == "animepahe.pw"
        assert uuid == "540562da-0708-2e0f-2178-01306c59b207"

    def test_different_domain(self) -> None:
        host, uuid = parse_anime_url(
            "https://animepahe.com/anime/540562da-0708-2e0f-2178-01306c59b207"
        )
        assert host == "animepahe.com"

    def test_invalid_domain(self) -> None:
        with pytest.raises(ValueError, match="Not an AnimePahe URL"):
            parse_anime_url("https://google.com/test")

    def test_invalid_scheme(self) -> None:
        with pytest.raises(ValueError, match="Unsupported scheme"):
            parse_anime_url("ftp://animepahe.com/test")

    def test_no_uuid(self) -> None:
        with pytest.raises(ValueError, match="No anime UUID"):
            parse_anime_url("https://animepahe.com/anime/")

    def test_url_with_trailing_slash(self) -> None:
        host, uuid = parse_anime_url(
            "https://animepahe.pw/anime/540562da-0708-2e0f-2178-01306c59b207/"
        )
        assert host == "animepahe.pw"
        assert uuid == "540562da-0708-2e0f-2178-01306c59b207"


class TestAnimePaheScanner:
    def test_parse_episode_page(self) -> None:
        data = {
            "data": [
                {
                    "episode": 1,
                    "session": "ep-sess-1",
                    "title": "Test Episode",
                    "fansub": "SubsPlease",
                    "audio": "jpn",
                },
                {
                    "episode": 2,
                    "session": "ep-sess-2",
                    "title": "Test Episode DUB",
                    "fansub": "Yameii",
                    "audio": "eng",
                },
            ]
        }
        eps = AnimePaheScanner._parse_episode_page(data, "animepahe.com", "anime-session")
        assert len(eps) == 2
        assert eps[0].number == 1.0
        assert eps[0].audio == "jpn"
        assert eps[0].play_url == "https://animepahe.com/play/anime-session/ep-sess-1"
        assert eps[1].audio == "eng"

    def test_parse_episode_page_empty(self) -> None:
        eps = AnimePaheScanner._parse_episode_page({"data": []}, "host", "session")
        assert eps == []

    def test_parse_episode_page_question_mark_title(self) -> None:
        data = {"data": [{"episode": 1, "session": "s1", "title": "?", "fansub": "", "audio": "jpn"}]}
        eps = AnimePaheScanner._parse_episode_page(data, "host", "session")
        assert eps[0].title == ""

    def test_parse_episode_page_dub_detection(self) -> None:
        data = {"data": [{"episode": 1, "session": "s1", "title": "DUB Episode", "fansub": "", "audio": "jpn"}]}
        eps = AnimePaheScanner._parse_episode_page(data, "host", "session")
        assert eps[0].audio == "eng"

    def test_parse_episode_page_skip_empty_session(self) -> None:
        data = {"data": [{"episode": 1, "session": "", "title": "T", "fansub": "", "audio": "jpn"}]}
        eps = AnimePaheScanner._parse_episode_page(data, "host", "session")
        assert eps == []

    async def test_search(self, mock_solver: MagicMock) -> None:
        mock_solver.fetch_json = AsyncMock(return_value={
            "data": [{"title": "Test", "session": "s1"}],
        })
        results = await AnimePaheScanner.search(mock_solver, "animepahe.com", "Test")
        assert len(results) == 1
        assert results[0]["title"] == "Test"
