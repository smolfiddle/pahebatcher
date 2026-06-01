"""Tests for M3U8 parser."""

from __future__ import annotations

from unittest.mock import patch

from pahebatcher.extract.m3u8 import parse_m3u8


class TestParseM3U8:
    def test_simple_playlist(self) -> None:
        content = """#EXTM3U
#EXT-X-TARGETDURATION:10
#EXT-X-MEDIA-SEQUENCE:0
#EXTINF:10.0,
segment1.ts
#EXTINF:10.0,
segment2.ts
"""
        segments = parse_m3u8(content, "https://base.url/")
        assert len(segments) == 2
        assert segments[0]["url"].endswith("segment1.ts")
        assert segments[1]["url"].endswith("segment2.ts")

    def test_aes_key_extraction(self) -> None:
        content = """#EXTM3U
#EXT-X-KEY:METHOD=AES-128,URI="https://key.url",IV=0x1234567890ABCDEF1234567890ABCDEF
#EXTINF:10.0,
segment1.ts
"""
        segments = parse_m3u8(content, "https://base.url/")
        assert len(segments) == 1
        assert segments[0]["key_url"] == "https://key.url"
        assert segments[0]["iv"] == bytes.fromhex("1234567890ABCDEF1234567890ABCDEF")

    def test_aes_without_iv(self) -> None:
        content = """#EXTM3U
#EXT-X-MEDIA-SEQUENCE:0
#EXT-X-KEY:METHOD=AES-128,URI="https://key.url"
#EXTINF:10.0,
segment1.ts
"""
        segments = parse_m3u8(content, "https://base.url/")
        assert len(segments) == 1
        assert segments[0]["key_url"] == "https://key.url"
        assert segments[0]["iv"] == (0).to_bytes(16, "big")

    def test_no_encryption(self) -> None:
        content = """#EXTM3U
#EXTINF:10.0,
segment1.ts
"""
        segments = parse_m3u8(content, "https://base.url/")
        assert len(segments) == 1
        assert segments[0]["key_url"] is None
        assert segments[0]["iv"] is None

    def test_variant_playlist_detection(self) -> None:
        content = """#EXTM3U
#EXT-X-STREAM-INF:BANDWIDTH=1280000
low.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=2560000
high.m3u8
"""
        segments = parse_m3u8(content, "https://base.url/")
        assert segments == []

    def test_media_sequence_offset(self) -> None:
        content = """#EXTM3U
#EXT-X-MEDIA-SEQUENCE:5
#EXTINF:10.0,
segment5.ts
"""
        segments = parse_m3u8(content, "https://base.url/")
        assert len(segments) == 1

    def test_url_joining(self) -> None:
        content = """#EXTM3U
#EXTINF:10.0,
path/to/segment.ts
"""
        segments = parse_m3u8(content, "https://base.url/subdir/")
        assert segments[0]["url"] == "https://base.url/subdir/path/to/segment.ts"

    def test_empty_playlist(self) -> None:
        segments = parse_m3u8("#EXTM3U\n", "https://base.url/")
        assert segments == []
