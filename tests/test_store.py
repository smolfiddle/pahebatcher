"""Tests for SegmentStore."""

from __future__ import annotations

from pathlib import Path

from pahebatcher.store import SegmentStore


class TestSegmentStore:
    def test_init_creates_dirs(self, tmp_path: Path) -> None:
        cache = tmp_path / "cache"
        store = SegmentStore(cache, "Test Anime", "session-1", "001", "jpn")
        assert store.dir.exists()
        assert store.dir.name == "Ep_001_JPN"

    def test_save_metadata(self, tmp_path: Path) -> None:
        cache = tmp_path / "cache"
        store = SegmentStore(cache, "Test Anime", "session-1", "001", "jpn")
        store.save_metadata("Test Anime", "https://example.com")
        meta = store.root / "session.json"
        assert meta.exists()
        import json
        data = json.loads(meta.read_text())
        assert data["title"] == "Test Anime"
        assert data["url"] == "https://example.com"

    def test_write_and_read_segment(self, tmp_path: Path) -> None:
        cache = tmp_path / "cache"
        store = SegmentStore(cache, "Test Anime", "session-1", "001", "jpn")
        data = b"test segment data"
        store.write_seg(0, data)
        assert store.has_seg(0)
        assert store.seg_path(0).read_bytes() == data

    def test_done_indices(self, tmp_path: Path) -> None:
        cache = tmp_path / "cache"
        store = SegmentStore(cache, "Test Anime", "session-1", "001", "jpn")
        store.write_seg(0, b"data")
        store.write_seg(3, b"data")
        store.write_seg(7, b"data")
        indices = store.done_indices()
        assert indices == {0, 3, 7}

    def test_has_seg_false(self, tmp_path: Path) -> None:
        cache = tmp_path / "cache"
        store = SegmentStore(cache, "Test Anime", "session-1", "001", "jpn")
        assert not store.has_seg(99)

    def test_cleanup(self, tmp_path: Path) -> None:
        cache = tmp_path / "cache"
        store = SegmentStore(cache, "Test Anime", "session-1", "001", "jpn")
        store.write_seg(0, b"data")
        assert store.dir.exists()
        store.cleanup()
        assert not store.dir.exists()

    def test_assemble_missing_segments(self, tmp_path: Path) -> None:
        cache = tmp_path / "cache"
        store = SegmentStore(cache, "Test Anime", "session-1", "001")
        out = tmp_path / "out.mp4"
        result = store.assemble(5, out)
        assert result is False
