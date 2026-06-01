"""Tests for episode selection and interactive prompts."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from pahebatcher.models import AnimeInfo, EpisodeInfo
from pahebatcher.ui.prompts import (
    _parse_ep_range,
    noninteractive_episodes,
)


def make_eps(*nums: float) -> list[EpisodeInfo]:
    return [
        EpisodeInfo(n, f"s{n}", f"Ep {n}", "Fansub", "jpn", f"url{n}")
        for n in nums
    ]


class TestParseEpRange:
    def test_simple_range(self) -> None:
        eps = make_eps(1, 2, 3, 4)
        assert _parse_ep_range("1-2", eps) == [1.0, 2.0]

    def test_open_ended(self) -> None:
        eps = make_eps(1, 2, 3, 4)
        assert _parse_ep_range("3-", eps) == [3.0, 4.0]

    def test_csv(self) -> None:
        eps = make_eps(1, 2, 3, 4)
        assert _parse_ep_range("1,4", eps) == [1.0, 4.0]

    def test_mixed(self) -> None:
        eps = make_eps(1, 2, 3, 4, 5, 6)
        result = _parse_ep_range("1-2,5", eps)
        assert result == [1.0, 2.0, 5.0]

    def test_float_episodes(self) -> None:
        eps = make_eps(1.0, 1.5, 2.0)
        assert _parse_ep_range("1.5", eps) == [1.5]

    def test_empty(self) -> None:
        assert _parse_ep_range("", []) == []

    def test_nonexistent(self) -> None:
        eps = make_eps(1, 2, 3)
        result = _parse_ep_range("10", eps)
        assert result == [10.0]


class TestNoninteractiveEpisodes:
    def test_all(self) -> None:
        eps = make_eps(1, 2, 3)
        anime = AnimeInfo("s", "Title", "host", episodes=eps)
        result = noninteractive_episodes(anime, "all")
        assert len(result) == 3

    def test_latest(self) -> None:
        eps = make_eps(1, 2, 3, 4, 5)
        anime = AnimeInfo("s", "Title", "host", episodes=eps)
        result = noninteractive_episodes(anime, "latest", latest_n=2)
        assert len(result) == 2
        assert result[0].number == 4
        assert result[1].number == 5

    def test_range(self) -> None:
        eps = make_eps(1, 2, 3, 4)
        anime = AnimeInfo("s", "Title", "host", episodes=eps)
        result = noninteractive_episodes(anime, "range", range_str="1-3")
        assert len(result) == 3

    def test_unknown_mode(self) -> None:
        anime = AnimeInfo("s", "Title", "host", episodes=[])
        result = noninteractive_episodes(anime, "unknown")
        assert result == []
