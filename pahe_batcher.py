#!/usr/bin/env python3
"""
pahe-batcher v2.0.0 — AnimePahe Batch Downloader
=================================================
Blazing-fast HLS engine · Shared aiohttp pool · Prefetch pipeline · Rich TUI

Usage (interactive wizard):
    python pahe_batcher.py https://animepahe.ru/anime/<uuid>

Usage (non-interactive / scripted):
    python pahe_batcher.py https://animepahe.ru/anime/<uuid> --all
    python pahe_batcher.py https://animepahe.ru/anime/<uuid> --range 1-12
    python pahe_batcher.py https://animepahe.ru/anime/<uuid> --latest 5
    python pahe_batcher.py https://animepahe.ru/anime/<uuid> --list
    python pahe_batcher.py https://animepahe.ru/anime/<uuid> --export
    python pahe_batcher.py https://animepahe.ru/anime/<uuid> --stream

Requirements
------------
  pip install rich aiohttp
  ffmpeg in PATH               (HLS → MP4 remux; falls back to .ts)
  FlareSolverr running         (FLARESOLVERR_URL env var, default http://localhost:8191/v1)

Optional
--------
  pip install pycryptodomex    (AES-128 encrypted HLS streams)

What's new in v2.0.0
--------------------
  Speed:
    • SegmentStore replaces SQLite chunk engine — no per-segment hashing,
      no entropy compression, no DB transaction overhead. Direct file I/O.
    • Shared aiohttp session across all concurrent downloads (connection
      reuse, DNS cache, keep-alive).
    • Prefetch pipeline: FlareSolverr resolves episode N+1 while N downloads.
    • Tuned TCP connector: keepalive, DNS TTL, per-host limit.
    • ffmpeg concat demuxer (faster than piping raw TS).
  UX:
    • Pre-download summary panel with episode list & size estimate.
    • FlareSolverr health check at startup (clear error if not running).
    • Cleaner 3-step wizard; re-uses settings between actions.
    • Richer completion table with per-episode file size.
    • Better error messages with actionable hints.
"""

from __future__ import annotations

# ── Standard library ──────────────────────────────────────────────────────
import argparse
import asyncio
import atexit
import contextlib
import json
import logging
import os
import re
import shutil
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterator, List, Literal, Optional, Set, Tuple

# ── AES (optional) ────────────────────────────────────────────────────────
try:
    from Cryptodome.Cipher import AES
    HAS_AES = True
except ImportError:
    try:
        from Crypto.Cipher import AES  # type: ignore[no-redef]
        HAS_AES = True
    except ImportError:
        HAS_AES = False

# ── Rich TUI ──────────────────────────────────────────────────────────────
try:
    from rich import box
    from rich.align import Align
    from rich.columns import Columns
    from rich.console import Console, Group
    from rich.live import Live
    from rich.panel import Panel
    from rich.progress import (
        BarColumn,
        DownloadColumn,
        MofNCompleteColumn,
        Progress,
        SpinnerColumn,
        TaskID,
        TextColumn,
        TimeRemainingColumn,
        TransferSpeedColumn,
    )
    from rich.prompt import Confirm, IntPrompt, Prompt
    from rich.rule import Rule
    from rich.table import Table
    from rich.text import Text
except ImportError:
    print("Install dependencies:  pip install rich aiohttp")
    sys.exit(1)

# ── aiohttp ───────────────────────────────────────────────────────────────
try:
    import aiohttp
    HAS_AIOHTTP = True
except ImportError:
    HAS_AIOHTTP = False

console = Console()

# ─────────────────────────────────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────────────────────────────────

VERSION          = "2.0.0"
HLS_WORKERS      = 24          # parallel segment fetches per episode
RETRY_ATTEMPTS   = 5
RETRY_BASE_DELAY = 0.5
FLARESOLVERR_URL = os.getenv("FLARESOLVERR_URL", "http://localhost:8191/v1")
REQUEST_DELAY    = 0.4         # between API page fetches
CACHE_DIR        = Path(tempfile.gettempdir()) / "pahe_batcher_v2"

# Rough bytes-per-segment hint before real sizes land (188 bytes × 512 packets)
_SEG_HINT_BYTES = 188 * 512

log = logging.getLogger("pahe_batcher")
logging.basicConfig(level=logging.WARNING, format="%(asctime)s [%(levelname)s] %(message)s")


# ═════════════════════════════════════════════════════════════════════════
# 1.  DATA MODELS
# ═════════════════════════════════════════════════════════════════════════

@dataclass
class EpisodeInfo:
    number:      float
    session:     str
    title:       str
    fansub:      str
    audio:       str
    play_url:    str

    @property
    def ep_str(self) -> str:
        return str(int(self.number)) if self.number == int(self.number) else str(self.number)

    @property
    def label(self) -> str:
        num = int(self.number) if self.number == int(self.number) else self.number
        dub = "  [yellow]DUB[/yellow]" if self.audio and self.audio != "jpn" else ""
        return f"Ep [cyan]{num:>4}[/cyan]  {self.title or '—'}{dub}"


@dataclass
class AnimeInfo:
    session:  str
    title:    str
    host:     str
    total:    int = 0
    episodes: List[EpisodeInfo] = field(default_factory=list)


@dataclass
class StreamInfo:
    """Resolved stream information from Kwik."""
    url:        str
    cookies:    List[dict]
    user_agent: str
    referer:    str
    title:      str = ""
    audio:      str = "jpn"
    fansub:     str = ""

    @property
    def headers(self) -> Dict[str, str]:
        return {"User-Agent": self.user_agent, "Referer": self.referer}

    @property
    def cookie_str(self) -> str:
        return "; ".join(f"{c['name']}={c['value']}" for c in self.cookies)


@dataclass
class DownloadConfig:
    output_dir:   str  = "./downloads"
    max_parallel: int  = 2
    hls_workers:  int  = HLS_WORKERS
    quality:      int  = 1080
    export_mode:  bool = False
    stream_mode:  bool = False
    audio_lang:   str  = "jpn"
    keep_temp:    bool = False   # keep raw segment files (useful for debugging)


# ═════════════════════════════════════════════════════════════════════════
# 2.  SHARED UTILITIES
# ═════════════════════════════════════════════════════════════════════════

def sanitize(name: str) -> str:
    """Strip unsafe filename characters and collapse underscores."""
    safe = re.sub(r"[^\w\s\-.]", "", name).strip().replace(" ", "_")
    return re.sub(r"_+", "_", safe)


def ep_prefix(ep_num: str) -> str:
    """Zero-pad an episode number for sortable filenames: '5' → '005', '5.5' → '005.5'."""
    try:
        return f"{float(ep_num):05.1f}" if "." in ep_num else f"{int(ep_num):03d}"
    except (ValueError, TypeError):
        return ep_num


def audio_badge(audio: str) -> str:
    return {"eng": "[yellow]DUB[/yellow]", "jpn": "[dim]JPN[/dim]"}.get(audio, f"[cyan]{audio.upper()}[/cyan]")


def fmt_bytes(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n //= 1024
    return f"{n:.1f} TB"


def compact_ep_range(episodes: List[EpisodeInfo]) -> str:
    """Summarise episode list as '1–12, 14, 16–24'."""
    if not episodes:
        return "none"
    nums = sorted(ep.number for ep in episodes)
    if len(nums) == 1:
        n = nums[0]; return str(int(n) if n == int(n) else n)
    ranges: List[Tuple[float, float]] = []
    s = p = nums[0]
    for n in nums[1:]:
        if n == p + 1: p = n
        else: ranges.append((s, p)); s = p = n
    ranges.append((s, p))
    parts = []
    for a, b in ranges:
        ai = int(a) if a == int(a) else a
        bi = int(b) if b == int(b) else b
        parts.append(str(ai) if a == b else f"{ai}–{bi}")
    return ", ".join(parts)


# ═════════════════════════════════════════════════════════════════════════
# 3.  HARDENED TLS
# ═════════════════════════════════════════════════════════════════════════

def make_ssl_ctx() -> ssl.SSLContext:
    ctx = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.options |= ssl.OP_NO_COMPRESSION
    ctx.set_ciphers(
        "ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:"
        "ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384:"
        "ECDHE-ECDSA-CHACHA20-POLY1305:ECDHE-RSA-CHACHA20-POLY1305:"
        "DHE-RSA-AES128-GCM-SHA256:DHE-RSA-AES256-GCM-SHA384"
    )
    ctx.verify_mode = ssl.CERT_REQUIRED
    ctx.check_hostname = True
    return ctx


# ═════════════════════════════════════════════════════════════════════════
# 4.  SEGMENT STORE  — direct temp-dir, no SQLite overhead
# ═════════════════════════════════════════════════════════════════════════

class SegmentStore:
    """
    Stores HLS segments as individual numbered files in a temp directory.

    v2.0 design rationale vs. the old SQLite chunk store
    ─────────────────────────────────────────────────────
    Old path per segment: blake2b hash → entropy sample → maybe zlib → BEGIN TX
                          → INSERT chunks → INSERT asset_chunks → COMMIT
    New path per segment: open(path, "wb").write(data)   ← one syscall

    TS segments are already compressed media; the entropy check always
    returned "high entropy" and skipped compression anyway.  The blake2b
    hash and DB round-trip were pure overhead.

    Resume works by checking which numbered .ts files already exist.
    """

    def __init__(self, anime_session: str, ep_session: str) -> None:
        self.dir = CACHE_DIR / anime_session / ep_session
        self.dir.mkdir(parents=True, exist_ok=True)

    # ── Segment I/O ───────────────────────────────────────────────────────

    def seg_path(self, idx: int) -> Path:
        return self.dir / f"{idx:06d}.ts"

    def has_seg(self, idx: int) -> bool:
        return self.seg_path(idx).exists()

    def done_indices(self) -> Set[int]:
        return {int(p.stem) for p in self.dir.glob("??????.ts")}

    def write_seg(self, idx: int, data: bytes) -> None:
        """Atomic write via rename to avoid partial files on crash."""
        tmp = self.seg_path(idx).with_suffix(".tmp")
        tmp.write_bytes(data)
        tmp.rename(self.seg_path(idx))

    # ── Assembly ──────────────────────────────────────────────────────────

    def assemble(self, n_segments: int, out: Path) -> bool:
        """
        Concatenate segments → mux to MP4 via ffmpeg concat demuxer.
        Falls back to raw .ts if ffmpeg is unavailable or fails.
        Returns True on success.
        """
        # Validate completeness
        missing = [i for i in range(n_segments) if not self.seg_path(i).exists()]
        if missing:
            log.error("Missing segments %s … (total %d)", missing[:5], len(missing))
            return False

        # Build ffmpeg concat list
        lst = self.dir / "concat.txt"
        with open(lst, "w", encoding="utf-8") as f:
            for i in range(n_segments):
                # ffmpeg requires forward slashes even on Windows
                f.write(f"file '{self.seg_path(i).as_posix()}'\n")

        # Try ffmpeg concat demuxer (single-pass, fastest)
        if shutil.which("ffmpeg"):
            try:
                res = subprocess.run(
                    ["ffmpeg", "-y",
                     "-f", "concat", "-safe", "0", "-i", str(lst),
                     "-c", "copy", "-movflags", "+faststart", str(out)],
                    capture_output=True, timeout=600,
                )
                if res.returncode == 0:
                    return True
                # Attempt pipe fallback
                stderr = res.stderr.decode(errors="replace").strip()
                log.warning("ffmpeg concat failed (%s) — trying TS pipe", stderr[-120:])

                # Pipe: cat all segments → stdin → ffmpeg → MP4
                proc = subprocess.Popen(
                    ["ffmpeg", "-y", "-i", "pipe:0", "-c", "copy",
                     "-movflags", "+faststart", str(out)],
                    stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                for i in range(n_segments):
                    proc.stdin.write(self.seg_path(i).read_bytes())  # type: ignore[union-attr]
                proc.stdin.close()  # type: ignore[union-attr]
                proc.wait(timeout=600)
                if proc.returncode == 0:
                    return True

            except FileNotFoundError:
                console.print("  [yellow]⚠ ffmpeg not found — saving as .ts[/yellow]")
            except subprocess.TimeoutExpired:
                console.print("  [yellow]⚠ ffmpeg timed out — saving as .ts[/yellow]")
            except Exception as exc:
                console.print(f"  [yellow]⚠ ffmpeg error ({exc}) — saving as .ts[/yellow]")
        else:
            console.print("  [yellow]⚠ ffmpeg not in PATH — saving as .ts[/yellow]")

        # Final fallback: raw .ts concatenation
        out_ts = out.with_suffix(".ts")
        with open(out_ts, "wb") as fh:
            for i in range(n_segments):
                fh.write(self.seg_path(i).read_bytes())
        return True

    def cleanup(self) -> None:
        with contextlib.suppress(Exception):
            shutil.rmtree(self.dir)


# ═════════════════════════════════════════════════════════════════════════
# 5.  TTL CACHE  (simplified, replaces OrderedDict LRU)
# ═════════════════════════════════════════════════════════════════════════

class TTLCache:
    def __init__(self, ttl: float = 120.0, max_size: int = 512) -> None:
        self._d: Dict[str, Tuple[float, Any]] = {}
        self._lock = threading.Lock()
        self._ttl  = ttl
        self._max  = max_size

    def get(self, key: str) -> Any:
        with self._lock:
            if e := self._d.get(key):
                if time.monotonic() - e[0] < self._ttl:
                    return e[1]
                del self._d[key]
        return None

    def set(self, key: str, val: Any) -> None:
        with self._lock:
            if len(self._d) >= self._max:
                del self._d[min(self._d, key=lambda k: self._d[k][0])]
            self._d[key] = (time.monotonic(), val)

    def clear(self) -> None:
        with self._lock:
            self._d.clear()


_solver_cache  = TTLCache(ttl=120.0, max_size=256)
_aes_key_cache = TTLCache(ttl=3600.0, max_size=128)


# ═════════════════════════════════════════════════════════════════════════
# 6.  FLARESOLVERR CLIENT
# ═════════════════════════════════════════════════════════════════════════

class Solver:
    """
    FlareSolverr wrapper with session reuse, retry, and TTL cache.
    Only one browser action runs at a time (_sem), but the cache means
    repeated episode-page fetches (common in stream mode) are free.
    """

    _session_id: Optional[str] = None
    _lock = threading.Lock()
    _sem  = threading.Semaphore(1)  # one Chromium action at a time

    # ── Low-level POST ────────────────────────────────────────────────────

    @classmethod
    def _post(cls, body: Dict, timeout: int = 90) -> Optional[Dict]:
        req = urllib.request.Request(
            FLARESOLVERR_URL,
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.load(r)
        except Exception as exc:
            log.debug("FlareSolverr POST: %s", exc)
            return None

    # ── Session lifecycle ─────────────────────────────────────────────────

    @classmethod
    def ping(cls) -> bool:
        """Return True if FlareSolverr is reachable (used for startup check)."""
        try:
            base = FLARESOLVERR_URL
            if base.endswith("/v1"):
                base = base[:-3]
            req = urllib.request.Request(base + "/health", method="GET")
            with urllib.request.urlopen(req, timeout=5) as r:
                return json.load(r).get("status") == "ok"
        except Exception:
            return False

    @classmethod
    def _ensure_session(cls) -> None:
        with cls._lock:
            if cls._session_id:
                return
        data = cls._post({"cmd": "sessions.create"}, timeout=15)
        if data and data.get("status") == "ok":
            sid = data.get("session")
            with cls._lock:
                cls._session_id = sid
            log.info("FlareSolverr session created: %s", sid)

    @classmethod
    def destroy_session(cls) -> None:
        with cls._lock:
            sid, cls._session_id = cls._session_id, None
        if sid:
            cls._post({"cmd": "sessions.destroy", "session": sid}, timeout=10)
            log.info("FlareSolverr session %s destroyed.", sid)

    # ── Public request ────────────────────────────────────────────────────

    @classmethod
    def request(cls, url: str, cache: bool = True) -> Optional[Dict]:
        if cache and (hit := _solver_cache.get(url)) is not None:
            return hit

        with cls._sem:
            cls._ensure_session()
            for attempt in range(RETRY_ATTEMPTS):
                body: Dict[str, Any] = {
                    "cmd": "request.get",
                    "url": url,
                    "maxTimeout": 60000,
                    "wait": 2000,
                }
                with cls._lock:
                    if cls._session_id:
                        body["session"] = cls._session_id

                data = cls._post(body)
                if not data:
                    if attempt < RETRY_ATTEMPTS - 1:
                        time.sleep(RETRY_BASE_DELAY * (2 ** attempt))
                    continue

                if data.get("status") == "ok":
                    sol = data.get("solution")
                    if sol and cache:
                        _solver_cache.set(url, sol)
                    return sol

                msg = data.get("message", "")
                log.warning("FlareSolverr: %s", msg)

                if "session" in msg.lower() or "not found" in msg.lower():
                    with cls._lock:
                        cls._session_id = None
                    cls._ensure_session()
                else:
                    break

        return None

    # ── Convenience helpers ───────────────────────────────────────────────

    @classmethod
    def fetch_json(cls, url: str) -> Optional[Dict]:
        sol  = cls.request(url)
        body = (sol or {}).get("response", "")
        if not body:
            return None
        # Strategy 1: raw JSON inside a <pre> tag (Chromium renders it this way)
        for m in re.finditer(r'<pre[^>]*>([\s\S]*?)</pre>', body, re.I):
            txt = (m.group(1)
                   .replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
                   .replace("&quot;", '"').replace("&#39;", "'").strip())
            with contextlib.suppress(json.JSONDecodeError):
                return json.loads(txt)
        # Strategy 2: strip all HTML tags
        with contextlib.suppress(json.JSONDecodeError):
            return json.loads(re.sub(r"<[^>]+>", "", body).strip())
        # Strategy 3: walk to first { … } object
        start = body.find("{")
        if start >= 0:
            depth = in_str = escape = 0
            for i, ch in enumerate(body[start:], start):
                if escape:      escape = 0;  continue
                if ch == "\\" and in_str: escape = 1; continue
                if ch == '"':   in_str ^= 1; continue
                if in_str:      continue
                if ch == "{":   depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        with contextlib.suppress(json.JSONDecodeError):
                            return json.loads(body[start:i + 1])
                        break
        return None

    @classmethod
    def fetch_html(cls, url: str) -> Optional[Tuple[str, List[dict]]]:
        sol = cls.request(url)
        if not sol:
            return None
        return sol.get("response", ""), sol.get("cookies", [])


atexit.register(Solver.destroy_session)


# ═════════════════════════════════════════════════════════════════════════
# 7.  KWIK / ANIMEPAHE EXTRACTION
# ═════════════════════════════════════════════════════════════════════════

_KWIK_DOMAINS = r"kwik\.(?:si|cx|pw|gg|me|net|to|in|cc)"


class JsPacker:
    """Decode eval-based JS packer (p,a,c,k,e,d) used by Kwik."""

    @staticmethod
    def unpack(packed: str) -> str:
        m = re.search(
            r"\}\s*\(\s*'(.*)'\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*'(.*?)'\.split\('\|'\)",
            packed, re.DOTALL,
        )
        if not m:
            return packed
        payload, base_s, count_s, mapping_s = m.groups()
        base, count = int(base_s), int(count_s)
        mapping = mapping_s.split("|")
        digits  = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"

        def enc(c: int) -> str:
            return digits[c] if c < base else enc(c // base) + digits[c % base]

        lookup = {
            enc(i): (mapping[i] if i < len(mapping) and mapping[i] else enc(i))
            for i in range(count)
        }
        return re.sub(r"\b\w+\b", lambda mo: lookup.get(mo.group(0), mo.group(0)), payload)


def _extract_m3u8(html: str) -> Optional[str]:
    """Three-strategy M3U8 URL extraction from a Kwik HTML page."""
    m = re.search(r"(https?://[^\s'\"\\>]+(?:uwu\.m3u8|\.m3u8)[^\s'\"\\>]*)", html)
    if m:
        return m.group(1).replace("\\/", "/").rstrip("\\")

    for script in sorted(
        re.findall(r"<script[^>]*>([\s\S]*?)</script>", html, re.IGNORECASE),
        key=len, reverse=True,
    ):
        cur = script
        for _ in range(6):
            inner = re.search(r'eval\s*\(\s*"((?:[^"\\]|\\.)*)"\s*\)', cur)
            if inner:
                cur = inner.group(1).encode().decode("unicode_escape", errors="ignore")
                continue
            unpacked = JsPacker.unpack(cur)
            if unpacked != cur:
                cur = unpacked
                continue
            break
        m = re.search(r"(https?://[^\s'\"\\>]+(?:uwu\.m3u8|\.m3u8)[^\s'\"\\>]*)", cur)
        if m:
            return m.group(1).replace("\\/", "/").rstrip("\\")

    m = re.search(r'<source[^>]+src=["\']([^"\']+\.m3u8[^"\']*)["\']', html, re.IGNORECASE)
    return m.group(1) if m else None


def _resolve_kwik(url: str) -> Optional[StreamInfo]:
    sol = Solver.request(url, cache=False)
    if not sol:
        return None
    html       = sol.get("response", "")
    cookies    = sol.get("cookies", [])
    user_agent = sol.get("userAgent", "Mozilla/5.0")
    direct     = re.search(r'(https?://[^\s"\']+\.(?:m3u8|mp4)[^\s"\']*)', html)
    video_url  = direct.group(1) if direct else _extract_m3u8(html)
    if video_url:
        return StreamInfo(url=video_url, cookies=cookies,
                          user_agent=user_agent, referer=url)
    return None


def _parse_resolution_buttons(html: str) -> List[Tuple[int, str, bool, str]]:
    entries: List[Tuple[int, str, bool, str]] = []
    menu_m = re.search(r'<div[^>]+id=["\']resolutionMenu["\'][^>]*>(.*?)</div>',
                       html, re.I | re.S)
    if not menu_m:
        return entries
    for btn_m in re.finditer(r'<button\b([^>]*?)>(.*?)</button>',
                              menu_m.group(1), re.I | re.S):
        attrs = btn_m.group(1)
        text  = btn_m.group(2).strip()
        src_m = re.search(r'data-src=["\']([^"\']+kwik\.[^"\']+)["\']', attrs, re.I)
        if not src_m:
            continue
        kwik_url = src_m.group(1)
        res_m = re.search(r'data-resolution=["\']?(\d+)["\']?', attrs, re.I)
        if res_m:
            res = int(res_m.group(1))
        else:
            res_m = re.match(r'(\d+)\s*p', text, re.I)
            if not res_m:
                continue
            res = int(res_m.group(1))
        is_dub   = bool(re.search(r'''data-audio\s*=\s*["']eng["']''', attrs, re.I))
        fansub_m = re.search(r'data-fansub=["\']([^"\']+)["\']', attrs, re.I)
        fansub   = fansub_m.group(1) if fansub_m else (text.split("·")[0].strip())
        entries.append((res, kwik_url, is_dub, fansub))
    return entries


def extract_stream(play_url: str, quality: int = 1080, audio: str = "jpn") -> StreamInfo:
    """
    Resolve an AnimePahe play URL to a StreamInfo.
    Raises RuntimeError if resolution fails.
    """
    sol = Solver.request(play_url, cache=True)
    if not sol:
        raise RuntimeError("FlareSolverr failed to fetch episode page")

    html    = sol["response"]
    entries = _parse_resolution_buttons(html)

    quality_map: Dict[int, Tuple[str, bool, str]] = {}
    if entries:
        filtered = [e for e in entries if (not e[2] if audio == "jpn" else e[2])]
        if not filtered:
            log.warning("Audio filter (%s) removed all links — using all", audio)
            filtered = entries
        for res, url, is_dub, fansub in filtered:
            quality_map.setdefault(res, (url, is_dub, fansub))
    else:
        for url, q_str in re.findall(
            r'(?:href|data-src)=["\']([^"\']*kwik\.[^"\']+)["\'][^>]*>\s*(?:\S+\s+)?(\d+)p',
            html, re.I,
        ):
            with contextlib.suppress(ValueError):
                quality_map.setdefault(int(q_str), (url, False, ""))

    if quality_map:
        qs = sorted(quality_map, reverse=True)
        chosen_q = next((q for q in qs if q <= quality), qs[-1])
        chosen_url, is_dub, fansub = quality_map[chosen_q]
    else:
        kwik_links = re.findall(
            rf'data-src=["\']?(https?://{_KWIK_DOMAINS}/[ef]/\w+)["\']?', html, re.I
        )
        if not kwik_links:
            raise RuntimeError("No Kwik link found on episode page")
        chosen_url, is_dub, fansub = kwik_links[0], False, ""

    title_m = re.search(r"<title>([^<]+)</title>", html)
    title   = title_m.group(1).strip() if title_m else ""

    info = _resolve_kwik(chosen_url)
    if not info:
        raise RuntimeError(f"Could not resolve Kwik URL: {chosen_url}")

    info.title  = title
    info.audio  = "eng" if is_dub else "jpn"
    info.fansub = fansub
    return info


# ═════════════════════════════════════════════════════════════════════════
# 8.  M3U8 PARSER
# ═════════════════════════════════════════════════════════════════════════

def _fetch_text(url: str, headers: Optional[Dict] = None, timeout: int = 30) -> str:
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="replace")


def parse_m3u8(content: str, base_url: str) -> List[Dict[str, Any]]:
    """Parse HLS manifest → list of segment dicts. Handles master playlists & AES-128."""
    lines = content.splitlines()

    # Master playlist — resolve last (highest) variant
    if "#EXT-X-STREAM-INF" in content:
        variants = [
            urllib.parse.urljoin(base_url, lines[i + 1].strip())
            for i, line in enumerate(lines)
            if line.startswith("#EXT-X-STREAM-INF") and i + 1 < len(lines)
        ]
        if variants:
            sub = _fetch_text(variants[-1])
            return parse_m3u8(sub, variants[-1])

    segments: List[Dict[str, Any]] = []
    key_url = key_iv = None
    seq_num = 0

    for line in lines:
        line = line.strip()
        if not line:
            continue
        if line.startswith("#EXT-X-MEDIA-SEQUENCE:"):
            with contextlib.suppress(ValueError):
                seq_num = int(line.split(":", 1)[1])
        elif line.startswith("#EXT-X-KEY:"):
            kv = {k: vq or vr
                  for k, vq, vr in re.findall(r'([^,="]+)=(?:"([^"]+)"|([^,]+))', line[11:])}
            if kv.get("METHOD") == "AES-128":
                if "URI" in kv:
                    key_url = urllib.parse.urljoin(base_url, kv["URI"])
                if iv_hex := kv.get("IV"):
                    h = iv_hex.lstrip("0xX").lstrip("0X")
                    key_iv = bytes.fromhex(("0" + h) if len(h) % 2 else h)
                else:
                    key_iv = None
            else:
                key_url = key_iv = None
        elif not line.startswith("#"):
            iv = key_iv or (seq_num.to_bytes(16, "big") if key_url else None)
            segments.append({"url": urllib.parse.urljoin(base_url, line),
                              "key_url": key_url, "iv": iv})
            seq_num += 1

    return segments


# ═════════════════════════════════════════════════════════════════════════
# 9.  DASHBOARD  (two-tier progress; refresh_per_second=12 for fluidity)
# ═════════════════════════════════════════════════════════════════════════

class Dashboard:
    """
    Clean single-row-per-episode progress.
    No nested progress bars -- segment status is shown as a compact counter.
    """

    def __init__(self, total_eps: int) -> None:
        self._total_eps = total_eps
        self._done_eps  = 0
        self._tasks: Dict[str, TaskID] = {}
        self._live: Optional[Live]     = None

        self._progress = Progress(
            SpinnerColumn(style="cyan", finished_text=" "),
            TextColumn("[bold white]{task.description:<32}"),
            BarColumn(bar_width=16, style="cyan", complete_style="bold green"),
            TextColumn("[bold green]{task.percentage:>5.1f}%"),
            TextColumn("[dim cyan]{task.fields[seg_text]:>12}[/dim cyan]"),
            DownloadColumn(),
            TransferSpeedColumn(),
            TimeRemainingColumn(),
            console=console,
            expand=False,
        )

    def start(self) -> None:
        self._live = Live(
            Panel(
                self._progress,
                title=f"[bold cyan]pahe-batcher[/bold cyan]  [dim]v{VERSION}  -  {self._total_eps} episodes[/dim]",
                border_style="cyan",
                box=box.ROUNDED,
                padding=(0, 1),
            ),
            console=console,
            refresh_per_second=8,
        )
        self._live.start()

    def stop(self) -> None:
        if self._live:
            self._live.stop()

    # -- Task lifecycle ---------------------------------------------------

    def add_ep(self, key: str, label: str, total_bytes: int = 0) -> None:
        if key not in self._tasks:
            kwargs: Dict[str, Any] = {"seg_text": "", "seg_total": 0, "seg_done": 0}
            if total_bytes:
                kwargs["total"] = total_bytes
            self._tasks[key] = self._progress.add_task(label[:32], **kwargs)
        elif (tid := self._tasks.get(key)):
            self._progress.update(tid, description=label[:32])
            if total_bytes and not self._progress.tasks[tid].total:
                self._progress.update(tid, total=total_bytes)

    def set_total(self, key: str, n: int) -> None:
        if tid := self._tasks.get(key):
            self._progress.update(tid, total=n)

    def set_segment_total(self, key: str, total: int) -> None:
        if tid := self._tasks.get(key):
            self._progress.update(tid, seg_total=total, seg_done=0, seg_text=f"0/{total}")

    def seg_done(self, key: str, nbytes: int) -> None:
        if tid := self._tasks.get(key):
            task = self._progress.tasks[tid]
            done = (task.fields.get("seg_done", 0) or 0) + 1
            total = task.fields.get("seg_total", 0) or 0
            seg_text = f"{done}/{total}" if total else ""
            self._progress.update(tid, advance=nbytes, seg_done=done, seg_text=seg_text)

    # -- State transitions ------------------------------------------------

    def mark_resolving(self, key: str, label: str) -> None:
        if tid := self._tasks.get(key):
            self._progress.update(tid, description=f"[dim cyan]⟳ {label[:30]}[/dim cyan]", seg_text="resolving")

    def mark_downloading(self, key: str, label: str) -> None:
        if tid := self._tasks.get(key):
            self._progress.update(tid, description=f"[white]{label[:32]}[/white]")

    def mark_remuxing(self, key: str, label: str) -> None:
        if tid := self._tasks.get(key):
            self._progress.update(tid, description=f"[yellow]⟳ Remuxing {label[:30]}[/yellow]", seg_text="mux")

    def mark_done(self, key: str, label: str) -> None:
        self._done_eps += 1
        if tid := self._tasks.get(key):
            t = self._progress.tasks[tid]
            total = t.total or t.completed or 1
            self._progress.update(
                tid,
                description=f"[bold green]✓ {label[:32]}[/bold green]",
                completed=total,
                total=total,
                seg_text="done",
            )
            self._progress.stop_task(tid)

    def mark_fail(self, key: str, reason: str) -> None:
        if tid := self._tasks.get(key):
            self._progress.update(
                tid,
                description=f"[red]✗ {reason[:32]}[/red]",
                seg_text="fail",
            )
            self._progress.stop_task(tid)


# ═════════════════════════════════════════════════════════════════════════
# 10.  ASYNC HTTP HELPERS
# ═════════════════════════════════════════════════════════════════════════

def make_aio_session(hls_workers: int) -> "aiohttp.ClientSession":
    """
    Well-tuned aiohttp session for HLS segment fetching.
    Using limit=0 (no global cap) with limit_per_host for per-CDN control.
    keepalive_timeout reuses TCP connections across segments of the same stream.
    """
    connector = aiohttp.TCPConnector(
        limit=0,                     # semaphore controls concurrency
        limit_per_host=hls_workers,  # bounded per CDN host
        ttl_dns_cache=300,           # 5-min DNS cache
        keepalive_timeout=45,
        enable_cleanup_closed=True,
        ssl=make_ssl_ctx(),
        use_dns_cache=True,
    )
    return aiohttp.ClientSession(
        connector=connector,
        timeout=aiohttp.ClientTimeout(total=None, connect=10, sock_read=45),
        connector_owner=True,
    )


async def aio_get(
    session: "aiohttp.ClientSession",
    url: str,
    headers: Optional[Dict] = None,
    timeout: int = 60,
) -> bytes:
    for attempt in range(RETRY_ATTEMPTS):
        try:
            async with session.get(
                url, headers=headers,
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as resp:
                resp.raise_for_status()
                return await resp.read()
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError):
            if attempt == RETRY_ATTEMPTS - 1:
                raise
            await asyncio.sleep(RETRY_BASE_DELAY * (2 ** attempt))
    raise AssertionError("unreachable")  # pragma: no cover


async def urllib_get(url: str, headers: Optional[Dict] = None) -> bytes:
    """Async-compatible urllib GET with exponential-backoff retry."""
    def _sync() -> bytes:
        req = urllib.request.Request(url, headers=headers or {})
        for attempt in range(RETRY_ATTEMPTS):
            try:
                with urllib.request.urlopen(req, timeout=60) as r:
                    return r.read()
            except (urllib.error.URLError, OSError):
                if attempt == RETRY_ATTEMPTS - 1:
                    raise
                time.sleep(RETRY_BASE_DELAY * (2 ** attempt))
        raise AssertionError("unreachable")  # pragma: no cover
    return await asyncio.get_event_loop().run_in_executor(None, _sync)


# ═════════════════════════════════════════════════════════════════════════
# 11.  EPISODE DOWNLOADER
# ═════════════════════════════════════════════════════════════════════════

class EpisodeDownloader:
    """
    Downloads one episode's HLS stream using the shared aiohttp session.
    Writes segments directly to temp files (no SQLite overhead).
    """

    def __init__(
        self,
        anime_session: str,
        cfg: DownloadConfig,
        dash: Dashboard,
        session: Optional["aiohttp.ClientSession"],
    ) -> None:
        self.anime_session = anime_session
        self.cfg     = cfg
        self.dash    = dash
        self.session = session

    async def run(self, ep: EpisodeInfo, info: StreamInfo) -> Optional[Path]:
        key   = ep.session
        def _safe(t: str) -> str:
            return t if t and t != "?" else ""
        title = _safe(ep.title) or _safe(info.title) or f"Episode {ep.ep_str}"
        label = f"Ep {ep.ep_str} — {title}"
        store = SegmentStore(self.anime_session, ep.session)
        loop  = asyncio.get_event_loop()

        # Build output path
        outdir = Path(self.cfg.output_dir)
        outdir.mkdir(parents=True, exist_ok=True)
        prefix = ep_prefix(ep.ep_str)
        fname  = sanitize(f"Ep {prefix} - {title}") or f"ep_{ep.ep_str}"
        out    = outdir / f"{fname}.mp4"

        # Already on disk → skip
        if out.exists() and out.stat().st_size > 0:
            self.dash.mark_done(key, f"Ep {ep.ep_str} (already exists)")
            return out

        self.dash.add_ep(key, label)

        try:
            hdrs = info.headers

            # Fetch M3U8
            m3u8_txt = await loop.run_in_executor(None, _fetch_text, info.url, hdrs)
            segments  = parse_m3u8(m3u8_txt, info.url)
            if not segments:
                raise RuntimeError("No segments found in M3U8")

            n        = len(segments)
            done_set = await loop.run_in_executor(None, store.done_indices)
            pending  = [(i, s) for i, s in enumerate(segments) if i not in done_set]

            self.dash.set_total(key, n * _SEG_HINT_BYTES)
            self.dash.set_segment_total(key, n)
            self.dash.mark_downloading(key, label)
            # Advance progress bar for already-downloaded segments
            for _ in done_set:
                self.dash.seg_done(key, _SEG_HINT_BYTES)

            # Prefetch AES keys (usually 0 or 1 unique key)
            key_map: Dict[str, bytes] = {}
            unique_keys = {s["key_url"] for s in segments if s["key_url"]}
            for kurl in unique_keys:
                if hit := _aes_key_cache.get(kurl):
                    key_map[kurl] = hit
                elif self.session:
                    key_map[kurl] = await aio_get(self.session, kurl, hdrs)
                    _aes_key_cache.set(kurl, key_map[kurl])
                else:
                    key_map[kurl] = await urllib_get(kurl, hdrs)
                    _aes_key_cache.set(kurl, key_map[kurl])

            # Segment semaphore — limits concurrent fetches for *this* episode
            seg_sem = asyncio.Semaphore(self.cfg.hls_workers)

            async def fetch_one(idx: int, seg: Dict[str, Any]) -> None:
                async with seg_sem:
                    raw = (
                        await aio_get(self.session, seg["url"], hdrs)
                        if self.session
                        else await urllib_get(seg["url"], hdrs)
                    )
                    if seg["key_url"]:
                        if not HAS_AES:
                            raise RuntimeError(
                                "AES-128 stream detected — install pycryptodomex:\n"
                                "  pip install pycryptodomex"
                            )
                        cipher = AES.new(key_map[seg["key_url"]], AES.MODE_CBC, iv=seg["iv"])
                        raw    = cipher.decrypt(raw)

                    await loop.run_in_executor(None, store.write_seg, idx, raw)
                    self.dash.seg_done(key, len(raw))

            # Fetch all pending segments concurrently
            await asyncio.gather(
                *(asyncio.create_task(fetch_one(i, s)) for i, s in pending)
            )

            # Assemble segments → MP4
            self.dash.mark_remuxing(key, f"Ep {ep.ep_str}")
            ok = await loop.run_in_executor(None, store.assemble, n, out)
            if not ok:
                raise RuntimeError("Assembly failed")

            if not self.cfg.keep_temp:
                await loop.run_in_executor(None, store.cleanup)

            self.dash.mark_done(key, f"Ep {ep.ep_str} — {title[:34]}")
            return out

        except Exception as exc:
            log.exception("Episode %s failed", ep.ep_str)
            self.dash.mark_fail(key, f"Ep {ep.ep_str} — {str(exc)[:40]}")
            return None


# ═════════════════════════════════════════════════════════════════════════
# 12.  BATCH DOWNLOADER — two-stage prefetch pipeline
# ═════════════════════════════════════════════════════════════════════════

class BatchDownloader:
    """
    Pipeline architecture:
    ┌──────────────────────────────────────────────────────────────┐
    │ Stage 1 (resolver)  — FlareSolverr, serialised               │
    │   Resolves episode stream URLs one-by-one, slightly ahead     │
    │   of the downloaders.  No idle time between episodes.         │
    ├──────────────────────────────────────────────────────────────┤
    │ Stage 2 (N workers) — HLS download, max_parallel concurrent   │
    │   Shared aiohttp session → connection reuse across episodes.  │
    │   Per-episode segment semaphore limits CDN load.              │
    └──────────────────────────────────────────────────────────────┘
    """

    def __init__(self, anime: AnimeInfo, cfg: DownloadConfig) -> None:
        self.anime   = anime
        self.cfg     = cfg
        self._results: Dict[str, Optional[Path]] = {}

    async def run(self, episodes: List[EpisodeInfo]) -> Dict[str, Optional[Path]]:
        loop  = asyncio.get_event_loop()
        dash  = Dashboard(len(episodes))
        start = time.time()

        # One shared aiohttp session for all concurrent downloads
        session: Optional["aiohttp.ClientSession"] = (
            make_aio_session(self.cfg.hls_workers) if HAS_AIOHTTP else None
        )

        queue: "asyncio.Queue[Optional[Tuple[EpisodeInfo, StreamInfo]]]" = asyncio.Queue(
            maxsize=self.cfg.max_parallel + 2  # small look-ahead buffer
        )

        # ── Stage 1: resolver ─────────────────────────────────────────────

        async def resolver() -> None:
            for ep in episodes:
                # Show the episode in the dashboard immediately (resolving state)
                placeholder = ep.title or "⏳ Resolving title…"
                label = f"Ep {ep.ep_str} — {placeholder}"
                dash.add_ep(ep.session, label)
                dash.mark_resolving(ep.session, label)
                try:
                    info = await loop.run_in_executor(
                        None, extract_stream, ep.play_url,
                        self.cfg.quality, self.cfg.audio_lang,
                    )
                    await queue.put((ep, info))
                except Exception as exc:
                    dash.mark_fail(ep.session, f"Ep {ep.ep_str}: {exc!s:.35}")
                    self._results[ep.session] = None
            # Sentinels — one per download worker
            for _ in range(self.cfg.max_parallel):
                await queue.put(None)

        # ── Stage 2: download workers ─────────────────────────────────────

        async def download_worker() -> None:
            ep_dl = EpisodeDownloader(self.anime.session, self.cfg, dash, session)
            while True:
                item = await queue.get()
                if item is None:
                    return
                ep, info = item
                path = await ep_dl.run(ep, info)
                self._results[ep.session] = path

        # ── Run pipeline ──────────────────────────────────────────────────

        dash.start()
        try:
            workers = [
                asyncio.create_task(download_worker())
                for _ in range(self.cfg.max_parallel)
            ]
            await asyncio.gather(asyncio.create_task(resolver()), *workers)
            await asyncio.sleep(0.4)  # let final render flush
        finally:
            dash.stop()
            if session:
                await session.close()

        self._print_summary(episodes, time.time() - start)
        return self._results

    def _print_summary(self, episodes: List[EpisodeInfo], elapsed: float) -> None:
        h, rem    = divmod(int(elapsed), 3600)
        m, s      = divmod(rem, 60)
        time_str  = (f"{h}h {m}m {s}s" if h else f"{m}m {s}s" if m else f"{s}s")
        ok        = sum(1 for v in self._results.values() if v is not None)
        fail      = len(self._results) - ok

        console.print()
        console.print(Rule("[bold green] Download Complete [/bold green]", style="green"))

        table = Table(
            box=box.SIMPLE_HEAVY, show_header=True,
            header_style="bold cyan", border_style="dim",
        )
        table.add_column("Ep",     style="cyan",        width=6,  justify="right")
        table.add_column("Title",  style="bold white",  ratio=1)
        table.add_column("Audio",  width=5)
        table.add_column("Status", justify="center",    width=10)
        table.add_column("Size",   justify="right",     width=10, style="cyan")
        table.add_column("File",   style="dim",         ratio=1)

        for ep in episodes:
            path   = self._results.get(ep.session)
            badge  = "[bold green]✓  done[/bold green]" if path else "[red]✗ failed[/red]"
            size   = fmt_bytes(path.stat().st_size) if path and path.exists() else "—"
            fname  = path.name[:40] if path else "—"
            table.add_row(
                ep.ep_str, (ep.title or "—")[:36],
                audio_badge(ep.audio), badge, size, fname,
            )

        console.print(table)

        status_line = f"[bold green]✓ {ok} completed[/bold green]"
        if fail:
            status_line += f"  [bold red]✗ {fail} failed[/bold red]"

        console.print(Panel(
            f"  {status_line}\n"
            f"  [dim]Time:[/dim]      [cyan]{time_str}[/cyan]\n"
            f"  [dim]Saved to:[/dim]  {self.cfg.output_dir}",
            border_style="green" if not fail else "yellow",
            box=box.ROUNDED,
        ))


# ═════════════════════════════════════════════════════════════════════════
# 13.  ANIMEPAHE SCANNER
# ═════════════════════════════════════════════════════════════════════════

_UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I
)


def parse_anime_url(url: str) -> Tuple[str, str]:
    """Validate an AnimePahe series URL; return (host, anime_uuid)."""
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"Unsupported scheme: {parsed.scheme!r}")
    if not parsed.netloc or "animepahe" not in parsed.netloc:
        raise ValueError(f"Not an AnimePahe URL: {url!r}")
    m = _UUID_RE.search(parsed.path)
    if not m:
        raise ValueError(
            "No anime UUID in URL.\n"
            "  Expected: https://animepahe.ru/anime/<uuid>\n"
            f"  Got:      {url}"
        )
    return parsed.netloc, m.group(0)


class AnimePaheScanner:
    def __init__(self, host: str, session: str) -> None:
        self.host    = host
        self.session = session

    def _fetch_page(self, page: int) -> Optional[Dict]:
        url  = (f"https://{self.host}/api?m=release&id={self.session}"
                f"&sort=episode_asc&page={page}")
        data = Solver.fetch_json(url)
        if data is None:
            alt  = (f"https://{self.host}/api/{self.session}"
                    f"/releases?sort=episode_asc&page={page}")
            data = Solver.fetch_json(alt)
        return data

    def _fetch_title(self) -> str:
        result = Solver.fetch_html(f"https://{self.host}/anime/{self.session}")
        if not result:
            return "Unknown Anime"
        html, _ = result
        m = re.search(r"<h1[^>]*>\s*(.*?)\s*</h1>", html, re.I | re.S)
        if m:
            t = re.sub(r"<[^>]+>", "", m.group(1)).strip()
            if len(t) > 4:
                half = len(t) // 2
                if t[:half] == t[half:]:
                    return t[:half].strip()
            return t
        m = re.search(r"<title>([^<]+)</title>", html, re.I)
        if m:
            t = re.sub(r"\s*[|·].*$", "", m.group(1)).strip()
            return re.sub(r"^Watch\s+", "", t, flags=re.I).strip()
        return "Unknown Anime"

    def _parse_page(self, data: Dict) -> List[EpisodeInfo]:
        eps = []
        for item in data.get("data", []):
            if not (ep_sess := item.get("session", "")):
                continue
            eps.append(EpisodeInfo(
                number   = float(item.get("episode", 0) or 0),
                session  = ep_sess,
                title    = ("" if (item.get("title") or "").strip() == "?" else (item.get("title") or "").strip()),
                fansub   = (item.get("fansub") or "").strip(),
                audio    = (item.get("audio") or "jpn").strip().lower(),
                play_url = f"https://{self.host}/play/{self.session}/{ep_sess}",
            ))
        return eps

    def scan(self, prefer_audio: str = "jpn") -> AnimeInfo:
        console.print("  [dim]Fetching episode list …[/dim]", end="\r")
        first = self._fetch_page(1)
        if not first:
            raise RuntimeError(
                "Failed to fetch episode list.\n"
                "  • Is FlareSolverr running?  Check: " + FLARESOLVERR_URL + "\n"
                "  • Is the URL an /anime/ series page (not a /play/ episode link)?"
            )

        last_page = int(first.get("last_page", 1))
        total     = int(first.get("total", 0))
        anime     = AnimeInfo(
            session=self.session, title=self._fetch_title(),
            host=self.host, total=total,
        )
        anime.episodes.extend(self._parse_page(first))

        for page in range(2, last_page + 1):
            time.sleep(REQUEST_DELAY)
            console.print(f"  [dim]Fetching page {page}/{last_page} …[/dim]", end="\r")
            if data := self._fetch_page(page):
                anime.episodes.extend(self._parse_page(data))

        console.print(" " * 60, end="\r")

        # Deduplicate by episode number, favouring preferred audio
        if prefer_audio:
            best: Dict[float, EpisodeInfo] = {}
            for ep in anime.episodes:
                if ep.number not in best or ep.audio == prefer_audio:
                    best[ep.number] = ep
            anime.episodes = sorted(best.values(), key=lambda e: e.number)
        else:
            anime.episodes.sort(key=lambda e: e.number)

        return anime


# ═════════════════════════════════════════════════════════════════════════
# 14.  EPISODE SELECTION  (interactive + non-interactive)
# ═════════════════════════════════════════════════════════════════════════

def _parse_ep_range(raw: str, all_eps: List[EpisodeInfo]) -> List[float]:
    numbers = sorted({ep.number for ep in all_eps})
    result: Set[float] = set()
    for token in re.split(r"[,\s]+", raw):
        token = token.strip()
        if not token:
            continue
        if m := re.match(r"^(\d+(?:\.\d+)?)-(\d+(?:\.\d+)?)$", token):
            lo, hi = float(m.group(1)), float(m.group(2))
            result.update(n for n in numbers if lo <= n <= hi)
        elif m := re.match(r"^(\d+(?:\.\d+)?)-$", token):
            lo = float(m.group(1))
            result.update(n for n in numbers if n >= lo)
        elif m := re.match(r"^(\d+(?:\.\d+)?)$", token):
            result.add(float(m.group(1)))
    return sorted(result)


def _print_ep_table(episodes: List[EpisodeInfo], selected: Set[str]) -> None:
    t = Table(box=box.SIMPLE, show_header=True, header_style="bold cyan", padding=(0, 1))
    t.add_column("",      width=2, justify="center")
    t.add_column("Ep",    width=6, justify="right", style="dim")
    t.add_column("Title", style="white")
    t.add_column("Audio", width=5)
    for ep in episodes:
        check = "[green]✓[/green]" if ep.session in selected else " "
        t.add_row(check, ep.ep_str, ep.title or "—", audio_badge(ep.audio))
    console.print(t)


def select_episodes(anime: AnimeInfo) -> List[EpisodeInfo]:
    """Interactive episode picker."""
    console.print()
    console.print(Rule(f"[bold white] Episode Selection — {anime.title} [/bold white]",
                       style="cyan"))

    available = compact_ep_range(anime.episodes)
    console.print(
        f"  [cyan]{len(anime.episodes)}[/cyan] episodes: "
        f"[bold cyan]{available}[/bold cyan]"
        f"  [dim]({anime.total} total in series)[/dim]\n"
    )
    console.print(Panel(
        "  [bold white]A[/bold white]  All episodes\n"
        "  [bold white]R[/bold white]  Range    [dim]e.g. 1-12  or  1,4,7  or  13-[/dim]\n"
        "  [bold white]L[/bold white]  Toggle   [dim]interactive checklist[/dim]\n"
        "  [bold white]N[/bold white]  Latest N [dim]most recently aired[/dim]\n"
        "  [bold white]S[/bold white]  Skip",
        title="[cyan]Select mode[/cyan]", border_style="dim cyan",
        box=box.ROUNDED, padding=(0, 2),
    ))

    mode = Prompt.ask(
        "  [cyan]Mode[/cyan]",
        choices=["A", "a", "R", "r", "L", "l", "N", "n", "S", "s"],
        default="A",
    ).upper()

    eps_by_num = {ep.number: ep for ep in anime.episodes}

    if mode == "S":
        return []

    if mode == "A":
        console.print(f"  [green]✓[/green] All [cyan]{len(anime.episodes)}[/cyan] episodes selected.")
        return list(anime.episodes)

    if mode == "N":
        n      = IntPrompt.ask("  Latest [cyan]N[/cyan] episodes", default=1)
        chosen = anime.episodes[-max(1, min(n, len(anime.episodes))):]
        console.print(f"  [green]✓[/green] Latest [cyan]{len(chosen)}[/cyan] selected.")
        return chosen

    if mode == "R":
        console.print(
            "  Enter numbers or ranges — e.g. [dim]1-12[/dim]  "
            "[dim]1,4,7[/dim]  [dim]5-[/dim]  [dim]1-6,10[/dim]"
        )
        raw    = Prompt.ask("  [cyan]Episodes[/cyan]").strip()
        nums   = _parse_ep_range(raw, anime.episodes)
        chosen = [eps_by_num[n] for n in nums if n in eps_by_num]
        if not chosen:
            console.print(f"  [yellow]⚠ Nothing matched [bold]{raw}[/bold]. "
                          f"Available: [cyan]{available}[/cyan][/yellow]")
        else:
            console.print(f"  [green]✓[/green] [cyan]{len(chosen)}[/cyan] episodes selected.")
        return chosen

    # mode == "L" — toggle checklist
    selected: Set[str] = set()
    while True:
        console.clear()
        console.print(Rule(f"[bold white] {anime.title} [/bold white]", style="cyan"))
        _print_ep_table(anime.episodes, selected)
        console.print(
            "  [dim]a[/dim]=all  [dim]n[/dim]=none  "
            "[dim]<num>[/dim]=toggle  [dim]done[/dim]=confirm"
        )
        cmd = Prompt.ask("  [cyan]>[/cyan]").strip().lower()
        if cmd in ("done", "d", ""):
            break
        elif cmd == "a":
            selected = {ep.session for ep in anime.episodes}
        elif cmd == "n":
            selected.clear()
        else:
            for num in _parse_ep_range(cmd, anime.episodes):
                if ep := eps_by_num.get(num):
                    selected ^= {ep.session}

    chosen = [ep for ep in anime.episodes if ep.session in selected]
    console.print(f"  [green]✓[/green] [cyan]{len(chosen)}[/cyan] episodes selected.")
    return chosen


def noninteractive_episodes(
    anime:     AnimeInfo,
    mode:      Literal["all", "range", "latest"],
    range_str: str = "",
    latest_n:  int = 1,
) -> List[EpisodeInfo]:
    eps_by_num = {ep.number: ep for ep in anime.episodes}
    if mode == "all":
        return list(anime.episodes)
    if mode == "latest":
        return anime.episodes[-max(1, min(latest_n, len(anime.episodes))):]
    if mode == "range":
        return [eps_by_num[n] for n in _parse_ep_range(range_str, anime.episodes) if n in eps_by_num]
    return []


# ═════════════════════════════════════════════════════════════════════════
# 15.  PRE-DOWNLOAD CONFIRMATION PANEL
# ═════════════════════════════════════════════════════════════════════════

def _confirm_download(anime: AnimeInfo, episodes: List[EpisodeInfo], cfg: DownloadConfig) -> bool:
    """Show a summary panel and ask for confirmation (skipped in non-interactive mode)."""
    n          = len(episodes)
    ep_range   = compact_ep_range(episodes)
    # Rough size estimate: 360p ~50 MB, 720p ~90 MB, 1080p ~150 MB
    est_mb_per = {360: 50, 720: 90, 1080: 150}.get(cfg.quality, 120)
    est_total  = n * est_mb_per

    sub_n = sum(1 for ep in episodes if ep.audio == "jpn")
    dub_n = n - sub_n
    audio_str = ""
    if dub_n and sub_n:
        audio_str = f"[cyan]{sub_n}[/cyan] JPN + [yellow]{dub_n}[/yellow] DUB"
    elif dub_n:
        audio_str = f"[yellow]{dub_n} DUB[/yellow]"
    else:
        audio_str = f"[cyan]{sub_n} JPN[/cyan]"

    console.print()
    console.print(Panel(
        f"  [dim]Series:[/dim]    [bold white]{anime.title}[/bold white]\n"
        f"  [dim]Episodes:[/dim]  [cyan]{n}[/cyan]  ({ep_range})\n"
        f"  [dim]Audio:[/dim]     {audio_str}\n"
        f"  [dim]Quality:[/dim]   [cyan]{cfg.quality}p[/cyan]\n"
        f"  [dim]Output:[/dim]    {cfg.output_dir}\n"
        f"  [dim]Workers:[/dim]   [cyan]{cfg.max_parallel}[/cyan] episodes × "
        f"[cyan]{cfg.hls_workers}[/cyan] segments each\n"
        f"  [dim]Est. size:[/dim] [cyan]~{est_total} MB[/cyan]  "
        f"[dim](~{est_mb_per} MB/ep × {n} eps)[/dim]",
        title=f"[bold green]Ready to Download — {n} episode{'s' if n != 1 else ''}[/bold green]",
        border_style="green", box=box.ROUNDED,
    ))
    return Confirm.ask("  [cyan]Start download?[/cyan]", default=True)


# ═════════════════════════════════════════════════════════════════════════
# 16.  EXPORT MODE
# ═════════════════════════════════════════════════════════════════════════

async def run_export(episodes: List[EpisodeInfo], cfg: DownloadConfig) -> None:
    """Resolve M3U8 links for all episodes and write to a text file."""
    console.print()
    console.print(Rule("[bold white] Exporting Links [/bold white]", style="cyan"))
    loop       = asyncio.get_event_loop()
    results    = []
    start_t    = time.time()

    with Progress(
        SpinnerColumn(),
        TextColumn("[bold white]{task.description:<48}"),
        BarColumn(bar_width=None, style="cyan"),
        MofNCompleteColumn(),
        console=console, expand=True,
    ) as prog:
        task = prog.add_task("Resolving …", total=len(episodes))
        for ep in episodes:
            prog.update(task, description=f"Resolving Ep {ep.ep_str}: {ep.title[:36] or '?'}")
            try:
                info = await loop.run_in_executor(
                    None, extract_stream, ep.play_url, cfg.quality, cfg.audio_lang,
                )
                ff_cmd = (
                    f'ffmpeg -headers "User-Agent: {info.user_agent}\\r\\n'
                    f'Referer: {info.referer}\\r\\n'
                    f'Cookie: {info.cookie_str}\\r\\n" '
                    f'-i "{info.url}" -c copy "Ep_{ep.ep_str}.mp4"'
                )
                results.append({
                    "ep":     ep.ep_str,
                    "title":  ep.title or "—",
                    "url":    info.url,
                    "ua":     info.user_agent,
                    "ref":    info.referer,
                    "cookie": info.cookie_str,
                    "ffmpeg": ff_cmd,
                })
            except Exception as exc:
                console.print(f"  [red]✗ Ep {ep.ep_str}:[/red] {exc}")
            prog.advance(task)

    if not results:
        console.print("\n  [red]No links resolved.[/red]")
        return

    elapsed  = time.time() - start_t
    m, s     = divmod(int(elapsed), 60)
    time_str = f"{m}m {s}s" if m else f"{s}s"

    out_dir  = Path(cfg.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "links_export.txt"

    with open(out_file, "w", encoding="utf-8") as f:
        f.write("=" * 80 + "\n PAHE-BATCHER LINK EXPORT\n")
        f.write(f" Generated: {time.ctime()}\n" + "=" * 80 + "\n\n")
        for item in results:
            f.write(f"EPISODE {item['ep']}: {item['title']}\n")
            f.write(f"  M3U8:     {item['url']}\n")
            f.write(f"  UA:       {item['ua']}\n")
            f.write(f"  Referer:  {item['ref']}\n")
            f.write(f"  Cookie:   {item['cookie']}\n")
            f.write(f"  FFmpeg:\n    {item['ffmpeg']}\n")
            f.write("-" * 40 + "\n\n")

    console.print(Panel(
        f"  [green]✓ Exported [bold]{len(results)}[/bold] links.[/green]\n"
        f"  [dim]Time:[/dim]  [cyan]{time_str}[/cyan]\n"
        f"  [dim]File:[/dim]  [cyan]{out_file}[/cyan]",
        border_style="green", box=box.ROUNDED,
    ))


# ═════════════════════════════════════════════════════════════════════════
# 17.  STREAM MODE  (MPV playback)
# ═════════════════════════════════════════════════════════════════════════

async def run_stream(anime_title: str, episodes: List[EpisodeInfo], cfg: DownloadConfig) -> None:
    if not shutil.which("mpv"):
        console.print("\n  [red]✗ MPV not found![/red]")
        console.print("  [dim]Install MPV from https://mpv.io and ensure it is in your PATH.[/dim]")
        return

    console.print()
    console.print(Rule("[bold white] Streaming via MPV [/bold white]", style="cyan"))

    loop = asyncio.get_event_loop()
    idx  = 0
    while 0 <= idx < len(episodes):
        ep = episodes[idx]
        try:
            with Progress(
                SpinnerColumn(),
                TextColumn(f"[bold white]({idx + 1}/{len(episodes)}) Resolving Ep {ep.ep_str}…"),
                console=console, transient=True,
            ) as prog:
                prog.add_task("", total=None)
                info = await loop.run_in_executor(
                    None, extract_stream, ep.play_url, cfg.quality, cfg.audio_lang,
                )

            # Update episode metadata from stream page
            if info.audio:  ep.audio  = info.audio
            if info.fansub: ep.fansub = info.fansub
            if info.title and (not ep.title or "animepahe" in ep.title.lower()):
                t = re.sub(r"\s*[|·].*$", "", info.title).strip()
                t = re.sub(r"^Watch\s+.*?Episode\s+\d+.*", "", t, flags=re.I).strip()
                if ep.audio == "jpn":
                    t = re.sub(r"\s+DUB\s*$", "", t, flags=re.I).strip()
                if t:
                    ep.title = t

            cmd = [
                "mpv",
                f"--user-agent={info.user_agent}",
                f"--referrer={info.referer}",
                f"--http-header-fields=Cookie: {info.cookie_str}",
                "--demuxer-lavf-format=hls",
                f"--demuxer-lavf-o=cookies={info.cookie_str},referer={info.referer}",
                f"--force-media-title={ep.title or f'Episode {ep.ep_str}'}",
                "--msg-level=all=warn,lavf=error,ffmpeg=error",
                info.url,
            ]

            play_panel = Panel(
                Align.center(Group(
                    Text(anime_title, style="bold cyan underline"),
                    Text.from_markup(f"Now Playing: {ep.label}", style="bold green"),
                    Text(f"Quality: {cfg.quality}p  ·  {idx + 1}/{len(episodes)}", style="dim"),
                    Rule(style="dim", characters="─"),
                    Text("Close MPV window to return to controls", style="italic cyan"),
                )),
                title="[bold cyan]Live Playback[/bold cyan]",
                border_style="green", box=box.ROUNDED, padding=(1, 2),
            )

            with Live(play_panel, console=console, refresh_per_second=4) as live:
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.PIPE,
                )
                _, stderr = await proc.communicate()
                if proc.returncode != 0 and stderr:
                    err = stderr.decode().strip()
                    if "failed" in err.lower() or "error" in err.lower():
                        live.stop()
                        console.print(f"  [red]✗ MPV error:[/red] {err[:200]}")

            # Playback controls
            console.print()
            options: List[str] = []
            if idx < len(episodes) - 1:
                options.append("[bold green][N][/bold green] Next")
            if idx > 0:
                options.append("[bold cyan][P][/bold cyan] Previous")
            options += [
                "[bold yellow][R][/bold yellow] Replay",
                "[bold magenta][S][/bold magenta] Select",
                "[bold red][Q][/bold red] Quit",
            ]
            console.print(Panel(
                Columns(options, padding=(0, 3)),
                title="[dim]Playback Controls[/dim]",
                border_style="dim", expand=False,
            ))

            default = "n" if idx < len(episodes) - 1 else "q"
            choice  = Prompt.ask("  [cyan]Action[/cyan]",
                                 choices=["n", "p", "r", "s", "q"], default=default).lower()

            if choice == "n":   idx += 1
            elif choice == "p": idx -= 1
            elif choice == "r": continue
            elif choice == "q": break
            elif choice == "s":
                sel = Table(box=box.SIMPLE, header_style="bold cyan",
                            title="[bold white]Episode List[/bold white]")
                sel.add_column("#",     justify="right", style="dim")
                sel.add_column("Ep",   justify="right")
                sel.add_column("Title")
                for i, e in enumerate(episodes):
                    style = "bold green" if i == idx else ""
                    sel.add_row(str(i + 1), e.ep_str, e.title or "—", style=style)
                console.print(sel)
                num = IntPrompt.ask("  [cyan]Jump to #[/cyan]", default=idx + 1)
                idx = max(0, min(num - 1, len(episodes) - 1))

        except KeyboardInterrupt:
            break
        except Exception as exc:
            console.print(f"  [red]✗ Error:[/red] {exc}")
            if not Confirm.ask("  [cyan]Try next episode?[/cyan]", default=True):
                break

    console.print("\n  [yellow]Playback session ended.[/yellow]")


# ═════════════════════════════════════════════════════════════════════════
# 18.  INTERACTIVE WIZARD
# ═════════════════════════════════════════════════════════════════════════

def _wizard_config(defaults: DownloadConfig) -> DownloadConfig:
    console.print()
    console.print(Rule("[bold white] Download Settings [/bold white]", style="cyan"))

    # Quality
    _q_default = {360: "1", 720: "2", 1080: "3"}.get(defaults.quality, "3")
    console.print(Panel(
        "  [bold white]1[/bold white]  [dim cyan]360p [/dim cyan]  [dim]~50 MB/ep[/dim]\n"
        "  [bold white]2[/bold white]  [cyan]720p [/cyan]  [dim]~90 MB/ep   · recommended[/dim]\n"
        "  [bold white]3[/bold white]  [bold cyan]1080p[/bold cyan]  [dim]~150 MB/ep  · best quality[/dim]",
        title="[cyan]Quality[/cyan]", border_style="dim cyan", box=box.ROUNDED, padding=(0, 2),
    ))
    quality = {1: 360, 2: 720, 3: 1080}[int(
        Prompt.ask("  [cyan]Select[/cyan]", choices=["1", "2", "3"], default=_q_default)
    )]

    # Audio
    _audio_default = "1" if defaults.audio_lang == "jpn" else "2"
    console.print(Panel(
        "  [bold white]1[/bold white]  [cyan]Subbed[/cyan]  [dim](Japanese audio)[/dim]\n"
        "  [bold white]2[/bold white]  [yellow]Dubbed[/yellow]  [dim](English audio)[/dim]",
        title="[cyan]Audio Language[/cyan]", border_style="dim cyan", box=box.ROUNDED, padding=(0, 2),
    ))
    audio_lang = "jpn" if Prompt.ask(
        "  [cyan]Select[/cyan]", choices=["1", "2"], default=_audio_default
    ) == "1" else "eng"

    # Output directory
    output_dir = Prompt.ask(
        "  [cyan]Output directory[/cyan]", default=defaults.output_dir
    ).strip()
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    # Concurrency
    console.print(Panel(
        "  [bold white]1[/bold white]  [dim]1 download   · safest[/dim]\n"
        "  [bold white]2[/bold white]  [cyan]2 simultaneous  · recommended[/cyan]\n"
        "  [bold white]4[/bold white]  [dim]4 simultaneous  · faster, more RAM[/dim]\n"
        "  [bold white]6[/bold white]  [dim]6 simultaneous  · may trigger rate-limits[/dim]",
        title="[cyan]Concurrent Downloads[/cyan]", border_style="dim cyan",
        box=box.ROUNDED, padding=(0, 2),
    ))
    max_parallel = max(1, min(6, IntPrompt.ask(
        "  [cyan]Select[/cyan]", default=defaults.max_parallel
    )))
    hls_workers = max(8, min(32, IntPrompt.ask(
        "  [cyan]HLS workers per episode[/cyan] [dim](8–32, default 24)[/dim]",
        default=defaults.hls_workers,
    )))

    return DownloadConfig(
        output_dir=output_dir, max_parallel=max_parallel,
        hls_workers=hls_workers, quality=quality, audio_lang=audio_lang,
    )


# ═════════════════════════════════════════════════════════════════════════
# 19.  BANNER
# ═════════════════════════════════════════════════════════════════════════

_BANNER = r"""
 ____        _            ____        _       _
|  _ \ __ _| |__   ___  | __ )  __ _| |_ ___| |__   ___ _ __
| |_) / _` | '_ \ / _ \ |  _ \ / _` | __/ __| '_ \ / _ \ '__|
|  __/ (_| | | | |  __/ | |_) | (_| | || (__| | | |  __/ |
|_|   \__,_|_| |_|\___| |____/ \__,_|\__\___|_| |_|\___|_|
"""


def _print_banner() -> None:
    console.print(Panel(
        Align.center(
            Text(_BANNER, style="bold cyan") +
            Text(f"\n  v{VERSION}  ·  AnimePahe Batch Downloader\n", style="dim cyan")
        ),
        border_style="cyan", box=box.DOUBLE, padding=(0, 2),
    ))


# ═════════════════════════════════════════════════════════════════════════
# 20.  MAIN ASYNC ENTRY POINT
# ═════════════════════════════════════════════════════════════════════════

async def _main(args: argparse.Namespace) -> None:
    _print_banner()

    # ── Parse + validate URL ──────────────────────────────────────────────
    try:
        host, session = parse_anime_url(args.url)
    except ValueError as exc:
        console.print(f"\n  [red]✗ Invalid URL:[/red] {exc}")
        sys.exit(1)

    # ── FlareSolverr health check ─────────────────────────────────────────
    console.print(Rule("[bold white] Checking Prerequisites [/bold white]", style="cyan"))
    console.print(f"  [dim]FlareSolverr:[/dim] {FLARESOLVERR_URL}  ", end="")
    if Solver.ping():
        console.print("[green]✓ reachable[/green]")
    else:
        console.print("[red]✗ not responding[/red]")
        console.print(
            "\n  [red bold]FlareSolverr is not running![/red bold]\n"
            "  [dim]Start it with Docker:[/dim]\n"
            "    docker run -d --name=flaresolverr -p 8191:8191 ghcr.io/flaresolverr/flaresolverr\n"
            f"  [dim]Or set FLARESOLVERR_URL env var if it's on a different host.[/dim]"
        )
        sys.exit(1)

    if not HAS_AIOHTTP:
        console.print(
            "  [yellow]⚠ aiohttp not installed — using urllib (slower)[/yellow]\n"
            "  [dim]Install for 3–5× faster downloads:  pip install aiohttp[/dim]"
        )

    # ── Scan series ───────────────────────────────────────────────────────
    console.print()
    console.print(Rule("[bold white] Scanning Series [/bold white]", style="cyan"))
    console.print(f"  [dim]Host:[/dim]     {host}")
    console.print(f"  [dim]Session:[/dim]  {session}\n")

    try:
        anime = AnimePaheScanner(host, session).scan(prefer_audio=args.audio_lang)
    except RuntimeError as exc:
        console.print(f"\n  [red]✗ Scan failed:[/red] {exc}")
        sys.exit(1)

    sub_n = sum(1 for ep in anime.episodes if ep.audio == "jpn")
    dub_n = len(anime.episodes) - sub_n
    audio_info = (
        f"{sub_n} JPN, {dub_n} DUB" if dub_n else "JPN audio"
    )
    console.print(
        f"  [green]✓[/green] [bold]{anime.title}[/bold]\n"
        f"  — [cyan]{len(anime.episodes)}[/cyan] episodes  "
        f"({compact_ep_range(anime.episodes)})  "
        f"[dim]{audio_info}[/dim]\n"
    )

    if args.list_only:
        t = Table(box=box.SIMPLE, show_header=True, header_style="bold cyan")
        t.add_column("Ep",    width=6, justify="right")
        t.add_column("Title", style="white")
        t.add_column("Audio", width=5)
        for ep in anime.episodes:
            t.add_row(ep.ep_str, ep.title or "—", audio_badge(ep.audio))
        console.print(t)
        return

    # ── Determine mode: interactive vs scripted ───────────────────────────
    _scripted = bool(args.all or args.range or args.latest or args.export or args.stream)
    _cached_cfg: Optional[DownloadConfig] = None

    while True:
        # ── Action selection ──────────────────────────────────────────────
        if _scripted:
            mode = "export" if args.export else "stream" if args.stream else "download"
        else:
            console.print()
            console.print(Rule("[bold white] Action [/bold white]", style="cyan"))
            console.print(Panel(
                "  [bold white]1[/bold white]  [cyan]Download[/cyan]  [dim]· save .mp4 files[/dim]\n"
                "  [bold white]2[/bold white]  [cyan]Export[/cyan]    [dim]· get M3U8 URLs + headers[/dim]\n"
                "  [bold white]3[/bold white]  [cyan]Stream[/cyan]    [dim]· play in MPV[/dim]\n"
                "  [bold white]4[/bold white]  [cyan]List[/cyan]      [dim]· show episode table[/dim]\n"
                "  [bold white]5[/bold white]  [red]Exit[/red]",
                title=f"[bold cyan]{anime.title}[/bold cyan]",
                border_style="cyan", box=box.ROUNDED, padding=(0, 2),
            ))
            _default = "5" if _cached_cfg else "1"
            choice = Prompt.ask(
                "  [cyan]Select[/cyan]",
                choices=["1", "2", "3", "4", "5"],
                default=_default,
            )
            if choice == "5":
                break
            if choice == "4":
                t = Table(box=box.SIMPLE, show_header=True, header_style="bold cyan")
                t.add_column("Ep",    width=6, justify="right")
                t.add_column("Title", style="white")
                t.add_column("Audio", width=5)
                for ep in anime.episodes:
                    t.add_row(ep.ep_str, ep.title or "—", audio_badge(ep.audio))
                console.print(t)
                continue
            mode = {"1": "download", "2": "export", "3": "stream"}[choice]

        # ── Episode selection ─────────────────────────────────────────────
        if args.all:
            chosen = noninteractive_episodes(anime, "all")
        elif args.range:
            chosen = noninteractive_episodes(anime, "range", range_str=args.range)
            if not chosen:
                console.print(f"  [red]✗ No episodes matched:[/red] {args.range}")
                console.print(f"    Available: [cyan]{compact_ep_range(anime.episodes)}[/cyan]")
                if _scripted: sys.exit(1)
                continue
        elif args.latest:
            chosen = noninteractive_episodes(anime, "latest", latest_n=args.latest)
        else:
            chosen = select_episodes(anime)

        if not chosen:
            if _scripted:
                break
            continue

        # ── Build config ──────────────────────────────────────────────────
        safe_title = sanitize(anime.title)
        series_dir = os.path.join(args.output, safe_title)

        if _scripted:
            cfg = DownloadConfig(
                output_dir=series_dir, max_parallel=args.parallel,
                hls_workers=args.workers, quality=args.quality,
                export_mode=(mode == "export"), stream_mode=(mode == "stream"),
                audio_lang=args.audio_lang,
            )
        elif mode == "stream":
            cfg = DownloadConfig(
                quality=args.quality, stream_mode=True, audio_lang=args.audio_lang,
                output_dir=series_dir,
            )
        else:
            # Reuse previous settings if same mode
            if _cached_cfg and (
                (mode == "download" and not _cached_cfg.export_mode and not _cached_cfg.stream_mode) or
                (mode == "export" and _cached_cfg.export_mode)
            ):
                cfg = _cached_cfg
                console.print(
                    f"  [dim]Reusing settings:[/dim] "
                    f"[cyan]{cfg.quality}p[/cyan] · "
                    f"[cyan]{'Sub' if cfg.audio_lang == 'jpn' else 'Dub'}[/cyan] · "
                    f"[cyan]{cfg.output_dir}[/cyan]"
                )
            else:
                cfg = _wizard_config(DownloadConfig(
                    output_dir=series_dir, max_parallel=args.parallel,
                    hls_workers=args.workers, quality=args.quality,
                    audio_lang=args.audio_lang,
                ))
                _cached_cfg = cfg

        # ── Execute ───────────────────────────────────────────────────────
        if mode == "export":
            await run_export(chosen, cfg)
            break

        elif mode == "stream":
            await run_stream(anime.title, chosen, cfg)
            if _scripted:
                break

        else:  # download
            # Confirmation summary (skipped in scripted mode)
            if not _scripted:
                if not _confirm_download(anime, chosen, cfg):
                    continue

            dl = BatchDownloader(anime, cfg)
            await dl.run(chosen)
            break

        if _scripted:
            break

    # ── Cleanup orphaned cache dirs from old crashed runs ─────────────────
    with contextlib.suppress(Exception):
        now = time.time()
        for p in CACHE_DIR.glob("*/*/*.ts"):
            if now - p.stat().st_mtime > 86400:   # older than 1 day
                with contextlib.suppress(Exception):
                    shutil.rmtree(p.parent.parent)

    console.print("\n  [bold cyan]Session finished.[/bold cyan]")
    Solver.destroy_session()


# ═════════════════════════════════════════════════════════════════════════
# 21.  CLI
# ═════════════════════════════════════════════════════════════════════════

def main() -> None:
    parser = argparse.ArgumentParser(
        prog="pahe_batcher",
        description=f"pahe-batcher v{VERSION} — AnimePahe Batch Downloader",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  %(prog)s https://animepahe.ru/anime/<uuid>                        # wizard\n"
            "  %(prog)s https://animepahe.ru/anime/<uuid> --all                  # all episodes\n"
            "  %(prog)s https://animepahe.ru/anime/<uuid> --range 1-12           # season 1\n"
            "  %(prog)s https://animepahe.ru/anime/<uuid> --latest 3             # last 3\n"
            "  %(prog)s https://animepahe.ru/anime/<uuid> --list                 # list only\n"
            "  %(prog)s https://animepahe.ru/anime/<uuid> --audio eng --all      # dubbed\n"
            "  %(prog)s https://animepahe.ru/anime/<uuid> --all -o ~/anime -j 3  # full flags\n"
        ),
    )

    parser.add_argument("url", metavar="URL",
                        help="AnimePahe series URL (https://animepahe.ru/anime/<uuid>)")

    sel = parser.add_mutually_exclusive_group()
    sel.add_argument("--all",    "-a", action="store_true",
                     help="Download every episode")
    sel.add_argument("--range",  "-r", metavar="RANGE",
                     help='Episode range, e.g. "1-12" "1,4,7" "13-"')
    sel.add_argument("--latest", "-n", metavar="N", type=int,
                     help="Download the latest N episodes")

    parser.add_argument("--list",   "-l", action="store_true", dest="list_only",
                        help="List episodes and exit (no download)")
    parser.add_argument("--export", "-e", action="store_true",
                        help="Export M3U8 links to a file")
    parser.add_argument("--stream", "-s", action="store_true",
                        help="Stream episodes via MPV")

    parser.add_argument("-o", "--output",   default="./downloads",
                        help="Output directory (default: ./downloads)")
    parser.add_argument("-q", "--quality",  metavar="Q", type=int,
                        choices=[360, 720, 1080], default=1080,
                        help="Quality: 360, 720, or 1080 (default: 1080)")
    parser.add_argument("--audio",          metavar="LANG", type=str,
                        choices=["jpn", "eng"], default="jpn", dest="audio_lang",
                        help="Audio preference: jpn=subbed (default), eng=dubbed")
    parser.add_argument("-j", "--parallel", metavar="N", type=int, default=2,
                        help="Concurrent episode downloads (default: 2, max: 6)")
    parser.add_argument("-w", "--workers",  metavar="N", type=int, default=HLS_WORKERS,
                        help=f"HLS segment workers per episode (default: {HLS_WORKERS}, max: 32)")
    parser.add_argument("--keep-temp",      action="store_true",
                        help="Keep raw segment files after download (for debugging)")

    args = parser.parse_args()
    args.parallel = max(1, min(6, args.parallel))
    args.workers  = max(8, min(32, args.workers))

    try:
        asyncio.run(_main(args))
    except KeyboardInterrupt:
        console.print("\n  [yellow]Interrupted.[/yellow]")
        Solver.destroy_session()
        sys.exit(0)


if __name__ == "__main__":
    main()
