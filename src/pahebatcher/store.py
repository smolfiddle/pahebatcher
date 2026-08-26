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
        """Concatenate segments to MP4 via binary merge and timestamp regeneration."""
        missing = [i for i in range(n_segments) if not self.seg_path(i).exists()]
        if missing:
            return False

        # 1. Binary merge all .ts parts into one temporary continuous TS file
        combined_ts = self.dir / "combined_stream.ts"
        try:
            with open(combined_ts, "wb") as outfile:
                for i in range(n_segments):
                    with open(self.seg_path(i), "rb") as infile:
                        outfile.write(infile.read())
        except Exception as e:
            print(f"\n[Error] Failed to merge TS segments: {e}")
            return False

        # 2. Run FFmpeg safely via subprocess.run (no pipe deadlock)
        cmd = [
            "ffmpeg", "-y",
            "-fflags", "+genpts",                  # Regenerate unbroken timeline
            "-i", str(combined_ts.resolve()),
            "-c:v", "copy",                        # Copy video instantly without loss
            "-c:a", "aac", "-b:a", "192k",         # Transcode AAC-Main to Jellyfin-supported AAC-LC
            "-af", "aresample=async=1",            # Keep audio and video perfectly synchronized
            "-movflags", "+faststart",
            str(out.resolve())
        ]

        result = subprocess.run(cmd, capture_output=True, timeout=600)

        # 3. Clean up the temporary combined TS file
        with contextlib.suppress(Exception):
            combined_ts.unlink(missing_ok=True)

        if result.returncode != 0:
            err_output = result.stderr.decode("utf-8", errors="ignore")
            print(f"\n[Error] FFmpeg remux failed: {err_output}")
            return False

        return True

    def cleanup(self) -> None:
        with contextlib.suppress(Exception):
            import shutil
            shutil.rmtree(self.dir)