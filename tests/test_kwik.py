"""Tests for Kwik extraction — JsPacker, resolution parsing."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pahebatcher.extract.kwik import JsPacker, _extract_m3u8, _parse_resolution_buttons


class TestJsPacker:
    def test_unpack_basic(self) -> None:
        packed = "} ('payload', 10, 10, 'payload'.split('|'))"
        unpacked = JsPacker.unpack(packed)
        assert "payload" in unpacked

    def test_unpack_no_match(self) -> None:
        result = JsPacker.unpack("plain javascript code")
        assert result == "plain javascript code"


class TestExtractM3U8:
    def test_direct_url(self) -> None:
        html = '<script>var url = "https://cdn.example.com/video.m3u8";</script>'
        result = _extract_m3u8(html)
        assert result == "https://cdn.example.com/video.m3u8"

    def test_uwu_m3u8(self) -> None:
        html = '<script>var url = "https://cdn.example.com/uwu.m3u8";</script>'
        result = _extract_m3u8(html)
        assert result == "https://cdn.example.com/uwu.m3u8"

    def test_source_tag(self) -> None:
        html = '<source src="https://cdn.example.com/video.m3u8">'
        result = _extract_m3u8(html)
        assert result == "https://cdn.example.com/video.m3u8"

    def test_no_m3u8_found(self) -> None:
        html = "<html><body>No video here</body></html>"
        result = _extract_m3u8(html)
        assert result is None


class TestParseResolutionButtons:
    def test_standard_buttons(self) -> None:
        html = """
        <div id="resolutionMenu">
            <button data-src="https://kwik.si/e/sub1080" data-resolution="1080" data-fansub="SubsPlease">1080p · SubsPlease</button>
            <button data-src="https://kwik.si/e/dub1080" data-resolution="1080" data-audio="eng" data-fansub="Yameii">1080p · Yameii</button>
            <button data-src="https://kwik.si/e/sub720" data-resolution="720" data-fansub="SubsPlease">720p · SubsPlease</button>
        </div>
        """
        entries = _parse_resolution_buttons(html)
        assert len(entries) == 3
        assert entries[0] == (1080, "https://kwik.si/e/sub1080", False, "SubsPlease")
        assert entries[1] == (1080, "https://kwik.si/e/dub1080", True, "Yameii")
        assert entries[2] == (720, "https://kwik.si/e/sub720", False, "SubsPlease")

    def test_no_menu(self) -> None:
        entries = _parse_resolution_buttons("<html>no menu</html>")
        assert entries == []

    def test_resolution_from_text(self) -> None:
        html = """
        <div id="resolutionMenu">
            <button data-src="https://kwik.si/e/1080">1080p</button>
        </div>
        """
        entries = _parse_resolution_buttons(html)
        assert len(entries) == 1
        assert entries[0][0] == 1080
