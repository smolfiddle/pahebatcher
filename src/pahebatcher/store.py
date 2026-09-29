"""Persistent HLS segment store with atomic writes and ffmpeg assembly."""

from __future__ import annotations

import contextlib
import json
import subprocess
import time
from pathlib import Path

from pahebatcher.utils import sanitize


def _read_box(data: bytearray, off: int, end: int) -> tuple[str, int, int] | None:
    """Read an MP4 box header at off. Returns (type, payload_off, box_end) or None."""
    if off + 8 > end:
        return None
    size = int.from_bytes(data[off:off + 4], "big")
    typ = bytes(data[off + 4:off + 8]).decode("latin1")
    if size == 1:
        if off + 16 > end:
            return None
        size = int.from_bytes(data[off + 8:off + 16], "big")
        payload_off = off + 16
    elif size == 0:
        size = end - off
        payload_off = off + 8
    else:
        payload_off = off + 8
    if size < 8 or off + size > end:
        return None
    return typ, payload_off, off + size


def _read_descr_len(data: bytearray, off: int, end: int) -> tuple[int, int] | None:
    """Read an MPEG-4 descriptor tag+length. Returns (payload_off, payload_len) or None."""
    if off + 1 > end:
        return None
    pos = off + 1  # skip tag
    length = 0
    for _ in range(4):
        if pos >= end:
            return None
        byte = data[pos]
        pos += 1
        length = (length << 7) | (byte & 0x7F)
        if not byte & 0x80:
            break
    else:
        return None
    if pos + length > end:
        return None
    return pos, length


def _patch_aac_main_to_lc(data: bytearray) -> bool:
    """Find audio esds boxes declaring AAC Main (AOT 1) and relabel to AAC-LC (AOT 2).

    Only patches when the full descriptor chain validates
    (esds -> ES_Descriptor 0x03 -> DecoderConfig 0x04 with objectType 0x40
    -> DecSpecificInfo 0x05 with >= 2 bytes). Returns True if patched.
    """
    patched_any = False
    # Walk the box tree to find esds boxes. Containers have known child offsets:
    # moov/trak/mdia/minf/stbl start children immediately; stsd skips 8 bytes
    # (version/flags + entry count); mp4a sample entries have fixed fields
    # before child boxes, so esds is located by scanning.
    stack: list[tuple[int, int]] = [(0, len(data))]
    while stack:
        start, end = stack.pop()
        off = start
        while off < end:
            box = _read_box(data, off, end)
            if box is None:
                break
            typ, payload_off, box_end = box
            if typ == "esds":
                if _patch_esds_payload(data, payload_off, box_end):
                    patched_any = True
            elif typ in ("moov", "trak", "mdia", "minf", "stbl"):
                stack.append((payload_off, box_end))
            elif typ == "stsd":
                if payload_off + 8 <= box_end:
                    stack.append((payload_off + 8, box_end))
            elif typ == "mp4a":
                # Sample entry fixed fields precede child boxes; scan for esds.
                # Strict descriptor validation inside _patch_esds_payload
                # rules out false positives.
                pos = payload_off
                while True:
                    idx = data.find(b"esds", pos, box_end)
                    if idx < 0 or idx - 4 < payload_off:
                        break
                    size = int.from_bytes(data[idx - 4:idx], "big")
                    if (
                        size >= 8
                        and idx - 4 + size <= box_end
                        and _patch_esds_payload(data, idx + 4, idx - 4 + size)
                    ):
                        patched_any = True
                    pos = idx + 4
            off = box_end
    return patched_any


def _patch_esds_payload(data: bytearray, off: int, end: int) -> bool:
    """Patch a single esds payload. Returns True if patched."""
    if off + 4 > end:  # version + flags
        return False
    pos = off + 4
    # ES_Descriptor tag 0x03
    if pos >= end or data[pos] != 0x03:
        return False
    parsed = _read_descr_len(data, pos, end)
    if parsed is None:
        return False
    es_off, es_len = parsed
    es_end = es_off + es_len
    if es_off + 3 > es_end:  # ES_ID(2) + flags(1)
        return False
    pos = es_off + 3
    # DecoderConfigDescriptor tag 0x04
    if pos >= es_end or data[pos] != 0x04:
        return False
    parsed = _read_descr_len(data, pos, es_end)
    if parsed is None:
        return False
    dc_off, dc_len = parsed
    dc_end = dc_off + dc_len
    # objectTypeIndication(1) + streamType(1) + bufferSizeDB(3) + maxBitrate(4) + avgBitrate(4)
    if dc_off + 13 > dc_end:
        return False
    if data[dc_off] != 0x40:  # 0x40 = MPEG-4 AAC; leave other codecs alone
        return False
    pos = dc_off + 13
    # DecSpecificInfo tag 0x05
    if pos >= dc_end or data[pos] != 0x05:
        return False
    parsed = _read_descr_len(data, pos, dc_end)
    if parsed is None:
        return False
    asc_off, asc_len = parsed
    if asc_len < 2 or asc_off + 2 > dc_end:
        return False
    # AudioObjectType = top 5 bits of first ASC byte; 1 = Main -> 2 = LC
    if data[asc_off] >> 3 != 1:
        return False
    data[asc_off] = (data[asc_off] + 0x08) & 0xFF
    return True


class SegmentStore:
    def __init__(
        self, cache_root: Path, anime_title: str, anime_session: str, ep_num: str, audio: str = "jpn",
    ) -> None:
        safe_title = sanitize(anime_title)
        self.root = cache_root / f"{safe_title}_{anime_session}"
        self.dir = self.root / f"Ep_{ep_num}_{audio.upper()}"
        self.dir.mkdir(parents=True, exist_ok=True)

    def save_metadata(self, anime_title: str, url: str) -> None:
        meta = self.root / "session.json"
        if not meta.exists():
            meta.write_text(
                json.dumps({"title": anime_title, "url": url, "updated": time.time()}, indent=2),
                encoding="utf-8",
            )

    def seg_path(self, idx: int) -> Path:
        return self.dir / f"{idx:06d}.ts"

    def has_seg(self, idx: int) -> bool:
        return self.seg_path(idx).exists()

    def done_indices(self) -> set[int]:
        return {int(p.stem) for p in self.dir.glob("??????.ts")}

    def write_seg(self, idx: int, data: bytes) -> None:
        tmp = self.seg_path(idx).with_suffix(".tmp")
        tmp.write_bytes(data)
        tmp.rename(self.seg_path(idx))

    def assemble(self, n_segments: int, out: Path) -> bool:
        """Assemble segments to MP4 via contiguous pipe (primary) or concat demuxer (fallback)."""
        missing = [i for i in range(n_segments) if not self.seg_path(i).exists()]
        if missing:
            return False

        lst = self.dir / "concat.txt"
        try:
            # Primary: feed segments as one contiguous MPEG-TS stream.
            # The concat demuxer derives boundaries from each file's reported
            # duration, which can stretch the timeline with timestamp gaps;
            # a contiguous input avoids that. Still -c copy (lossless).
            if self._assemble_pipe(n_segments, out):
                self.fix_aac_esds(out)
                return True
            # Fallback: concat demuxer (handles inputs the pipe path rejects).
            if self._assemble_concat(n_segments, out, lst):
                self.fix_aac_esds(out)
                return True
            return False
        finally:
            with contextlib.suppress(Exception):
                lst.unlink()

    def _assemble_pipe(self, n_segments: int, out: Path) -> bool:
        """Feed all segments as one contiguous MPEG-TS stream via stdin."""
        try:
            proc = subprocess.Popen(
                ["ffmpeg", "-y", "-i", "pipe:0", "-c", "copy", "-movflags", "+faststart", str(out)],
                stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            assert proc.stdin is not None
            for i in range(n_segments):
                try:
                    proc.stdin.write(self.seg_path(i).read_bytes())
                except BrokenPipeError:
                    break
            proc.stdin.close()
            proc.wait(timeout=600)
            return proc.returncode == 0
        except Exception:
            return False

    def _assemble_concat(self, n_segments: int, out: Path, lst: Path) -> bool:
        """Concatenate segments to MP4 via ffmpeg concat demuxer."""
        with open(lst, "w", encoding="utf-8") as f:
            for i in range(n_segments):
                f.write(f"file '{self.seg_path(i).as_posix()}'\n")

        result = subprocess.run(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(lst),
             "-c", "copy", "-movflags", "+faststart", str(out)],
            capture_output=True, timeout=600,
        )
        return result.returncode == 0

    @staticmethod
    def fix_aac_esds(out: Path) -> bool:
        """Correct mislabeled AAC Main esds to AAC-LC (metadata only, no re-encode).

        Some sources declare AAC Main (AOT 1) while the payload is HE-AAC;
        strict decoders (Android MediaCodec) reject Main, while VLC tolerates
        it. Flipping AOT 1 -> 2 preserves all other bits. Best-effort:
        returns False (and leaves the file untouched) on any mismatch.
        """
        try:
            data = bytearray(out.read_bytes())
        except Exception:
            return False
        try:
            patched = _patch_aac_main_to_lc(data)
        except Exception:
            return False
        if not patched:
            return False
        try:
            out.write_bytes(bytes(data))
        except Exception:
            return False
        return True

    def cleanup(self) -> None:
        with contextlib.suppress(Exception):
            import shutil
            shutil.rmtree(self.dir)
