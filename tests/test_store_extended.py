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

    def test_assemble_pipe_primary_on_concat_fail(self, tmp_path: Path) -> None:
        # Pipe (contiguous) is tried first; concat demuxer is the fallback.
        cache = tmp_path / "cache"
        store = SegmentStore(cache, "Anime", "sess", "007", "jpn")
        out = tmp_path / "out.mp4"
        store.write_seg(0, b"seg0")
        store.write_seg(1, b"seg1")
        with patch("subprocess.run") as mock_run:
            mock_proc = MagicMock()
            mock_proc.stdin = MagicMock()
            mock_proc.wait = MagicMock()
            mock_proc.returncode = 0
            with patch("subprocess.Popen", return_value=mock_proc):
                result = store.assemble(2, out)
                assert result is True
                assert mock_proc.stdin.write.call_count == 2
                mock_proc.stdin.close.assert_called_once()
                assert not (store.dir / "concat.txt").exists()
                mock_run.assert_not_called()  # concat fallback not needed

    def test_assemble_concat_fallback_on_pipe_fail(self, tmp_path: Path) -> None:
        cache = tmp_path / "cache"
        store = SegmentStore(cache, "Anime", "sess", "009", "jpn")
        out = tmp_path / "out.mp4"
        store.write_seg(0, b"seg0")
        store.write_seg(1, b"seg1")
        with patch("subprocess.Popen", side_effect=RuntimeError("no ffmpeg")):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=0)
                result = store.assemble(2, out)
                assert result is True
                mock_run.assert_called_once()

    def test_assemble_both_fail_returns_false(self, tmp_path: Path) -> None:
        cache = tmp_path / "cache"
        store = SegmentStore(cache, "Anime", "sess", "010", "jpn")
        out = tmp_path / "out.mp4"
        store.write_seg(0, b"seg0")
        with patch("subprocess.Popen", side_effect=RuntimeError("no ffmpeg")):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=1)
                assert store.assemble(1, out) is False

    def test_assemble_runs_aac_fix_on_success(self, tmp_path: Path) -> None:
        cache = tmp_path / "cache"
        store = SegmentStore(cache, "Anime", "sess", "011", "jpn")
        out = tmp_path / "out.mp4"
        store.write_seg(0, b"seg0")
        mock_proc = MagicMock()
        mock_proc.stdin = MagicMock()
        mock_proc.wait = MagicMock()
        mock_proc.returncode = 0
        with patch("subprocess.Popen", return_value=mock_proc):
            with patch.object(SegmentStore, "fix_aac_esds", return_value=False) as mock_fix:
                assert store.assemble(1, out) is True
                mock_fix.assert_called_once_with(out)

    def test_assemble_missing_returns_false_and_no_ffmpeg(self, tmp_path: Path) -> None:
        cache = tmp_path / "cache"
        store = SegmentStore(cache, "Anime", "sess", "008", "jpn")
        out = tmp_path / "out.mp4"
        with patch("subprocess.run") as mock_run:
            with patch("subprocess.Popen") as mock_popen:
                result = store.assemble(2, out)
                assert result is False
                mock_run.assert_not_called()
                mock_popen.assert_not_called()


def _mp4_box(typ: str, payload: bytes) -> bytes:
    return (8 + len(payload)).to_bytes(4, "big") + typ.encode("latin1") + payload


def _descr(tag: int, body: bytes) -> bytes:
    assert len(body) < 128
    return bytes([tag, len(body)]) + body


def _mp4_with_aac(asc: bytes, object_type: int = 0x40) -> bytes:
    dec_specific = _descr(0x05, asc)
    dec_config = _descr(
        0x04,
        bytes([object_type, 0x15]) + b"\x00\x00\x00" + b"\x00\x00\x00\x00" * 2 + dec_specific,
    )
    es_body = b"\x00\x02\x00" + dec_config
    esds = _mp4_box("esds", b"\x00\x00\x00\x00" + _descr(0x03, es_body))
    mp4a_entry = b"\x00" * 6 + b"\x00\x01" + b"\x00" * 2 + b"\x00" * 2 + b"\x00" * 4
    mp4a_entry += b"\x00\x02\x00\x10\x00\x00\x00\x00\xac\x44\x00\x00" + esds
    stsd = _mp4_box("stsd", b"\x00\x00\x00\x00" + (1).to_bytes(4, "big") + _mp4_box("mp4a", mp4a_entry))
    stbl = _mp4_box("stbl", stsd)
    minf = _mp4_box("minf", stbl)
    mdia = _mp4_box("mdia", minf)
    trak = _mp4_box("trak", mdia)
    moov = _mp4_box("moov", trak)
    return _mp4_box("ftyp", b"isom" + b"\x00\x00\x00\x00" + b"isom") + moov


class TestFixAacEsds:
    def test_patches_main_to_lc_single_byte(self, tmp_path: Path) -> None:
        from pahebatcher.store import SegmentStore

        out = tmp_path / "main.mp4"
        out.write_bytes(_mp4_with_aac(bytes([0x0B, 0x88])))
        assert SegmentStore.fix_aac_esds(out) is True
        data = out.read_bytes()
        assert bytes([0x13, 0x88]) in data
        assert bytes([0x0B, 0x88]) not in data
        # Idempotent: second run finds nothing to patch
        assert SegmentStore.fix_aac_esds(out) is False

    def test_leaves_lc_untouched(self, tmp_path: Path) -> None:
        from pahebatcher.store import SegmentStore

        out = tmp_path / "lc.mp4"
        raw = _mp4_with_aac(bytes([0x13, 0x88]))
        out.write_bytes(raw)
        assert SegmentStore.fix_aac_esds(out) is False
        assert out.read_bytes() == raw

    def test_leaves_non_aac_untouched(self, tmp_path: Path) -> None:
        from pahebatcher.store import SegmentStore

        out = tmp_path / "mp3.mp4"
        raw = _mp4_with_aac(bytes([0x0B, 0x88]), object_type=0x6B)
        out.write_bytes(raw)
        assert SegmentStore.fix_aac_esds(out) is False
        assert out.read_bytes() == raw

    def test_garbage_and_truncated_safe(self, tmp_path: Path) -> None:
        from pahebatcher.store import SegmentStore

        for name, raw in [
            ("garbage.mp4", b"\x00" * 64),
            ("short.mp4", b"\x00\x00\x00\x08ftyp"),
            ("trunc.mp4", _mp4_with_aac(bytes([0x0B]))[:40]),
            ("empty.mp4", b""),
        ]:
            out = tmp_path / name
            out.write_bytes(raw)
            assert SegmentStore.fix_aac_esds(out) is False
            assert out.read_bytes() == raw

    def test_missing_file_returns_false(self, tmp_path: Path) -> None:
        from pahebatcher.store import SegmentStore

        assert SegmentStore.fix_aac_esds(tmp_path / "nope.mp4") is False
