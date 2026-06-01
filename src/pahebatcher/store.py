"""Persistent HLS segment store with atomic writes and ffmpeg assembly."""

from __future__ import annotations

import contextlib
import json
import subprocess
import time
from pathlib import Path

from pahebatcher.utils import sanitize


class SegmentStore:
    def __init__(self, cache_root: Path, anime_title: str, anime_session: str, ep_num: str, audio: str = "jpn") -> None:
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
        """Concatenate segments to MP4 via ffmpeg concat demuxer."""
        missing = [i for i in range(n_segments) if not self.seg_path(i).exists()]
        if missing:
            return False

        lst = self.dir / "concat.txt"
        with open(lst, "w", encoding="utf-8") as f:
            for i in range(n_segments):
                f.write(f"file '{self.seg_path(i).as_posix()}'\n")

        result = subprocess.run(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(lst),
             "-c", "copy", "-movflags", "+faststart", str(out)],
            capture_output=True, timeout=600,
        )
        if result.returncode == 0:
            return True

        proc = subprocess.Popen(
            ["ffmpeg", "-y", "-i", "pipe:0", "-c", "copy", "-movflags", "+faststart", str(out)],
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        for i in range(n_segments):
            proc.stdin.write(self.seg_path(i).read_bytes())  # type: ignore[union-attr]
        proc.stdin.close()  # type: ignore[union-attr]
        proc.wait(timeout=600)
        return proc.returncode == 0

    def cleanup(self) -> None:
        with contextlib.suppress(Exception):
            import shutil
            shutil.rmtree(self.dir)
