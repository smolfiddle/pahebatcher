"""Extended tests for utils — fmt_bytes precision and edge cases."""

from __future__ import annotations

from pahebatcher.models import EpisodeInfo
from pahebatcher.utils import compact_ep_range, ep_prefix, fmt_bytes, sanitize


class TestFmtBytesPrecision:
    def test_zero(self) -> None:
        assert fmt_bytes(0) == "0.0 B"

    def test_1023(self) -> None:
        assert fmt_bytes(1023) == "1023.0 B"

    def test_1024_exact(self) -> None:
        assert fmt_bytes(1024) == "1.0 KB"

    def test_1536(self) -> None:
        # 1.5 KB
        assert fmt_bytes(1536) == "1.5 KB"

    def test_1mb(self) -> None:
        assert fmt_bytes(1048576) == "1.0 MB"

    def test_1_5mb(self) -> None:
        assert fmt_bytes(1572864) == "1.5 MB"

    def test_1gb(self) -> None:
        assert fmt_bytes(1073741824) == "1.0 GB"

    def test_large_tb(self) -> None:
        # 2 TB
        assert fmt_bytes(2 * 1024**4) == "2.0 TB"

    def test_float_precision_not_truncated(self) -> None:
        # 2048 bytes should be 2.0 KB, not truncated via int division
        assert fmt_bytes(2048) == "2.0 KB"
        # 3072 = 3.0 KB
        assert fmt_bytes(3072) == "3.0 KB"

    def test_small_bytes(self) -> None:
        assert fmt_bytes(1) == "1.0 B"


class TestSanitizeEdge:
    def test_empty_after_sanitize(self) -> None:
        # Only special chars -> becomes empty, sanitize returns ""
        assert sanitize("!!!") == ""

    def test_unicode(self) -> None:
        # \w includes unicode word chars in Python, ½ is preserved by sanitize; ':' removed
        assert sanitize("Anime: ½") == "Anime_½"

    def test_dash_and_dot_preserved(self) -> None:
        assert sanitize("file-name.test") == "file-name.test"


class TestEpPrefixEdge:
    def test_zero(self) -> None:
        assert ep_prefix("0") == "000"

    def test_large_number(self) -> None:
        assert ep_prefix("100") == "100"

    def test_float_single_decimal(self) -> None:
        assert ep_prefix("1.5") == "001.5"

    def test_none_handling(self) -> None:
        # type-ignored but should not crash
        assert ep_prefix(None) is None or isinstance(ep_prefix(None), str)  # type: ignore


class TestCompactEpRangeEdge:
    def test_single_float(self) -> None:
        eps = [EpisodeInfo(1.5, "s", "T", "F", "jpn", "u")]
        assert compact_ep_range(eps) == "1.5"

    def test_dedup(self) -> None:
        eps = [
            EpisodeInfo(1, "s1", "T", "F", "jpn", "u"),
            EpisodeInfo(1, "s2", "T", "F", "eng", "u"),
        ]
        # both number 1, should dedup to single
        assert compact_ep_range(eps) == "1"

    def test_fractional_not_merged(self) -> None:
        eps = [
            EpisodeInfo(1, "s1", "T", "F", "jpn", "u"),
            EpisodeInfo(1.5, "s2", "T", "F", "jpn", "u"),
            EpisodeInfo(2, "s3", "T", "F", "jpn", "u"),
        ]
        # 1 and 1.5 not consecutive integer +1, should not merge
        result = compact_ep_range(eps)
        assert "1" in result
        assert "1.5" in result
        assert "2" in result

    def test_long_consecutive_range(self) -> None:
        eps = [EpisodeInfo(float(i), f"s{i}", "T", "F", "jpn", "u") for i in range(1, 13)]
        result = compact_ep_range(eps)
        assert result == "1–12"
