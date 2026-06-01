"""Tests for data models."""

from __future__ import annotations

from pahebatcher.models import AnimeInfo, AppContext, EpisodeInfo, StreamInfo


class TestEpisodeInfo:
    def test_ep_str_integer(self) -> None:
        ep = EpisodeInfo(1, "s1", "Title", "Fansub", "jpn", "url")
        assert ep.ep_str == "1"

    def test_ep_str_float(self) -> None:
        ep = EpisodeInfo(1.5, "s1", "Title", "Fansub", "jpn", "url")
        assert ep.ep_str == "1.5"

    def test_label_sub(self) -> None:
        ep = EpisodeInfo(1, "s1", "Episode Title", "Fansub", "jpn", "url")
        assert "Ep" in ep.label
        assert "DUB" not in ep.label

    def test_label_dub(self) -> None:
        ep = EpisodeInfo(1, "s1", "Episode Title", "Fansub", "eng", "url")
        assert "DUB" in ep.label


class TestAnimeInfo:
    def test_get_variant_found(self, sample_anime: AnimeInfo) -> None:
        ep = sample_anime.get_variant(1, "jpn")
        assert ep is not None
        assert ep.number == 1
        assert ep.audio == "jpn"

    def test_get_variant_not_found(self, sample_anime: AnimeInfo) -> None:
        ep = sample_anime.get_variant(1, "eng")
        assert ep is None

    def test_get_all_variants(self, sample_anime: AnimeInfo) -> None:
        variants = sample_anime.get_all_variants(1)
        assert len(variants) == 1


class TestStreamInfo:
    def test_headers(self) -> None:
        info = StreamInfo("url", [], "UA", "REF")
        assert info.headers == {"User-Agent": "UA", "Referer": "REF"}

    def test_cookie_str(self) -> None:
        info = StreamInfo("url", [
            {"name": "a", "value": "1"},
            {"name": "b", "value": "2"},
        ], "UA", "REF")
        assert info.cookie_str == "a=1; b=2"

    def test_cookie_str_empty(self) -> None:
        info = StreamInfo("url", [], "UA", "REF")
        assert info.cookie_str == ""


class TestAppContext:
    def test_defaults(self) -> None:
        ctx = AppContext.defaults()
        assert ctx.quality == 1080
        assert ctx.audio_lang == "jpn"
        assert ctx.max_parallel == 2
        assert ctx.hls_workers == 24
        assert ctx.keep_temp is False
