#!/usr/bin/env python3
"""
pahe-batcher v2.0.0 — AnimePahe Batch Downloader
=================================================
Blazing-fast HLS engine · Shared aiohttp pool · Prefetch pipeline · Rich TUI
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
_AIOHTTP_ERR: Optional[str] = None
try:
    import aiohttp
    HAS_AIOHTTP = True
except Exception as _exc:
    HAS_AIOHTTP = False
    _AIOHTTP_ERR = str(_exc)

console = Console()

# ─────────────────────────────────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────────────────────────────────

VERSION          = "2.2.0"
HLS_WORKERS      = 24          # parallel segment fetches per episode
RETRY_ATTEMPTS   = 5
RETRY_BASE_DELAY = 0.5
FLARESOLVERR_URL = os.getenv("FLARESOLVERR_URL", "http://localhost:8191/v1")
REQUEST_DELAY    = 0.4         # between API page fetches
CACHE_DIR        = Path("pahe_cache")

def _ensure_gitignore() -> None:
    """Ensure pahe_cache/ is in .gitignore."""
    git_ignore = Path(".gitignore")
    entry = "pahe_cache/"
    if git_ignore.exists():
        content = git_ignore.read_text(encoding="utf-8")
        if entry not in content:
            with open(git_ignore, "a", encoding="utf-8") as f:
                f.write(f"\n# Pahe-Batcher Cache\n{entry}\n")
    else:
        git_ignore.write_text(f"# Pahe-Batcher Cache\n{entry}\n", encoding="utf-8")

_ensure_gitignore()

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
    has_session: bool = False

    def get_variant(self, number: float, audio: str) -> Optional[EpisodeInfo]:
        """Find a specific audio variant for an episode number."""
        for ep in self.episodes:
            if ep.number == number and ep.audio == audio:
                return ep
        return None

    def get_all_variants(self, number: float) -> List[EpisodeInfo]:
        """Return all available audio variants for an episode number."""
        return [ep for ep in self.episodes if ep.number == number]


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


def audio_suffix(audio: str) -> str:
    """Return a simple suffix for filenames."""
    return "_SUB" if audio == "jpn" else "_DUB"


def audio_badge(audio: str, all_variants: List[EpisodeInfo] = None) -> str:
    # Just return a simple indicator for UI if needed, but we'll remove it from tables
    return "SUB" if audio == "jpn" else "DUB"

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
    Stores HLS segments as individual numbered files in a persistent cache directory.
    Structure: pahe_cache/[Sanitized_Anime_Title]_[Anime_UUID]/Ep_[Number]/
    """

    def __init__(self, anime_title: str, anime_session: str, ep_num: str) -> None:
        safe_title = sanitize(anime_title)
        self.root  = CACHE_DIR / f"{safe_title}_{anime_session}"
        self.dir   = self.root / f"Ep_{ep_num}"
        self.dir.mkdir(parents=True, exist_ok=True)

    def save_metadata(self, anime_title: str, url: str) -> None:
        """Save session metadata for the Library view."""
        meta = self.root / "session.json"
        if not meta.exists():
            with open(meta, "w", encoding="utf-8") as f:
                json.dump({"title": anime_title, "url": url, "updated": time.time()}, f, indent=2)

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
    """
    Directly fetch Kwik page using urllib with Referer to bypass Cloudflare.
    FlareSolverr is often blocked on Kwik, but direct requests with a Referer usually work.
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": "https://animepahe.com/",
    }
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=30) as r:
            html = r.read().decode("utf-8", errors="replace")
            # Extract cookies if any (though usually not needed for the m3u8 itself)
            cookies = []
            if cookie_hdr := r.headers.get("Set-Cookie"):
                # Simple cookie parser for Kwik
                for part in cookie_hdr.split(","):
                    if "=" in part:
                        name_val = part.split(";")[0].strip()
                        if "=" in name_val:
                            n, v = name_val.split("=", 1)
                            cookies.append({"name": n, "value": v})

            user_agent = headers["User-Agent"]
            direct     = re.search(r'(https?://[^\s"\']+\.(?:m3u8|mp4)[^\s"\']*)', html)
            video_url  = direct.group(1) if direct else _extract_m3u8(html)
            if video_url:
                return StreamInfo(url=video_url, cookies=cookies,
                                  user_agent=user_agent, referer=url)
    except Exception as exc:
        log.debug("Direct Kwik resolve failed: %s", exc)
        # Fallback to Solver if direct fails
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
    Clean standardized progress.
    Unified metric: Segments (M of N) drives the bar and ETA.
    Accurate metric: Bytes track the real file size.
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
            MofNCompleteColumn(),
            TextColumn("[bold green]{task.percentage:>4.0f}%"),
            TransferSpeedColumn(),
            TimeRemainingColumn(),
            TextColumn("[dim cyan]{task.fields[size]:>10}[/dim cyan]"),
            console=console,
            expand=False,
        )

    def start(self) -> None:
        # coordinated header table for perfect column alignment
        header = Table.grid(padding=(0, 1))
        header.add_column(width=3)  # spinner
        header.add_column(width=32) # title
        header.add_column(width=16) # bar
        header.add_column(width=10, justify="center") # segments
        header.add_column(width=5,  justify="center") # %
        header.add_column(width=11, justify="center") # speed
        header.add_column(width=10, justify="center") # eta
        header.add_column(width=10, justify="right")  # size

        header.add_row(
            "", "[bold white]Episode Title[/bold white]", "[bold white]Progress[/bold white]",
            "[bold white]Segments[/bold white]", "[bold white]%[/bold white]",
            "[bold white]Speed[/bold white]", "[bold white]ETA[/bold white]", "[bold white]Size[/bold white]"
        )

        console.clear()
        self._live = Live(
            Panel(
                Group(
                    header,
                    Rule(style="dim"),
                    self._progress,
                ),
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
        time.sleep(0.1) # tiny cooldown for terminal buffer

    # -- Task lifecycle ---------------------------------------------------

    def add_ep(self, key: str, label: str, total_segments: int = 0) -> None:
        if key not in self._tasks:
            kwargs: Dict[str, Any] = {"size": "0 B", "bytes_done": 0}
            if total_segments:
                kwargs["total"] = total_segments
            self._tasks[key] = self._progress.add_task(label[:32], **kwargs)
        else:
            tid = self._tasks[key]
            self._progress.update(tid, description=label[:32])
            if total_segments and self._progress.tasks[tid].total is None:
                self._progress.update(tid, total=total_segments)

    def set_total(self, key: str, n_segments: int) -> None:
        """Set the total number of segments (this drives the progress bar)."""
        if (tid := self._tasks.get(key)) is not None:
            self._progress.update(tid, total=n_segments)

    def set_segment_total(self, key: str, total: int) -> None:
        """Alias for compatibility; sets the primary segment total."""
        self.set_total(key, total)

    def seg_done(self, key: str, nbytes: int) -> None:
        """One segment finished. Advance count by 1 and accumulate real bytes."""
        if (tid := self._tasks.get(key)) is not None:
            task = self._progress.tasks[tid]
            new_bytes = (task.fields.get("bytes_done", 0)) + nbytes
            self._progress.update(
                tid,
                advance=1,
                bytes_done=new_bytes,
                size=fmt_bytes(new_bytes)
            )

    # -- State transitions ------------------------------------------------

    def mark_resolving(self, key: str, label: str) -> None:
        if (tid := self._tasks.get(key)) is not None:
            self._progress.update(tid, description=f"[cyan]⟳ {label[:30]}[/cyan]", size="resolving")

    def mark_waiting(self, key: str, label: str) -> None:
        if (tid := self._tasks.get(key)) is not None:
            self._progress.update(tid, description=f"[dim]⋯ {label[:30]}[/dim]", size="waiting")

    def mark_queued(self, key: str, label: str) -> None:
        if (tid := self._tasks.get(key)) is not None:
            self._progress.update(tid, description=f"[dim cyan]⌛ {label[:30]}[/dim cyan]", size="queued")

    def mark_downloading(self, key: str, label: str) -> None:
        if (tid := self._tasks.get(key)) is not None:
            self._progress.update(tid, description=f"[bold white]{label[:32]}[/bold white]")

    def mark_remuxing(self, key: str, label: str) -> None:
        if (tid := self._tasks.get(key)) is not None:
            self._progress.update(tid, description=f"[yellow]⟳ Remuxing {label[:30]}[/yellow]", size="muxing")
            # Stop the task to freeze speed/ETA artifacts
            self._progress.stop_task(tid)

    def mark_done(self, key: str, label: str) -> None:
        self._done_eps += 1
        if (tid := self._tasks.get(key)) is not None:
            t = self._progress.tasks[tid]
            total = t.total or t.completed or 1
            # Keep the last known size instead of setting it to "done"
            final_size = t.fields.get("size", "done")
            self._progress.update(
                tid,
                description=f"[bold green]✓ {label[:32]}[/bold green]",
                completed=total,
                total=total,
                size=final_size,
            )
            # Stop the task so speed/ETA columns zero out or hide
            self._progress.stop_task(tid)

    def mark_fail(self, key: str, reason: str) -> None:
        if (tid := self._tasks.get(key)) is not None:
            self._progress.update(
                tid,
                description=f"[red]✗ {reason[:32]}[/red]",
                size="fail",
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
        anime_title: str,
        anime_session: str,
        anime_url: str,
        cfg: DownloadConfig,
        dash: Dashboard,
        session: Optional["aiohttp.ClientSession"],
    ) -> None:
        self.anime_title   = anime_title
        self.anime_session = anime_session
        self.anime_url     = anime_url
        self.cfg     = cfg
        self.dash    = dash
        self.session = session

    async def run(self, ep: EpisodeInfo, info: StreamInfo) -> Optional[Path]:
        key   = ep.session
        title = ep.title or info.title or f"Episode {ep.ep_str}"
        label = f"Ep {ep.ep_str} — {title}"
        store = SegmentStore(self.anime_title, self.anime_session, ep.ep_str)
        loop  = asyncio.get_event_loop()

        # Save metadata for library view
        await loop.run_in_executor(None, store.save_metadata, self.anime_title, self.anime_url)

        # Build output path
        outdir = Path(self.cfg.output_dir)
        outdir.mkdir(parents=True, exist_ok=True)
        prefix = ep_prefix(ep.ep_str)
        suffix = audio_suffix(ep.audio)
        fname  = sanitize(f"Ep {prefix} - {title}{suffix}") or f"ep_{ep.ep_str}{suffix}"
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

            self.dash.set_total(key, n)
            self.dash.mark_downloading(key, label)
            # Advance progress bar for already-downloaded segments
            for _ in done_set:
                self.dash.seg_done(key, 0)

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

    def __init__(self, anime: AnimeInfo, anime_url: str, cfg: DownloadConfig) -> None:
        self.anime     = anime
        self.anime_url = anime_url
        self.cfg       = cfg
        self._results: Dict[str, Optional[Path]] = {}

    def _count_cached(self, ep: EpisodeInfo) -> int:
        """Count how many segments already exist in the cache for this episode."""
        store = SegmentStore(self.anime.title, self.anime.session, ep.ep_str)
        return len(store.done_indices())

    def _find_existing(self, ep: EpisodeInfo) -> Optional[Path]:
        """Check if this episode already exists in the output directory."""
        outdir = Path(self.cfg.output_dir)
        if not outdir.exists():
            return None
        prefix = ep_prefix(ep.ep_str)
        suffix = audio_suffix(ep.audio)
        # Search for files starting with Ep <prefix> or Ep_<prefix>
        for p in outdir.iterdir():
            if not p.is_file() or p.suffix not in (".mp4", ".ts"):
                continue
            if (p.name.startswith(f"Ep {prefix}") or p.name.startswith(f"Ep_{prefix}")) and suffix in p.name:
                if p.stat().st_size > 0:
                    return p
        return None

    async def run(self, episodes: List[EpisodeInfo]) -> Dict[str, Optional[Path]]:
        loop  = asyncio.get_event_loop()
        dash  = Dashboard(len(episodes))
        start = time.time()

        # One shared aiohttp session for all concurrent downloads
        session: Optional["aiohttp.ClientSession"] = (
            make_aio_session(self.cfg.hls_workers) if HAS_AIOHTTP else None
        )

        # Pre-populate dashboard
        for ep in episodes:
            label = f"Ep {ep.ep_str} — {ep.title or 'Pending...'}"
            dash.add_ep(ep.session, label)
            dash.mark_waiting(ep.session, label)

        # Queues
        # resolve_queue: Episodes needing stream info
        # download_queue: (EpisodeInfo, StreamInfo) ready for download
        resolve_queue: "asyncio.Queue[EpisodeInfo]" = asyncio.Queue()
        for ep in episodes:
            await resolve_queue.put(ep)

        download_queue: "asyncio.Queue[Optional[Tuple[EpisodeInfo, StreamInfo]]]" = asyncio.Queue(
            maxsize=self.cfg.max_parallel + 2
        )
        # ── Stage 1: Resolver worker ──────────────────────────────────────

        async def resolver() -> None:
            while not resolve_queue.empty():
                raw_ep = await resolve_queue.get()

        # 1. Selection: for each episode number, find the variant that matches cfg.audio_lang.
                # If the preferred audio isn't available, we fallback to the first available variant.
                variants = self.anime.get_all_variants(raw_ep.number)
                ep = self.anime.get_variant(raw_ep.number, self.cfg.audio_lang) or variants[0]

                # 2. Failover: check if already downloaded
                existing = await loop.run_in_executor(None, self._find_existing, ep)
                ...
                if existing:
                    self._results[ep.session] = existing
                    # Title extraction
                    title = ep.title
                    if not title or title == "?":
                        with contextlib.suppress(Exception):
                            t = existing.stem
                            for sep in (" - ", "_-_"):
                                if sep in t:
                                    t = t.split(sep, 1)[-1]
                                    break
                            title = t.replace("_", " ").strip()

                    dash.add_ep(ep.session, f"Ep {ep.ep_str} — {title or 'Already exists'}")
                    dash.mark_done(ep.session, f"Ep {ep.ep_str} (already exists)")
                    resolve_queue.task_done()
                    continue

                # 2. Extract stream
                label = f"Ep {ep.ep_str} — {ep.title or 'Resolving...'}"
                dash.mark_resolving(ep.session, label)

                try:
                    # ADDED TIMEOUT: 120 seconds for resolution
                    info = await asyncio.wait_for(
                        loop.run_in_executor(
                            None, extract_stream, ep.play_url,
                            self.cfg.quality, self.cfg.audio_lang,
                        ),
                        timeout=120.0
                    )
                    if info.title and (not ep.title or ep.title == "?"):
                        ep.title = info.title

                    real_label = f"Ep {ep.ep_str} — {ep.title or f'Episode {ep.ep_str}'}"
                    dash.add_ep(ep.session, real_label)
                    dash.mark_queued(ep.session, real_label)
                    await download_queue.put((ep, info))
                except asyncio.TimeoutError:
                    log.error("Resolution timed out for Ep %s", ep.ep_str)
                    dash.mark_fail(ep.session, f"Ep {ep.ep_str}: Resolution Timeout")
                    self._results[ep.session] = None
                except Exception as exc:
                    log.error("Failed to resolve Ep %s: %s", ep.ep_str, exc)
                    dash.mark_fail(ep.session, f"Ep {ep.ep_str}: {exc!s:.35}")
                    self._results[ep.session] = None

                resolve_queue.task_done()

                # Sentinels for download workers
                for _ in range(self.cfg.max_parallel):
                    await download_queue.put(None)
                    # ── Stage 2: Download workers ─────────────────────────────────────

        async def download_worker() -> None:
            ep_dl = EpisodeDownloader(
                self.anime.title, self.anime.session, self.anime_url,
                self.cfg, dash, session
            )
            while True:
                item = await download_queue.get()
                if item is None:
                    download_queue.task_done()
                    return
                ep, info = item
                path = await ep_dl.run(ep, info)
                self._results[ep.session] = path
                download_queue.task_done()

        # ── Run pipeline ──────────────────────────────────────────────────

        dash.start()
        try:
            # We use 1 resolver worker (FlareSolverr is serial) but multiple downloaders
            tasks = [asyncio.create_task(resolver())]
            tasks.extend(
                asyncio.create_task(download_worker())
                for _ in range(self.cfg.max_parallel)
            )
            await asyncio.gather(*tasks)
            await asyncio.sleep(0.4)
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
        table.add_column("Title",  style="bold white",  ratio=1,  overflow="ellipsis")
        table.add_column("Audio",  width=5)
        table.add_column("Status", justify="center",    width=10)
        table.add_column("Size",   justify="right",     width=10, style="cyan")
        table.add_column("File",   style="dim",         ratio=1,  overflow="ellipsis")

        for ep in episodes:
            path   = self._results.get(ep.session)
            badge  = "[bold green]✓  done[/bold green]" if path else "[red]✗ failed[/red]"
            size   = fmt_bytes(path.stat().st_size) if path and path.exists() else "—"
            fname  = path.name if path else "—"
            table.add_row(
                ep.ep_str, (ep.title or "—"),
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
    @classmethod
    def search(cls, host: str, query: str) -> List[Dict[str, Any]]:
        """Search AnimePahe for a title and return results. Tries multiple hosts on failure."""
        hosts = [host, "animepahe.pw", "animepahe.com", "animepahe.org"]
        # Remove duplicates while preserving order
        hosts = list(dict.fromkeys(hosts))

        for h in hosts:
            url = f"https://{h}/api?m=search&q={urllib.parse.quote(query)}"
            try:
                data = Solver.fetch_json(url)
                if data and "data" in data:
                    # Update host to the one that worked for future links
                    cls._current_host = h
                    return data.get("data", [])
            except Exception as exc:
                log.debug("Search failed on %s: %s", h, exc)
                continue
        return []

    _current_host: str = "animepahe.com"

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
            
            # Robust audio detection: check 'audio' field, fallback to title keywords
            audio = (item.get("audio") or "jpn").strip().lower()
            title = (item.get("title") or "").strip()
            if audio == "jpn" and "dub" in title.lower():
                audio = "eng"
            elif audio == "eng" and "sub" in title.lower():
                audio = "jpn"
                
            eps.append(EpisodeInfo(
                number   = float(item.get("episode", 0) or 0),
                session  = ep_sess,
                title    = ("" if title == "?" else title),
                fansub   = (item.get("fansub") or "").strip(),
                audio    = audio,
                play_url = f"https://{self.host}/play/{self.session}/{ep_sess}",
            ))
        return eps

    @classmethod
    def discover_all_sessions(cls, host: str, session: str) -> List[str]:
        """Query the release API for the anime title to find all variant session IDs."""
        # Fetch the metadata for the series using the provided session UUID
        url = f"https://{host}/api?m=release&id={session}&sort=episode_asc&page=1"
        data = Solver.fetch_json(url)
        if not data or "anime" not in data:
            return [session]
            
        # Extract the 'anime' object to get the title
        anime_data = data["anime"]
        title = anime_data.get("title", "")
        
        # Search for this title to find all variants (Sub/Dub)
        search_results = cls.search(host, title)
        
        # Collect unique session IDs from the search results
        sessions = {session}
        for res in search_results:
            if "session" in res:
                sessions.add(res["session"])
                
        return list(sessions)

    def scan(self, prefer_audio: str = "jpn") -> AnimeInfo:
        console.print("  [dim]Discovering all variants …[/dim]", end="\r")
        all_sessions = self.discover_all_sessions(self.host, self.session)
        
        title = self._fetch_title()
        anime = AnimeInfo(session=self.session, title=title, host=self.host)

        # Detect existing session
        safe_title = sanitize(title)
        session_path = CACHE_DIR / f"{safe_title}_{self.session}"
        anime.has_session = session_path.exists()

        # Deduplicate episodes by number AND audio to avoid phantom duplicates
        unique_episodes: Dict[Tuple[float, str], EpisodeInfo] = {}

        for s in all_sessions:
            console.print(f"  [dim]Scanning session {s} …[/dim]", end="\r")
            sub_scanner = AnimePaheScanner(self.host, s)
            
            # Fetch all pages for this session
            page = 1
            while True:
                data = sub_scanner._fetch_page(page)
                if not data or not data.get("data"):
                    break
                
                for ep in sub_scanner._parse_page(data):
                    # Key by (number, audio) to ensure uniqueness
                    key = (ep.number, ep.audio)
                    if key not in unique_episodes:
                        unique_episodes[key] = ep
                
                if page >= int(data.get("last_page", 1)):
                    break
                page += 1
                time.sleep(REQUEST_DELAY)

        anime.episodes = sorted(unique_episodes.values(), key=lambda e: (e.number, e.audio))
        anime.total = len(set(e.number for e in anime.episodes))

        console.print(" " * 60, end="\r")
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


def _print_ep_table(anime: AnimeInfo, episodes: List[EpisodeInfo], selected: Set[str]) -> None:
    t = Table(box=box.SIMPLE, show_header=True, header_style="bold cyan", padding=(0, 1))
    t.add_column("",      width=2, justify="center")
    t.add_column("Ep",    width=6, justify="right", style="dim")
    t.add_column("Title", style="white")

    # Track which episode numbers we've already displayed to avoid duplicates in the table
    seen_nums = set()
    for ep in episodes:
        if ep.number in seen_nums:
            continue
        seen_nums.add(ep.number)

        # Get all variants for this number to show the combined badge
        variants = anime.get_all_variants(ep.number)
        check = "[green]✓[/green]" if ep.session in selected else " "

        # If any variant of this number is selected, show the checkmark
        if any(v.session in selected for v in variants):
            check = "[green]✓[/green]"

        t.add_row(check, ep.ep_str, ep.title or "—")
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
        "  [cyan]Select mode[/cyan]",
        choices=["A", "a", "R", "r", "L", "l", "N", "n", "S", "s"],
        default="A",
        show_choices=False
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
        _print_ep_table(anime, anime.episodes, selected)
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
                # Toggle both variants if they exist, or just the number
                variants = anime.get_all_variants(num)
                if not variants:
                    continue

                # If any variant is selected, deselect all. Otherwise select all.
                if any(v.session in selected for v in variants):
                    for v in variants:
                        selected.discard(v.session)
                else:
                    for v in variants:
                        selected.add(v.session)

    # Filter selection: if an episode number has multiple audio variants,
    # the actual preference will be handled in the Downloader/Streamer.
    # For now, we return all selected session objects.
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


def interactive_discovery(host: str) -> Optional[str]:
    """Interactive search & selection loop. Returns a play/anime URL if selected."""
    while True:
        console.print()
        console.print(Rule("[bold white] Search & Discovery [/bold white]", style="cyan"))
        query = Prompt.ask("  [cyan]Search Anime[/cyan] [dim](or 'q' to quit)[/dim]").strip()

        if not query or query.lower() == 'q':
            return None

        with Progress(
            SpinnerColumn(),
            TextColumn(f"[bold white]Searching for '{query}'..."),
            console=console, transient=True,
        ) as prog:
            prog.add_task("", total=None)
            results = AnimePaheScanner.search(host, query)

        if not results:
            console.print(f"  [yellow]⚠ No results found for '[bold]{query}[/bold]'[/yellow]")
            continue

        table = Table(box=box.ROUNDED, header_style="bold cyan",
                      title=f"[bold white]Search Results: {query}[/bold white]")
        table.add_column("#",     justify="right", style="dim", width=4)
        table.add_column("Title", ratio=1)
        table.add_column("Type",  justify="center", width=8)
        table.add_column("Year",  justify="center", width=6)
        table.add_column("Eps",   justify="center", width=6)
        table.add_column("Score", justify="center", width=6)

        for i, res in enumerate(results, 1):
            table.add_row(
                str(i),
                res.get("title", "Unknown"),
                res.get("type", "-"),
                str(res.get("year", "-")),
                str(res.get("episodes", "-")),
                str(res.get("score", "-"))
            )

        console.print(table)

        choice = Prompt.ask(
            f"  [cyan]Select # (1-{len(results)})[/cyan] [dim](or 's' to search again, 'q' to quit)[/dim]",
            default="1"
        ).lower()

        if choice == 'q':
            return None
        if choice == 's':
            continue

        with contextlib.suppress(ValueError):
            idx = int(choice)
            if 1 <= idx <= len(results):
                target = results[idx-1]
                session = target.get("session")
                if session:
                    # Use the host that actually worked for search
                    return f"https://{AnimePaheScanner._current_host}/anime/{session}"

        console.print("  [red]⚠ Invalid selection.[/red]")


# ═════════════════════════════════════════════════════════════════════════
# 15.  PRE-DOWNLOAD CONFIRMATION PANEL
# ═════════════════════════════════════════════════════════════════════════

def _confirm_download(anime: AnimeInfo, episodes: List[EpisodeInfo], cfg: DownloadConfig) -> bool:
    """Show a summary panel and ask for confirmation (skipped in non-interactive mode)."""
    n          = len(episodes)
    ep_range   = compact_ep_range(episodes)

    # Calculate reused segments
    reused_count = 0
    if anime.has_session:
        for ep in episodes:
            store = SegmentStore(anime.title, anime.session, ep.ep_str)
            reused_count += len(store.done_indices())

    # Rough size estimate: 360p ~50 MB, 720p ~90 MB, 1080p ~150 MB
    est_mb_per = {360: 50, 720: 90, 1080: 150}.get(cfg.quality, 120)
    est_total  = n * est_mb_per

    # Calculate audio breakdown from actual episode data
    sub_n = sum(1 for ep in episodes if ep.audio == "jpn")
    dub_n = n - sub_n
    audio_str = ""
    if dub_n and sub_n:
        audio_str = f"[cyan]{sub_n}[/cyan] JPN + [yellow]{dub_n}[/yellow] DUB"
    elif dub_n:
        audio_str = f"[yellow]{dub_n} DUB[/yellow]"
    else:
        audio_str = f"[cyan]{sub_n} JPN[/cyan]"

    stats = [
        f"  [dim]Series:[/dim]    [bold white]{anime.title}[/bold white]",
        f"  [dim]Episodes:[/dim]  [cyan]{n}[/cyan]  ({ep_range})",
        f"  [dim]Audio:[/dim]     {audio_str}",
        f"  [dim]Quality:[/dim]   [cyan]{cfg.quality}p[/cyan]",
        f"  [dim]Output:[/dim]    {cfg.output_dir}",
    ]
    if reused_count > 0:
        stats.append(f"  [dim]Reusing:[/dim]   [bold green]{reused_count}[/bold green] segments from previous session")

    stats.append(f"  [dim]Est. size:[/dim] [cyan]~{est_total} MB[/cyan]  [dim](~{est_mb_per} MB/ep × {n} eps)[/dim]")

    console.print()
    console.print(Panel(
        "\n".join(stats),
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

async def run_stream(anime: AnimeInfo, chosen_episodes: List[EpisodeInfo], cfg: DownloadConfig) -> None:
    if not shutil.which("mpv"):
        console.print("\n  [red]✗ MPV not found![/red]")
        console.print("  [dim]Install MPV from https://mpv.io and ensure it is in your PATH.[/dim]")
        return

    console.print()
    console.print(Rule("[bold white] Streaming via MPV [/bold white]", style="cyan"))

    loop = asyncio.get_event_loop()
    all_eps = anime.episodes

    # Find starting index based on the first chosen episode
    idx = 0
    if chosen_episodes:
        first_sess = chosen_episodes[0].session
        for i, e in enumerate(all_eps):
            if e.session == first_sess:
                idx = i
                break

    def render_play_panel(ep: EpisodeInfo, state: str = "playing", choices_ui: str = "") -> Panel:
        # Detect if another audio variant exists for this episode
        variants = anime.get_all_variants(ep.number)
        other_audio = "eng" if ep.audio == "jpn" else "jpn"
        has_other = any(v.audio == other_audio for v in variants)

        audio_info = audio_badge(ep.audio)
        if has_other:
            other_label = "DUB" if other_audio == "eng" else "SUB"
            audio_info += f" [dim]([cyan]{other_label} available[/cyan])[/dim]"

        if state == "playing":
            content = Group(
                Text(anime.title, style="bold cyan underline"),
                Text.from_markup(f"Now Playing: {ep.label}", style="bold green"),
                Text.from_markup(f"Audio: {audio_info}", style="dim"),
                Text(f"Quality: {cfg.quality}p  ·  Episode {idx + 1} of {len(all_eps)}", style="dim"),
                Rule(style="dim", characters="─"),
                Text("Close MPV window to return to controls", style="italic cyan"),
            )
            title = "[bold cyan]Live Playback[/bold cyan]"
            border = "green"
        else:
            content = Group(
                Text(anime.title, style="bold cyan underline"),
                Text.from_markup(f"Finished: {ep.label}", style="dim"),
                Rule(style="dim", characters="─"),
                Text.from_markup(choices_ui, style="bold white"),
            )
            title = "[bold yellow]Playback Ended[/bold yellow]"
            border = "yellow"

        return Panel(
            Align.center(content),
            title=title,
            border_style=border, box=box.ROUNDED, padding=(1, 2),
        )

    while 0 <= idx < len(all_eps):
        ep = all_eps[idx]
        try:
            with Progress(
                SpinnerColumn(),
                TextColumn(f"[bold white]({idx + 1}/{len(all_eps)}) Resolving Ep {ep.ep_str}…"),
                console=console, transient=True,
            ) as prog:
                prog.add_task("", total=None)
                info = await loop.run_in_executor(
                    None, extract_stream, ep.play_url, cfg.quality, cfg.audio_lang,
                )

            # Update episode metadata from stream page (only if not already set or generic)
            if info.audio and (not ep.audio or ep.audio == "jpn"):
                ep.audio = info.audio
            if info.fansub: ep.fansub = info.fansub

            # Aggressive title cleaning
            if info.title and (not ep.title or "animepahe" in ep.title.lower() or "?" in ep.title):
                t = info.title
                t = re.sub(r"\s*[|·].*$", "", t).strip()
                t = re.sub(r"^Watch\s+.*?Episode\s+\d+.*", "", t, flags=re.I).strip()
                t = re.sub(r"\(1080p|720p|360p\).*", "", t, flags=re.I).strip()
                t = re.sub(r"\[SubsPlease\].*", "", t, flags=re.I).strip()
                t = re.sub(r"AnimePahe_", "", t, flags=re.I).strip()
                t = t.replace("_", " ").strip()
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

            with Live(render_play_panel(ep, "playing"), console=console, refresh_per_second=4) as live:
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.PIPE,
                )
                await proc.communicate()

                # Build playback controls
                valid_choices = ["r", "s", "q"]
                prompt_parts = []
                ui_options = []

                if idx > 0:
                    valid_choices.insert(0, "p")
                    prompt_parts.append("[bold](P)[/bold]rev")
                    ui_options.append("[cyan]P[/cyan]rev")

                if idx < len(all_eps) - 1:
                    valid_choices.insert(0, "n")
                    prompt_parts.append("[bold](N)[/bold]ext")
                    ui_options.append("[green]N[/green]ext")

                # Audio switch option if variant exists
                variants = anime.get_all_variants(ep.number)
                other_audio = "eng" if ep.audio == "jpn" else "jpn"
                has_other = any(v.audio == other_audio for v in variants)
                if has_other:
                    valid_choices.insert(0, "a")
                    prompt_parts.append("[bold](A)[/bold]udio")
                    ui_options.append("[cyan]A[/cyan]udio")

                ui_options += ["[yellow]R[/yellow]eplay", "[magenta]S[/magenta]elect", "[red]Q[/red]uit"]
                prompt_parts += ["[bold](R)[/bold]eplay", "[bold](S)[/bold]elect", "[bold](Q)[/bold]uit"]

                choices_ui = "  ·  ".join(ui_options)
                live.update(render_play_panel(ep, "ended", choices_ui))

            default = "n" if idx < len(all_eps) - 1 else "q"
            choice_label = "  [cyan]Action " + ", ".join(prompt_parts) + "[/cyan]"
            all_choices = valid_choices + [c.upper() for c in valid_choices]

            choice = Prompt.ask(choice_label, choices=all_choices, default=default, show_choices=False).lower()

            if choice == "n":   idx += 1
            elif choice == "p": idx -= 1
            elif choice == "r": continue
            elif choice == "q": break
            elif choice == "a":
                # Toggle audio
                other_audio = "eng" if ep.audio == "jpn" else "jpn"
                if variant := anime.get_variant(ep.number, other_audio):
                    # Update current episode with its variant
                    all_eps[idx] = variant
                    continue
            elif choice == "s":
                console.print()
                sel = Table(box=box.ROUNDED, header_style="bold cyan",
                            title=f"[bold white]Select Episode — {anime.title}[/bold white]")
                sel.add_column("#",     justify="right", style="dim", width=4)
                sel.add_column("Ep",    justify="right", width=6)
                sel.add_column("Title", ratio=1)

                for i, e in enumerate(all_eps):
                    style = "bold green" if i == idx else "dim"
                    pointer = "→ " if i == idx else "  "
                    sel.add_row(f"{pointer}{i+1}", e.ep_str, e.title or "—", style=style)

                console.print(sel)
                num = IntPrompt.ask(f"  [cyan]Jump to # (1-{len(all_eps)})[/cyan]", default=idx + 1)
                idx = max(0, min(num - 1, len(all_eps) - 1))

        except KeyboardInterrupt:
            break
        except Exception as exc:
            console.print(f"  [red]✗ Error:[/red] {exc}")
            if not Confirm.ask("  [cyan]Try next episode?[/cyan]", default=True):
                break

    console.print("\n  [yellow]Playback session ended.[/yellow]")


# ═════════════════════════════════════════════════════════════════════════
# 18.  SESSION MANAGER
# ═════════════════════════════════════════════════════════════════════════

class SessionManager:
    """Manages the pahe_cache library and legacy artifacts."""

    @staticmethod
    def get_sessions() -> List[Dict[str, Any]]:
        sessions = []
        if not CACHE_DIR.exists():
            return sessions

        for folder in CACHE_DIR.iterdir():
            if not folder.is_dir():
                continue
            meta_file = folder / "session.json"
            if not meta_file.exists():
                continue

            try:
                with open(meta_file, "r", encoding="utf-8") as f:
                    meta = json.load(f)

                # Count episodes and segments
                eps = [d for d in folder.iterdir() if d.is_dir() and d.name.startswith("Ep_")]
                segs = sum(len(list(d.glob("*.ts"))) for d in eps)
                size = sum(f.stat().st_size for f in folder.rglob("*"))

                sessions.append({
                    "path": folder,
                    "title": meta.get("title", folder.name),
                    "url": meta.get("url", ""),
                    "ep_count": len(eps),
                    "seg_count": segs,
                    "size": size,
                    "updated": meta.get("updated", folder.stat().st_mtime)
                })
            except Exception:
                continue
        return sorted(sessions, key=lambda x: x["updated"], reverse=True)

    @staticmethod
    def get_legacy_files() -> List[Path]:
        return list(Path(".").glob(".pahe_staging_*.db*")) + list(Path(".").glob("pahe_batcher.db*"))

    @classmethod
    def run(cls) -> Optional[str]:
        """Main loop for session manager. Returns a URL if 'Resume' is selected."""
        while True:
            console.clear()
            console.print(Rule("[bold white] Session & Cache Manager [/bold white]", style="cyan"))

            sessions = cls.get_sessions()
            legacy   = cls.get_legacy_files()
            total_size = sum(s["size"] for s in sessions) + sum(f.stat().st_size for f in legacy)

            if not sessions and not legacy:
                console.print("\n  [dim]No active sessions or cache found.[/dim]")
                Prompt.ask("\n  [cyan]Press Enter to return to menu[/cyan]")
                return None

            table = Table(box=box.ROUNDED, header_style="bold cyan", border_style="dim")
            table.add_column("#", justify="right", style="dim")
            table.add_column("Anime Title", ratio=1)
            table.add_column("Eps", justify="center")
            table.add_column("Segments", justify="center")
            table.add_column("Size", justify="right", style="green")
            table.add_column("Status", justify="center")

            for i, s in enumerate(sessions, 1):
                table.add_row(
                    str(i), s["title"], str(s["ep_count"]),
                    str(s["seg_count"]), fmt_bytes(s["size"]), "[yellow]Paused[/yellow]"
                )

            if legacy:
                table.add_row(
                    "L", "[red]Legacy Artifacts (.db files)[/red]", "-", "-",
                    fmt_bytes(sum(f.stat().st_size for f in legacy)), "[red]Obsolete[/red]"
                )

            console.print(table)
            console.print(f"  [dim]Total Cache Size:[/dim] [bold cyan]{fmt_bytes(total_size)}[/bold cyan]\n")

            choices = ["B", "b"]
            prompt_parts = []
            if sessions:
                choices += ["R", "r", "D", "d", "C", "c"]
                prompt_parts.append("[cyan][R]esume[/cyan]  [cyan][D]elete[/cyan]  [cyan][C]lear All[/cyan]")
            if legacy:
                choices += ["L", "l"]
                prompt_parts.append("[red][L]egacy Cleanup[/red]")

            prompt_parts.append("[white][B]ack[/white]")
            full_prompt = "  " + "  ".join(prompt_parts) + " > "

            choice = Prompt.ask(full_prompt, choices=choices, default="B", show_choices=False).upper()

            if choice == "B":
                return None

            if choice == "C":
                if Confirm.ask("  [red]Wipe entire cache folder?[/red]", default=False):
                    shutil.rmtree(CACHE_DIR, ignore_errors=True)
                    console.print("  [green]✓ Cache cleared.[/green]")
                    time.sleep(0.5)
                continue

            if choice == "L":
                if Confirm.ask(f"  [red]Delete {len(legacy)} legacy .db files?[/red]", default=True):
                    for f in legacy:
                        with contextlib.suppress(Exception): f.unlink()
                    console.print("  [green]✓ Legacy files cleaned.[/green]")
                    time.sleep(0.5)
                continue

            if choice in ("R", "D"):
                idx = IntPrompt.ask(f"  Select # to { 'Resume' if choice == 'R' else 'Delete' }", default=1)
                if 1 <= idx <= len(sessions):
                    target = sessions[idx-1]
                    if choice == "R":
                        return target["url"]
                    else:
                        if Confirm.ask(f"  [red]Delete session for '{target['title']}'?[/red]", default=True):
                            shutil.rmtree(target["path"], ignore_errors=True)
                            console.print(f"  [green]✓ Deleted '{target['title']}'.[/green]")
                            time.sleep(0.5)
                continue

# ═════════════════════════════════════════════════════════════════════════
# 19.  INTERACTIVE WIZARD
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
        Prompt.ask("  [cyan]Select quality[/cyan]", choices=["1", "2", "3"], default=_q_default, show_choices=False)
    )]

    # Audio
    _audio_default = "1" if defaults.audio_lang == "jpn" else "2"
    console.print(Panel(
        "  [bold white]1[/bold white]  [cyan]Subbed[/cyan]  [dim](Japanese audio)[/dim]\n"
        "  [bold white]2[/bold white]  [yellow]Dubbed[/yellow]  [dim](English audio)[/dim]",
        title="[cyan]Audio Language[/cyan]", border_style="dim cyan", box=box.ROUNDED, padding=(0, 2),
    ))
    audio_lang = "jpn" if Prompt.ask(
        "  [cyan]Select audio[/cyan]", choices=["1", "2"], default=_audio_default, show_choices=False
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
 ____       _            ____        _       _
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
        err_hint = f" ({_AIOHTTP_ERR})" if _AIOHTTP_ERR else ""
        console.print(
            f"  [yellow]⚠ aiohttp not installed{err_hint} — using urllib (slower)[/yellow]\n"
            "  [dim]Install for 3–5× faster downloads:  pip install aiohttp[/dim]"
        )

    # ── Discovery / URL Validation ────────────────────────────────────────
    url = args.url
    if not url:
        # Default host if none provided via URL
        url = interactive_discovery("animepahe.com")
        if not url:
            console.print("\n  [yellow]No anime selected. Exiting.[/yellow]")
            return

    try:
        host, session = parse_anime_url(url)
    except ValueError as exc:
        console.print(f"\n  [red]✗ Invalid URL:[/red] {exc}")
        sys.exit(1)

    # ── Scan series ───────────────────────────────────────────────────────
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
    badge = " [bold yellow][PARTIAL DOWNLOAD FOUND][/bold yellow]" if anime.has_session else ""
    console.print(
        f"  [green]✓[/green] [bold]{anime.title}[/bold]{badge}\n"
        f"  — [cyan]{len(anime.episodes)}[/cyan] episodes  "
        f"({compact_ep_range(anime.episodes)})  "
        f"[dim]{audio_info}[/dim]"
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

            # Calculate cache size for menu display
            sessions = SessionManager.get_sessions()
            legacy   = SessionManager.get_legacy_files()
            total_cache = sum(s["size"] for s in sessions) + sum(f.stat().st_size for f in legacy)
            cache_hint  = f" [dim]({fmt_bytes(total_cache)})[/dim]" if total_cache > 0 else ""

            console.print(Panel(
                "  [bold white]1[/bold white]  [cyan]Download[/cyan]  [dim]· save .mp4 files[/dim]\n"
                "  [bold white]2[/bold white]  [cyan]Export[/cyan]    [dim]· get M3U8 URLs + headers[/dim]\n"
                "  [bold white]3[/bold white]  [cyan]Stream[/cyan]    [dim]· play in MPV[/dim]\n"
                f"  [bold white]4[/bold white]  [cyan]Sessions & Cache[/cyan]{cache_hint}\n"
                "  [bold white]5[/bold white]  [cyan]List[/cyan]      [dim]· show episode table[/dim]\n"
                "  [bold white]6[/bold white]  [red]Exit[/red]",
                title=f"[bold cyan]{anime.title}[/bold cyan]",
                border_style="cyan", box=box.ROUNDED, padding=(0, 2),
            ))
            _default = "6" if _cached_cfg else "1"
            choice = Prompt.ask(
                "  [cyan]Select action[/cyan]",
                choices=["1", "2", "3", "4", "5", "6"],
                default=_default,
                show_choices=False
            )
            if choice == "6":
                break
            if choice == "5":
                t = Table(box=box.SIMPLE, show_header=True, header_style="bold cyan")
                t.add_column("Ep",    width=6, justify="right")
                t.add_column("Title", style="white")
                t.add_column("Audio", width=5)
                for ep in anime.episodes:
                    t.add_row(ep.ep_str, ep.title or "—", audio_badge(ep.audio))
                console.print(t)
                continue

            if choice == "4":
                new_url = SessionManager.run()
                if new_url and new_url != args.url:
                    # Restart main with new URL
                    args.url = new_url
                    return await _main(args)
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
            # Before starting stream, ensure we pick the episodes matching the initial preference
            # but keep the full series available for navigation.
            initial_episodes = [
                anime.get_variant(ep.number, args.audio_lang) or ep
                for ep in chosen
            ]
            await run_stream(anime, initial_episodes, cfg)
            if _scripted:
                break

        else:  # download
            # Confirmation summary (skipped in scripted mode)
            if not _scripted:
                if not _confirm_download(anime, chosen, cfg):
                    continue

            dl = BatchDownloader(anime, args.url, cfg)
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

    parser.add_argument("url", metavar="URL", nargs='?',
                        help="AnimePahe series URL (optional if using interactive search)")

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
    except (KeyboardInterrupt, asyncio.CancelledError):
        console.print("\n  [yellow]Interrupted.[/yellow]")
        Solver.destroy_session()
        sys.exit(0)
    except Exception as exc:
        console.print(f"\n  [red]✗ Fatal Error:[/red] {exc}")
        Solver.destroy_session()
        sys.exit(1)


if __name__ == "__main__":
    main()
