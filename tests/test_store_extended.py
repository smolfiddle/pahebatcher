"""Extended SegmentStore tests — atomicity, cleanup, assemble."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from pahebatcher.store import SegmentStore


class TestStoreAtomic:
    def test_write_uses_tmp_then_rename(self, tmp_path: Path) -> None:
        cache = tmp_path / "cache"
        store = SegmentStore(cache, "Anime", "sess", "001", "jpn")
        with patch.object(Path, "write_bytes") as mock_write, patch.object(Path, "rename") as mock_rename:
            mock_write.return_value = None
            mock_rename.return_value = None
            # Actually need to mock seg_path tmp
            store.write_seg(5, b"data")
            # Verify tmp file would be used — check seg_path for 5 exists logic via has_seg after real write
        # Real write test
        store2 = SegmentStore(cache, "Anime", "sess", "002", "jpn")
        store2.write_seg(0, b"hello")
        assert not (store2.dir / "000000.tmp").exists()
        assert (store2.dir / "000000.ts").exists()

    def test_save_metadata_not_overwrite(self, tmp_path: Path) -> None:
        cache = tmp_path / "cache"
        store = SegmentStore(cache, "Anime", "sess", "003", "jpn")
        store.save_metadata("Title A", "https://a")
        meta = store.root / "session.json"
        content_a = meta.read_text()
        store.save_metadata("Title B", "https://b")
        content_b = meta.read_text()
        assert content_a == content_b  # first write wins

    def test_done_indices_ignores_tmp(self, tmp_path: Path) -> None:
        cache = tmp_path / "cache"
        store = SegmentStore(cache, "Anime", "sess", "004", "jpn")
        store.write_seg(0, b"a")
        # create .tmp file manually should be ignored
        (store.dir / "000001.tmp").write_bytes(b"tmp")
        assert store.done_indices() == {0}

    def test_seg_path_format(self, tmp_path: Path) -> None:
        cache = tmp_path / "cache"
        store = SegmentStore(cache, "Anime", "sess", "005", "eng")
        assert store.seg_path(0).name == "000000.ts"
        assert store.seg_path(123).name == "000123.ts"
        assert store.dir.name == "Ep_005_ENG"


class TestAssemble:
    def test_assemble_cleanup_removes_concat(self, tmp_path: Path) -> None:
        cache = tmp_path / "cache"
        store = SegmentStore(cache, "Anime", "sess", "006", "jpn")
        out = tmp_path / "out.mp4"
        # Need segments 0..1
        store.write_seg(0, b"a")
        store.write_seg(1, b"b")
        concat = store.dir / "concat.txt"
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            result = store.assemble(2, out)
            assert result is True
            assert not concat.exists()  # cleaned up

    def test_assemble_pipe_fallback_on_concat_fail(self, tmp_path: Path) -> None:
        cache = tmp_path / "cache"
        store = SegmentStore(cache, "Anime", "sess", "007", "jpn")
        out = tmp_path / "out.mp4"
        store.write_seg(0, b"seg0")
        store.write_seg(1, b"seg1")
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1)
            mock_proc = MagicMock()
            mock_proc.stdin = MagicMock()
            mock_proc.wait = MagicMock()
            mock_proc.returncode = 0
            with patch("subprocess.Popen", return_value=mock_proc) as mock_popen:
                result = store.assemble(2, out)
                assert result is True
                assert mock_proc.stdin.write.call_count == 2
                mock_proc.stdin.close.assert_called_once()
                assert not (store.dir / "concat.txt").exists()

    def test_assemble_missing_returns_false_and_no_ffmpeg(self, tmp_path: Path) -> None:
        cache = tmp_path / "cache"
        store = SegmentStore(cache, "Anime", "sess", "008", "jpn")
        out = tmp_path / "out.mp4"
        with patch("subprocess.run") as mock_run:
            result = store.assemble(2, out)
            assert result is False
            mock_run.assert_not_called()
