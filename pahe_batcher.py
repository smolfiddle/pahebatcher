#!/usr/bin/env python3
"""
pahe-batcher — AnimePahe Batch Downloader
==========================================
Parallel HLS segment engine · aiohttp-powered · Rich TUI

Usage (interactive wizard):
    python pahe_batcher.py https://animepahe.ru/anime/<uuid>

Usage (non-interactive / scripted):
    python pahe_batcher.py https://animepahe.ru/anime/<uuid> --all
    python pahe_batcher.py https://animepahe.ru/anime/<uuid> --range 1-12
    python pahe_batcher.py https://animepahe.ru/anime/<uuid> --latest 5

Requirements
------------
  pip install rich aiohttp
  ffmpeg in PATH               (HLS → MP4 remux; falls back to .ts)
  FlareSolverr running         (FLARESOLVERR_URL env var, default http://localhost:8191/v1)

Optional
--------
  pip install pycryptodomex    (AES-128 encrypted HLS streams)
"""

from __future__ import annotations

# ── Standard library ──────────────────────────────────────────────────────
import argparse
import asyncio
import atexit
import contextlib
import hashlib
import json
import logging
import math
import os
import queue
import re
import shutil
import sqlite3
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from collections import Counter, OrderedDict
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, Iterator, List, Literal, Optional, Set, Tuple

# ── SECURITY: HARDENED TLS ───────────────────────────────────────────────

def get_hardened_ssl_context() -> ssl.SSLContext:
    """
    Creates a strict SSL/TLS context:
    - TLS 1.2 or 1.3 only
    - Secure AEAD ciphers only (CRIME-resistant: no compression)
    - Strict certificate validation
    """
    ctx = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.options |= ssl.OP_NO_COMPRESSION
    ctx.set_ciphers(
        "ECDHE-ECDSA-AES128-GCM-SHA256:"
        "ECDHE-RSA-AES128-GCM-SHA256:"
        "ECDHE-ECDSA-AES256-GCM-SHA384:"
        "ECDHE-RSA-AES256-GCM-SHA384:"
        "ECDHE-ECDSA-CHACHA20-POLY1305:"
        "ECDHE-RSA-CHACHA20-POLY1305:"
        "DHE-RSA-AES128-GCM-SHA256:"
        "DHE-RSA-AES256-GCM-SHA384"
    )
    ctx.verify_mode = ssl.CERT_REQUIRED
    ctx.check_hostname = True
    return ctx


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

VERSION             = "1.3.0"
HLS_WORKERS         = 16        # parallel HLS segment fetches per episode
DB_TIMEOUT          = 120.0
RETRY_ATTEMPTS      = 5
RETRY_BASE_DELAY    = 0.75
FLARESOLVERR_URL    = os.getenv("FLARESOLVERR_URL", "http://localhost:8191/v1")

EPISODES_PER_PAGE   = 30
REQUEST_DELAY       = 0.4
DB_POOL_SIZE        = 8

# Rough estimate for total-bytes display before real sizes arrive.
# One TS packet = 188 bytes; a typical segment ≈ 256 packets.
_TS_SEGMENT_HINT_BYTES = 188 * 256

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
    resolutions: Dict[int, str] = field(default_factory=dict)  # quality → Kwik URL

    @property
    def label(self) -> str:
        num = int(self.number) if self.number == int(self.number) else self.number
        dub = "  [yellow]DUB[/yellow]" if self.audio and self.audio != "jpn" else ""
        return f"Ep [cyan]{num:>4}[/cyan]  {self.title or '—'}{dub}"

    @property
    def ep_str(self) -> str:
        return str(int(self.number)) if self.number == int(self.number) else str(self.number)


@dataclass
class AnimeInfo:
    session:  str
    title:    str
    host:     str
    total:    int               = 0
    episodes: List[EpisodeInfo] = field(default_factory=list)


@dataclass
class DownloadConfig:
    output_dir:     str  = "./downloads"
    max_parallel:   int  = 2
    hls_workers:    int  = HLS_WORKERS
    purge_db:       bool = True
    quality:        int  = 1080
    export_mode:    bool = False
    stream_mode:    bool = False
    audio_lang:     str  = "jpn"   # "jpn" = subbed, "eng" = dubbed
    fallback_audio: bool = True    # if preferred lang unavailable, use the other


# ═════════════════════════════════════════════════════════════════════════
# 2.  SHARED UTILITIES
# ═════════════════════════════════════════════════════════════════════════

def format_cookies(cookies: List[dict]) -> str:
    """Format a list of cookie dicts into a Cookie header string."""
    return "; ".join(f"{c['name']}={c['value']}" for c in cookies)


def _sanitize_filename(name: str) -> str:
    """Strip characters unsafe for filenames and collapse repeated underscores."""
    safe = re.sub(r"[^\w\s\-.]", "", name).strip().replace(" ", "_")
    return re.sub(r"_+", "_", safe)


def _make_ep_prefix(ep_num: str) -> str:
    """
    Convert an episode number string to a zero-padded sortable prefix.
    '5'   → '005'
    '5.5' → '005.5'
    """
    try:
        return f"{float(ep_num):05.1f}" if "." in ep_num else f"{int(ep_num):03d}"
    except (ValueError, TypeError):
        return ep_num


def _audio_display(audio: str) -> str:
    """Return a rich-styled badge for the audio language."""
    if audio == "eng":
        return "[yellow]DUB[/yellow]"
    elif audio == "jpn":
        return "[dim]JPN[/dim]"
    return f"[cyan]{audio.upper()}[/cyan]"


# ═════════════════════════════════════════════════════════════════════════
# 3.  LRU CACHE
# ═════════════════════════════════════════════════════════════════════════

class LRUCache:
    """Thread-safe LRU cache with per-entry TTL expiry."""

    def __init__(self, max_size: int = 512, ttl: float = 60.0) -> None:
        self.max_size = max_size
        self.ttl      = ttl
        self._cache: OrderedDict = OrderedDict()
        self._lock  = threading.Lock()

    def get(self, key: str) -> Any:
        with self._lock:
            if key in self._cache:
                ts, value = self._cache[key]
                if time.monotonic() - ts < self.ttl:
                    self._cache.move_to_end(key)
                    return value
                del self._cache[key]
        return None

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            if key in self._cache:
                del self._cache[key]
            elif len(self._cache) >= self.max_size:
                self._cache.popitem(last=False)
            self._cache[key] = (time.monotonic(), value)

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()


# ═════════════════════════════════════════════════════════════════════════
# 4.  ADAPTIVE COMPRESSOR
# ═════════════════════════════════════════════════════════════════════════

class AdaptiveCompressor:
    """
    Entropy-adaptive compression for stored HLS segments.

    High-entropy data (already encrypted/compressed TS) is stored raw.
    Low-entropy data (black frames, silent audio, subtitle segments) is
    compressed with zlib before being written to SQLite, saving disk space.
    """

    ENTROPY_HIGH = 7.5
    MIN_RATIO    = 1.08
    SAMPLE_SIZE  = 2048

    @classmethod
    def _entropy(cls, data: bytes) -> float:
        n = len(data)
        if n == 0:
            return 0.0
        if n <= cls.SAMPLE_SIZE:
            sample = data
        else:
            third  = cls.SAMPLE_SIZE // 3
            mid    = n // 2
            sample = data[:third] + data[mid:mid + third] + data[-third:]
        sample_len = len(sample)
        entropy    = 0.0
        for count in Counter(sample).values():
            p = count / sample_len
            entropy -= p * math.log2(p)
        return entropy

    @classmethod
    def compress(cls, data: bytes) -> Tuple[bytes, bool]:
        if cls._entropy(data) >= cls.ENTROPY_HIGH:
            return data, False
        compressed = zlib.compress(data, level=1)
        if len(data) / len(compressed) >= cls.MIN_RATIO:
            return compressed, True
        return data, False

    @classmethod
    def decompress(cls, data: bytes, was_compressed: bool) -> bytes:
        return zlib.decompress(data) if was_compressed else data


# ═════════════════════════════════════════════════════════════════════════
# 5.  SQLite CONNECTION POOL
# ═════════════════════════════════════════════════════════════════════════

class ConnectionPool:
    """Bounded pool of WAL-mode SQLite connections."""

    def __init__(self, db_path: str, pool_size: int = DB_POOL_SIZE) -> None:
        self.db_path  = db_path
        self._pool: queue.Queue[sqlite3.Connection] = queue.Queue(maxsize=pool_size)
        for _ in range(pool_size):
            self._pool.put(self._make())

    def _make(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=DB_TIMEOUT, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous  = NORMAL")
        conn.execute("PRAGMA cache_size   = -32768")
        conn.execute("PRAGMA temp_store   = MEMORY")
        conn.execute("PRAGMA mmap_size    = 268435456")
        return conn

    def get(self) -> sqlite3.Connection:
        try:
            conn = self._pool.get_nowait()
            conn.execute("SELECT 1")   # liveness probe
            return conn
        except queue.Empty:
            log.debug("Connection pool exhausted — creating overflow connection.")
            return self._make()
        except sqlite3.Error:
            log.debug("Pool connection unhealthy — replacing.")
            return self._make()

    def put(self, conn: sqlite3.Connection) -> None:
        try:
            self._pool.put_nowait(conn)
        except queue.Full:
            with contextlib.suppress(Exception):
                conn.close()

    def close_all(self) -> None:
        while not self._pool.empty():
            with contextlib.suppress(Exception):
                self._pool.get_nowait().close()


# ═════════════════════════════════════════════════════════════════════════
# 6.  DATABASE LAYER  (hash-addressed chunks with deduplication)
# ═════════════════════════════════════════════════════════════════════════

def _blake2b(data: bytes) -> str:
    return hashlib.blake2b(data, digest_size=32).hexdigest()


_SCHEMA = """
CREATE TABLE IF NOT EXISTS assets (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    source_url  TEXT    NOT NULL,
    title       TEXT,
    status      TEXT    NOT NULL DEFAULT 'pending',
    total_bytes INTEGER,
    ep_number   TEXT,
    created_at  INTEGER NOT NULL,
    updated_at  INTEGER NOT NULL
);

-- Content-addressed chunk store.  Identical HLS segments (shared OP/ED/eyecatch)
-- are stored ONCE regardless of how many episodes reference them.
CREATE TABLE IF NOT EXISTS chunks (
    hash        TEXT    PRIMARY KEY,
    data        BLOB    NOT NULL,
    compressed  INTEGER NOT NULL DEFAULT 0,
    orig_len    INTEGER NOT NULL
) WITHOUT ROWID;

-- Ordered per-asset chunk references.
CREATE TABLE IF NOT EXISTS asset_chunks (
    asset_id    INTEGER NOT NULL,
    seq_idx     INTEGER NOT NULL,
    chunk_hash  TEXT    NOT NULL REFERENCES chunks(hash),
    PRIMARY KEY (asset_id, seq_idx)
) WITHOUT ROWID;

CREATE INDEX IF NOT EXISTS idx_asset_chunks_hash ON asset_chunks(chunk_hash);

CREATE TABLE IF NOT EXISTS meta (
    asset_id    INTEGER NOT NULL,
    key         TEXT    NOT NULL,
    value       BLOB    NOT NULL,
    PRIMARY KEY (asset_id, key)
) WITHOUT ROWID;
"""


class VaultDB:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self.pool    = ConnectionPool(db_path)
        self._init_schema()

    def _init_schema(self) -> None:
        conn = self.pool.get()
        try:
            conn.execute("PRAGMA auto_vacuum = FULL")
            conn.executescript(_SCHEMA)
            # Schema migration: add ep_number column if absent (older DBs).
            cols = {r["name"] for r in conn.execute("PRAGMA table_info(assets)").fetchall()}
            if "ep_number" not in cols:
                conn.execute("ALTER TABLE assets ADD COLUMN ep_number TEXT")
            conn.commit()
        except Exception as exc:
            conn.rollback()
            log.error("Database init failed: %s", exc)
            raise
        finally:
            self.pool.put(conn)

    def close(self) -> None:
        conn = self.pool.get()
        try:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        finally:
            conn.close()
        self.pool.close_all()


class AssetManager:
    def __init__(self, db: VaultDB) -> None:
        self.db = db

    # ── Chunk I/O ─────────────────────────────────────────────────────────

    def store_single_chunk(self, asset_id: int, seq_idx: int, raw_data: bytes) -> int:
        """
        Store one chunk and immediately link it to the asset.
        Constant memory usage regardless of episode length.
        Returns the uncompressed byte count.
        """
        h             = _blake2b(raw_data)
        stored, compr = AdaptiveCompressor.compress(raw_data)
        conn          = self.db.pool.get()
        try:
            conn.execute("BEGIN")
            conn.execute(
                "INSERT OR IGNORE INTO chunks (hash, data, compressed, orig_len) VALUES (?,?,?,?)",
                (h, stored, int(compr), len(raw_data)),
            )
            conn.execute(
                "INSERT OR REPLACE INTO asset_chunks (asset_id, seq_idx, chunk_hash) VALUES (?,?,?)",
                (asset_id, seq_idx, h),
            )
            conn.commit()
            return len(raw_data)
        except Exception:
            conn.rollback()
            raise
        finally:
            self.db.pool.put(conn)

    def get_total_bytes(self, asset_id: int) -> int:
        """Sum of original (uncompressed) byte lengths for all chunks in this asset."""
        conn = self.db.pool.get()
        try:
            row = conn.execute(
                "SELECT SUM(c.orig_len) "
                "FROM chunks c JOIN asset_chunks ac ON c.hash = ac.chunk_hash "
                "WHERE ac.asset_id = ?",
                (asset_id,),
            ).fetchone()
            return row[0] or 0
        finally:
            self.db.pool.put(conn)

    def iter_chunks(self, asset_id: int) -> Iterator[bytes]:
        conn = self.db.pool.get()
        try:
            rows = conn.execute(
                "SELECT c.data, c.compressed "
                "FROM asset_chunks ac JOIN chunks c ON ac.chunk_hash = c.hash "
                "WHERE ac.asset_id = ? ORDER BY ac.seq_idx",
                (asset_id,),
            ).fetchall()
        finally:
            self.db.pool.put(conn)

        for data, compressed in rows:
            yield AdaptiveCompressor.decompress(data, bool(compressed))

    def get_completed_segments(self, asset_id: int) -> Set[int]:
        """Sequence indices already persisted for this asset (used for resuming)."""
        conn = self.db.pool.get()
        try:
            cur = conn.execute(
                "SELECT seq_idx FROM asset_chunks WHERE asset_id = ?", (asset_id,)
            )
            return {r[0] for r in cur.fetchall()}
        finally:
            self.db.pool.put(conn)

    def delete_asset_chunks(self, asset_id: int) -> None:
        """
        Remove an asset's chunks and metadata, preserving chunks referenced
        by other assets (dedup-safe).
        """
        conn = self.db.pool.get()
        try:
            conn.execute("BEGIN")
            conn.execute("DELETE FROM asset_chunks WHERE asset_id=?", (asset_id,))
            conn.execute("DELETE FROM meta WHERE asset_id=?", (asset_id,))
            conn.execute(
                "DELETE FROM chunks "
                "WHERE hash NOT IN (SELECT DISTINCT chunk_hash FROM asset_chunks)"
            )
            conn.commit()
        except Exception as exc:
            conn.rollback()
            log.debug("Cleanup for asset %d failed: %s", asset_id, exc)
        finally:
            self.db.pool.put(conn)

    # ── Asset CRUD ────────────────────────────────────────────────────────

    def add_asset(self, url: str, title: str = "", ep_number: str = "") -> int:
        now  = int(time.time())
        conn = self.db.pool.get()
        try:
            cur = conn.execute(
                "INSERT INTO assets "
                "(source_url, title, ep_number, status, created_at, updated_at) "
                "VALUES (?, ?, ?, 'pending', ?, ?)",
                (url, title or None, ep_number or None, now, now),
            )
            conn.commit()
            return cur.lastrowid
        finally:
            self.db.pool.put(conn)

    def get_asset(self, asset_id: int) -> Optional[sqlite3.Row]:
        conn = self.db.pool.get()
        try:
            return conn.execute(
                "SELECT * FROM assets WHERE id=?", (asset_id,)
            ).fetchone()
        finally:
            self.db.pool.put(conn)

    def update_status(self, asset_id: int, status: str, **kwargs: Any) -> None:
        sets   = [f"{k}=?" for k in kwargs] + ["status=?", "updated_at=?"]
        values = list(kwargs.values()) + [status, int(time.time()), asset_id]
        conn   = self.db.pool.get()
        try:
            conn.execute(f"UPDATE assets SET {', '.join(sets)} WHERE id=?", values)
            conn.commit()
        finally:
            self.db.pool.put(conn)

    # ── Metadata ──────────────────────────────────────────────────────────

    def store_meta(self, asset_id: int, key: str, value: str) -> None:
        conn = self.db.pool.get()
        try:
            conn.execute(
                "INSERT OR REPLACE INTO meta (asset_id, key, value) VALUES (?,?,?)",
                (asset_id, key, value.encode() if isinstance(value, str) else value),
            )
            conn.commit()
        finally:
            self.db.pool.put(conn)

    def get_meta(self, asset_id: int, key: str) -> Optional[str]:
        conn = self.db.pool.get()
        try:
            row = conn.execute(
                "SELECT value FROM meta WHERE asset_id=? AND key=?",
                (asset_id, key),
            ).fetchone()
        finally:
            self.db.pool.put(conn)
        if row:
            v = row[0]
            return v.decode() if isinstance(v, bytes) else str(v)
        return None

    # ── Export ────────────────────────────────────────────────────────────

    def export_to_file(self, asset_id: int, output_dir: str = ".") -> Optional[str]:
        asset = self.get_asset(asset_id)
        if not asset:
            return None

        ep_num     = asset["ep_number"]
        title      = asset["title"] or f"asset_{asset_id}"
        prefix     = _make_ep_prefix(ep_num) if ep_num else ""
        full_title = f"Ep {prefix} - {title}" if prefix else title
        base       = _sanitize_filename(full_title) or f"asset_{asset_id}"

        # Path-traversal guard: ensure output stays inside output_dir.
        out_dir     = Path(output_dir).resolve()
        out_dir.mkdir(parents=True, exist_ok=True)
        target_path = (out_dir / f"{base}.mp4").resolve()
        if not str(target_path).startswith(str(out_dir)):
            log.error("Path traversal attempt blocked: %s", target_path)
            return None

        chunks = list(self.iter_chunks(asset_id))
        if not chunks:
            log.error("No chunks for asset %d", asset_id)
            return None

        if self.get_meta(asset_id, "type") == "hls":
            return self._export_hls(chunks, target_path)

        with open(target_path, "wb") as fh:
            for chunk in chunks:
                fh.write(chunk)
        return str(target_path)

    def _export_hls(self, chunks: List[bytes], target_path: Path) -> Optional[str]:
        tmp_ts: Optional[str] = None
        try:
            fd, tmp_ts = tempfile.mkstemp(suffix=".ts", prefix="pb_")
            with os.fdopen(fd, "wb") as f:
                for chunk in chunks:
                    f.write(chunk)

            result = subprocess.run(
                ["ffmpeg", "-y", "-i", tmp_ts, "-c", "copy", "-movflags", "+faststart",
                 str(target_path)],
                capture_output=True, timeout=600,
            )
            if result.returncode == 0:
                return str(target_path)

            stderr = result.stderr.decode(errors="replace").strip()
            console.print(f"  [yellow]⚠ ffmpeg failed — saving .ts[/yellow]\n  [dim]{stderr[-200:]}[/dim]")

        except FileNotFoundError:
            console.print("  [yellow]⚠ ffmpeg not found — saving .ts instead[/yellow]")
        except subprocess.TimeoutExpired:
            console.print("  [yellow]⚠ ffmpeg timed out — saving .ts instead[/yellow]")
        except Exception as exc:
            console.print(f"  [red]✗ Export error: {exc}[/red]")
        finally:
            if tmp_ts:
                with contextlib.suppress(OSError):
                    os.unlink(tmp_ts)

        out_ts = target_path.with_suffix(".ts")
        with open(out_ts, "wb") as f:
            for chunk in chunks:
                f.write(chunk)
        return str(out_ts)


# ═════════════════════════════════════════════════════════════════════════
# 7.  FLARESOLVERR CLIENT  (session lifecycle management + retry + cache)
# ═════════════════════════════════════════════════════════════════════════

_solver_cache  = LRUCache(max_size=256, ttl=120.0)
_aes_key_cache = LRUCache(max_size=128, ttl=3600.0)


class Solver:
    """
    FlareSolverr wrapper with session reuse, retry, LRU cache,
    and guaranteed session teardown on exit.
    """

    _session_id: Optional[str] = None
    _lock         = threading.Lock()
    _request_sem  = threading.Semaphore(1)  # one browser action at a time

    # ── Session lifecycle ─────────────────────────────────────────────────

    @classmethod
    def _init_session(cls) -> None:
        try:
            req = urllib.request.Request(
                FLARESOLVERR_URL,
                data=json.dumps({"cmd": "sessions.create"}).encode(),
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.load(resp)
                if data.get("status") == "ok":
                    cls._session_id = data.get("session")
                    log.info("FlareSolverr session created: %s", cls._session_id)
        except Exception as exc:
            log.debug("FlareSolverr session create failed: %s", exc)

    @classmethod
    def destroy_session(cls) -> None:
        """
        Destroy the active Chromium session inside FlareSolverr.
        Terminates the browser process and frees RAM without stopping
        the FlareSolverr container itself.
        """
        with cls._lock:
            sid = cls._session_id
            if not sid:
                return
            cls._session_id = None

        try:
            req = urllib.request.Request(
                FLARESOLVERR_URL,
                data=json.dumps({"cmd": "sessions.destroy", "session": sid}).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.load(resp)
                if data.get("status") == "ok":
                    log.info("FlareSolverr session %s destroyed.", sid)
                else:
                    log.warning("FlareSolverr destroy: %s", data.get("message"))
        except Exception as exc:
            log.debug("FlareSolverr session destroy failed (harmless): %s", exc)

    # ── Request ───────────────────────────────────────────────────────────

    @classmethod
    def request(cls, url: str, use_cache: bool = True) -> Optional[Dict]:
        if use_cache and (cached := _solver_cache.get(url)) is not None:
            return cached

        with cls._request_sem:
            with cls._lock:
                if not cls._session_id:
                    cls._init_session()

            for attempt in range(RETRY_ATTEMPTS):
                try:
                    body: Dict[str, Any] = {
                        "cmd": "request.get",
                        "url": url,
                        "maxTimeout": 60000,
                        "wait": 2000,
                    }
                    if cls._session_id:
                        body["session"] = cls._session_id

                    req = urllib.request.Request(
                        FLARESOLVERR_URL,
                        data=json.dumps(body).encode(),
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    with urllib.request.urlopen(req, timeout=90) as resp:
                        data = json.load(resp)

                    if data.get("status") == "ok":
                        sol = data.get("solution")
                        if sol and use_cache:
                            _solver_cache.set(url, sol)
                        return sol

                    msg = data.get("message", "")
                    log.warning("FlareSolverr: %s", msg)

                    if "Error: Error: Session" in msg or "not found" in msg.lower():
                        with cls._lock:
                            cls._session_id = None
                        if attempt < RETRY_ATTEMPTS - 1:
                            with cls._lock:
                                cls._init_session()
                            continue
                    break

                except urllib.error.URLError as exc:
                    log.error("FlareSolverr connection error (attempt %d): %s", attempt + 1, exc)
                    if attempt < RETRY_ATTEMPTS - 1:
                        time.sleep(RETRY_BASE_DELAY * (2 ** attempt))
                except Exception as exc:
                    log.error("FlareSolverr unexpected error: %s", exc)
                    break

        return None

    # ── JSON / HTML helpers ───────────────────────────────────────────────

    @classmethod
    def fetch_json(cls, url: str) -> Optional[Dict]:
        sol  = cls.request(url)
        body = sol.get("response", "") if sol else ""
        if not body:
            return None

        # Strategy 1: Chromium renders raw JSON inside a <pre> tag.
        for pre_m in re.finditer(r'<pre[^>]*>([\s\S]*?)</pre>', body, re.I):
            text = (pre_m.group(1).strip()
                    .replace('&amp;', '&').replace('&lt;', '<')
                    .replace('&gt;', '>').replace('&quot;', '"')
                    .replace('&#39;', "'"))
            with contextlib.suppress(json.JSONDecodeError):
                return json.loads(text)

        # Strategy 2: Strip all HTML tags.
        with contextlib.suppress(json.JSONDecodeError):
            return json.loads(re.sub(r'<[^>]+>', '', body).strip())

        # Strategy 3: Walk character-by-character to find the first JSON object.
        start = body.find('{')
        if start != -1:
            depth  = 0
            in_str = False
            escape = False
            for i, ch in enumerate(body[start:], start):
                if escape:
                    escape = False
                    continue
                if ch == '\\' and in_str:
                    escape = True
                    continue
                if ch == '"':
                    in_str = not in_str
                    continue
                if in_str:
                    continue
                if ch == '{':
                    depth += 1
                elif ch == '}':
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


# Register guaranteed teardown on any exit (normal, exception, Ctrl-C).
atexit.register(Solver.destroy_session)


# ═════════════════════════════════════════════════════════════════════════
# 8.  KWIK / ANIMEPAHE EXTRACTION
# ═════════════════════════════════════════════════════════════════════════

_KWIK_DOMAINS = r"kwik\.(?:si|cx|pw|gg|me|net|to|in|cc)"
_KWIK_RE      = re.compile(rf"https?://(?:{_KWIK_DOMAINS})/(?:e|f)/\w+", re.IGNORECASE)


class JsPacker:
    """Decode eval-based JS packer (p,a,c,k,e,d pattern used by Kwik)."""

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
        mapping     = mapping_s.split("|")
        digits      = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"

        def enc(c: int) -> str:
            return digits[c] if c < base else enc(c // base) + digits[c % base]

        lookup = {
            enc(i): (mapping[i] if i < len(mapping) and mapping[i] else enc(i))
            for i in range(count)
        }
        return re.sub(r"\b\w+\b", lambda mo: lookup.get(mo.group(0), mo.group(0)), payload)


def _extract_m3u8_from_kwik_html(html: str) -> Optional[str]:
    """Three-strategy M3U8 URL extraction from a Kwik HTML page."""
    # Strategy 1: bare m3u8 URL in HTML
    m = re.search(r"(https?://[^\s'\"\\>]+(?:uwu\.m3u8|\.m3u8)[^\s'\"\\>]*)", html)
    if m:
        return m.group(1).replace("\\/", "/").rstrip("\\")

    # Strategy 2: JS packer (iterative unpack)
    scripts = re.findall(r"<script[^>]*>([\s\S]*?)</script>", html, re.IGNORECASE)
    for script in sorted(scripts, key=len, reverse=True):
        current = script
        for _ in range(6):
            inner = re.search(r'eval\s*\(\s*"((?:[^"\\]|\\.)*)"\s*\)', current)
            if inner:
                current = inner.group(1).encode().decode("unicode_escape", errors="ignore")
                continue
            unpacked = JsPacker.unpack(current)
            if unpacked != current:
                current = unpacked
                continue
            break
        m = re.search(r"(https?://[^\s'\"\\>]+(?:uwu\.m3u8|\.m3u8)[^\s'\"\\>]*)", current)
        if m:
            return m.group(1).replace("\\/", "/").rstrip("\\")

    # Strategy 3: <source> element
    m = re.search(r'<source[^>]+src=["\']([^"\']+\.m3u8[^"\']*)["\']', html, re.IGNORECASE)
    if m:
        return m.group(1)

    return None


def _find_kwik_links(html: str) -> List[str]:
    """
    Locate Kwik embed URLs from an AnimePahe episode page HTML.
    Returns a de-duplicated list in priority order.
    """
    # Priority 1: redirect-class links (most reliable)
    redirects = re.findall(
        rf'href=["\'](https?://{_KWIK_DOMAINS}/[ef]/\w+)["\'][^>]+class=["\'][^"\']*redirect[^"\']*["\']',
        html, re.I,
    )
    if redirects:
        return list(dict.fromkeys(redirects))

    # Priority 2: data-src within a player container
    player_box = re.search(
        r'<(?:div|section)[^>]+(?:id|class)=["\'](?:video-)?player["\'][^>]*>(.*?)</(?:div|section)>',
        html, re.I | re.S,
    )
    if player_box:
        data_srcs = re.findall(
            rf'data-src=["\'](https?://{_KWIK_DOMAINS}/[ef]/\w+)["\']',
            player_box.group(1), re.I,
        )
        if data_srcs:
            return list(dict.fromkeys(data_srcs))

    # Priority 3: any data-src with a kwik domain
    return list(dict.fromkeys(re.findall(
        rf'data-src=["\'](https?://{_KWIK_DOMAINS}/[ef]/\w+)["\']', html, re.I,
    )))


def _resolve_one_kwik(kwik_url: str) -> Optional[Dict]:
    sol = Solver.request(kwik_url, use_cache=False)
    if not sol or "response" not in sol:
        return None

    html       = sol["response"]
    cookies    = sol.get("cookies", [])
    user_agent = sol.get("userAgent", "Mozilla/5.0")

    direct    = re.search(r'(https?://[^\s"\']+\.(?:m3u8|mp4)[^\s"\']*)', html)
    video_url = direct.group(1) if direct else _extract_m3u8_from_kwik_html(html)

    if video_url:
        return {"url": video_url, "cookies": cookies, "user_agent": user_agent, "referer": kwik_url}
    return None


def _has_dub_attribute(tag_attrs: str) -> bool:
    """Check if an HTML tag's attributes contain data-audio="eng"."""
    return bool(re.search(r'''data-audio\s*=\s*["']eng["']''', tag_attrs, re.I))


def _parse_resolution_buttons(html: str) -> List[Tuple[int, str, bool, str]]:
    """
    Extract quality options from <button> tags inside the resolution menu.
    Returns a list of (resolution, kwik_url, is_dub, fansub_group).
    """
    entries: List[Tuple[int, str, bool, str]] = []

    # Look for the resolution menu container; it's typically a div with id "resolutionMenu"
    menu_match = re.search(
        r'<div[^>]+id=["\']resolutionMenu["\'][^>]*>(.*?)</div>', html, re.I | re.S
    )
    if not menu_match:
        return entries

    menu_html = menu_match.group(1)

    # Find all buttons inside the menu
    for btn_match in re.finditer(r'<button\b([^>]*?)>(.*?)</button>', menu_html, re.I | re.S):
        attrs = btn_match.group(1)
        text = btn_match.group(2).strip()

        src_m = re.search(r'data-src=["\']([^"\']+kwik\.[^"\']+)["\']', attrs, re.I)
        if not src_m:
            continue
        kwik_url = src_m.group(1)

        # 1) Try data-resolution attribute first (most reliable)
        res_m = re.search(r'data-resolution=["\']?(\d+)["\']?', attrs, re.I)
        if res_m:
            resolution = int(res_m.group(1))
        else:
            # 2) Fallback: parse resolution from button text (e.g. "1080p")
            res_m = re.match(r'(\d+)\s*p', text, re.I)
            if not res_m:
                continue
            resolution = int(res_m.group(1))

        is_dub = _has_dub_attribute(attrs)

        # Extract fansub group
        fansub_m = re.search(r'data-fansub=["\']([^"\']+)["\']', attrs, re.I)
        if fansub_m:
            fansub = fansub_m.group(1)
        else:
            parts = text.split("·")
            fansub = parts[0].strip() if parts else ""

        entries.append((resolution, kwik_url, is_dub, fansub))

    return entries


def extract_animepahe_stream(
    episode_url: str,
    preferred_quality: int = 1080,
    prefer_audio: str = "jpn",
) -> Dict[str, Any]:
    """
    Resolve an AnimePahe play-page URL to a streamable M3U8.

    Quality selection:
    - Prefers the highest quality ≤ preferred_quality.
    - Falls back to the lowest available if every option exceeds the preference.

    Audio selection:
    - When `prefer_audio` is "jpn", dub (data-audio="eng") links are excluded.
    - When `prefer_audio` is "eng", only dub links are kept.
    - If no links match the preference after filtering, all links are used
      (graceful fallback).
    """
    sol = Solver.request(episode_url, use_cache=True)
    if not sol or "response" not in sol:
        raise RuntimeError("FlareSolverr failed to fetch episode page")

    html = sol["response"]

    # ── Parse resolution buttons ─────────────────────────────────────────
    entries = _parse_resolution_buttons(html)  # (res, url, is_dub, fansub)

    # ── Fallback: resolution embedded in link text ────────────────────────
    if not entries:
        quality_map: Dict[int, Tuple[str, bool, str]] = {}
        for kwik_url, q_str in re.findall(
            r'(?:href|data-src)=["\']([^"\']*kwik\.[^"\']+)["\'][^>]*>\s*(?:\S+\s+)?(\d+)p',
            html, re.I,
        ):
            with contextlib.suppress(ValueError):
                quality_map[int(q_str)] = (kwik_url, False, "")
    else:
        # ── Audio filtering ───────────────────────────────────────────────
        if prefer_audio == "jpn":
            filtered_entries = [e for e in entries if not e[2]]  # not dub
        else:  # "eng"
            filtered_entries = [e for e in entries if e[2]]      # is dub

        if not filtered_entries:
            log.warning(
                "Audio filter (%s) would remove all links — using all available.",
                prefer_audio,
            )
            filtered_entries = entries

        # Build final quality map (resolution → (url, is_dub, fansub))
        quality_map = {}
        for resolution, url, is_dub, fansub in filtered_entries:
            if resolution not in quality_map:
                quality_map[resolution] = (url, is_dub, fansub)
            # If duplicate resolutions exist after filtering, we keep the first
            # (subbed entries appear before dubbed in the menu).

    chosen_kwik: Optional[str] = None
    is_dub:      bool          = False
    fansub:      str           = ""

    if quality_map:
        sorted_quals = sorted(quality_map.keys(), reverse=True)
        for q in sorted_quals:
            if q <= preferred_quality:
                chosen_kwik, is_dub, fansub = quality_map[q]
                break
        if not chosen_kwik:
            chosen_kwik, is_dub, fansub = quality_map[sorted_quals[-1]]
        log.debug(
            "Quality map: %s  |  preferred: %dp  |  chosen: %dp  |  audio: %s",
            sorted(quality_map), preferred_quality,
            next(q for q, v in quality_map.items() if v[0] == chosen_kwik),
            prefer_audio,
        )
    else:
        # Method 3: generic Kwik-link scan (no resolution metadata)
        kwik_links = _find_kwik_links(html)
        if not kwik_links:
            raise RuntimeError("No Kwik link found on episode page")
        chosen_kwik = kwik_links[0]
        log.debug("Quality metadata absent; falling back to first Kwik link.")

    title_m = re.search(r"<title>([^<]+)</title>", html)
    title   = title_m.group(1).strip() if title_m else "AnimePahe episode"

    info = _resolve_one_kwik(chosen_kwik)
    if info:
        info["title"]  = title
        info["audio"]  = "eng" if is_dub else "jpn"
        info["fansub"] = fansub
        return info

    raise RuntimeError(f"Could not resolve Kwik URL: {chosen_kwik}")


# ═════════════════════════════════════════════════════════════════════════
# 9.  M3U8 PARSER
# ═════════════════════════════════════════════════════════════════════════

def _fetch_text(url: str, headers: Optional[Dict] = None, timeout: int = 30) -> str:
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="replace")


def parse_m3u8_segments(content: str, base_url: str) -> List[Dict[str, Any]]:
    """
    Parse an HLS manifest and return a list of segment dicts.
    Handles master playlists (recursively resolves the last variant),
    AES-128 encryption headers, and custom IV values.
    """
    lines = content.splitlines()

    # Master playlist — recurse into the last (highest) variant.
    if "#EXT-X-STREAM-INF" in content:
        variants: List[str] = []
        for i, line in enumerate(lines):
            if line.startswith("#EXT-X-STREAM-INF") and i + 1 < len(lines):
                variants.append(urllib.parse.urljoin(base_url, lines[i + 1].strip()))
        if variants:
            sub = _fetch_text(variants[-1])
            return parse_m3u8_segments(sub, variants[-1])

    segments: List[Dict[str, Any]] = []
    key_url:  Optional[str]        = None
    key_iv:   Optional[bytes]      = None
    seq_num:  int                  = 0

    for line in lines:
        line = line.strip()
        if not line:
            continue
        if line.startswith("#EXT-X-MEDIA-SEQUENCE:"):
            with contextlib.suppress(ValueError, IndexError):
                seq_num = int(line.split(":", 1)[1])
        elif line.startswith("#EXT-X-KEY:"):
            kv: Dict[str, str] = {}
            for k, vq, vr in re.findall(r'([^,="]+)=(?:"([^"]+)"|([^,]+))', line[11:]):
                kv[k] = vq or vr
            if kv.get("METHOD") == "AES-128":
                if uri := kv.get("URI"):
                    key_url = urllib.parse.urljoin(base_url, uri)
                if iv_hex := kv.get("IV"):
                    clean_hex = iv_hex.lstrip("0xX").lstrip("0X")
                    if len(clean_hex) % 2:
                        clean_hex = "0" + clean_hex
                    key_iv = bytes.fromhex(clean_hex)
                else:
                    key_iv = None
            else:
                key_url = None
                key_iv  = None
        elif not line.startswith("#"):
            iv = key_iv if key_iv is not None else (
                seq_num.to_bytes(16, "big") if key_url else None
            )
            segments.append({
                "url":     urllib.parse.urljoin(base_url, line),
                "key_url": key_url,
                "iv":      iv,
            })
            seq_num += 1

    return segments


# ═════════════════════════════════════════════════════════════════════════
# 10.  PROGRESS DASHBOARD
# ═════════════════════════════════════════════════════════════════════════

class Dashboard:
    """Two-tier Rich progress display: one bar per episode + segment sub-bar."""

    def __init__(self) -> None:
        self._prog = Progress(
            SpinnerColumn(style="cyan"),
            TextColumn("[bold white]{task.description:<46}"),
            BarColumn(bar_width=None, style="cyan", complete_style="bold green"),
            TextColumn("[bold green]{task.percentage:>5.1f}%"),
            DownloadColumn(),
            TransferSpeedColumn(),
            TimeRemainingColumn(),
            console=console, expand=True,
        )
        self._seg_prog = Progress(
            TextColumn("   [dim]└─[/dim]"),
            TextColumn("[dim]{task.description}[/dim]"),
            BarColumn(bar_width=24, style="dim cyan", complete_style="dim green"),
            MofNCompleteColumn(),
            console=console, expand=True,
        )
        self._live:     Optional[Live]       = None
        self._task_map: Dict[int, TaskID]    = {}
        self._seg_map:  Dict[int, TaskID]    = {}

    def start(self) -> None:
        panel      = Panel(
            Group(self._prog, self._seg_prog),
            title="[bold cyan]pahe-batcher — Downloading[/bold cyan]",
            border_style="cyan", box=box.ROUNDED,
        )
        self._live = Live(panel, console=console, refresh_per_second=10)
        self._live.start()

    def stop(self) -> None:
        if self._live:
            self._live.stop()

    def add_asset(self, asset_id: int, label: str, total: int = 0) -> TaskID:
        if asset_id in self._task_map:
            self._prog.update(self._task_map[asset_id], description=label[:46])
            return self._task_map[asset_id]
        tid = self._prog.add_task(label[:46], total=total or None)
        self._task_map[asset_id] = tid
        return tid

    def set_total(self, asset_id: int, total: int) -> None:
        if (tid := self._task_map.get(asset_id)) is not None:
            self._prog.update(tid, total=total)

    def add_segment_bar(self, asset_id: int, label: str, n: int) -> TaskID:
        tid = self._seg_prog.add_task(label, total=n)
        self._seg_map[asset_id] = tid
        return tid

    def segment_done(self, asset_id: int, byte_len: int) -> None:
        if (tid := self._seg_map.get(asset_id)) is not None:
            self._seg_prog.update(tid, advance=1)
        if (tid := self._task_map.get(asset_id)) is not None:
            self._prog.update(tid, advance=byte_len)

    def remuxing(self, asset_id: int, label: str) -> None:
        if (tid := self._task_map.get(asset_id)) is not None:
            self._prog.update(tid, description=f"[yellow]⟳ Remuxing  {label[:36]}[/yellow]")
        if (tid := self._seg_map.get(asset_id)) is not None:
            self._seg_prog.update(tid, visible=False)

    def complete(self, asset_id: int, label: str) -> None:
        if (tid := self._task_map.get(asset_id)) is not None:
            total = self._prog.tasks[tid].total or 1
            self._prog.update(
                tid,
                description=f"[green]{label[:46]}[/green]",
                completed=total, total=total,
            )
            self._prog.stop_task(tid)
        if (tid := self._seg_map.get(asset_id)) is not None:
            self._seg_prog.update(tid, visible=False)

    def fail(self, asset_id: int, reason: str) -> None:
        if (tid := self._task_map.get(asset_id)) is not None:
            self._prog.update(tid, description=f"[red]{reason[:46]}[/red]")
            self._prog.stop_task(tid)
        if (tid := self._seg_map.get(asset_id)) is not None:
            self._seg_prog.update(tid, visible=False)


# ═════════════════════════════════════════════════════════════════════════
# 11.  ASYNC HTTP HELPERS
# ═════════════════════════════════════════════════════════════════════════

async def _aio_fetch(
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
    # Logically unreachable: the loop always returns or raises on the last attempt.
    raise AssertionError("_aio_fetch: exhausted retries without raising")  # pragma: no cover


async def _urllib_fetch(url: str, headers: Optional[Dict] = None) -> bytes:
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
        raise AssertionError("_urllib_fetch: exhausted retries without raising")  # pragma: no cover

    return await asyncio.get_running_loop().run_in_executor(None, _sync)


# ═════════════════════════════════════════════════════════════════════════
# 12.  DOWNLOAD ENGINE  (HLS only)
# ═════════════════════════════════════════════════════════════════════════

class Downloader:
    def __init__(
        self,
        db:   VaultDB,
        mgr:  AssetManager,
        dash: Dashboard,
        cfg:  DownloadConfig,
    ) -> None:
        self.db   = db
        self.mgr  = mgr
        self.dash = dash
        self.cfg  = cfg
        self._sem = asyncio.Semaphore(cfg.max_parallel)

    async def download_asset(self, asset_id: int) -> None:
        async with self._sem:
            loop = asyncio.get_running_loop()
            try:
                asset = await loop.run_in_executor(None, self.mgr.get_asset, asset_id)
                if not asset or asset["status"] in ("complete", "failed"):
                    return

                ep_num = asset["ep_number"] or ""
                title  = asset["title"] or asset["source_url"]
                label  = f"Ep {ep_num} — {title}" if ep_num else title
                self.dash.add_asset(asset_id, label)
                await loop.run_in_executor(None, self.mgr.update_status, asset_id, "extracting")

                info = await loop.run_in_executor(
                    None, extract_animepahe_stream, asset["source_url"],
                    self.cfg.quality, self.cfg.audio_lang,
                )
                if info.get("audio"):
                    await loop.run_in_executor(None, self.mgr.update_status, asset_id, "downloading", audio=info["audio"])
                
                await self._download_hls(asset_id, info)

            except Exception as exc:
                log.exception("Asset %d failed", asset_id)
                self.dash.fail(asset_id, f"✗ {str(exc)[:40]}")
                await loop.run_in_executor(None, self.mgr.update_status, asset_id, "failed")

    async def _download_hls(self, asset_id: int, info: Dict[str, Any]) -> None:
        url        = info["url"]
        user_agent = info.get("user_agent", "Mozilla/5.0")
        referer    = info.get("referer", "")
        title      = info.get("title", "HLS stream")
        hdrs       = {"User-Agent": user_agent, "Referer": referer}
        loop       = asyncio.get_running_loop()

        m3u8_txt = await loop.run_in_executor(None, _fetch_text, url, hdrs)
        segments  = parse_m3u8_segments(m3u8_txt, url)
        if not segments:
            raise RuntimeError("No segments found in m3u8")

        # Determine final display title (prefer a real episode title over generic ones).
        existing      = await loop.run_in_executor(None, self.mgr.get_asset, asset_id)
        current_title = existing["title"] if existing else ""
        if not current_title or "Episode" in current_title or "stream" in current_title.lower():
            await loop.run_in_executor(
                None, partial(self.mgr.update_status, asset_id, "downloading", title=title)
            )
        else:
            await loop.run_in_executor(None, self.mgr.update_status, asset_id, "downloading")
            title = current_title

        ep_num = existing["ep_number"] if existing else ""
        self.dash.add_asset(asset_id, f"Ep {ep_num} — {title}")

        # Resume: skip segments already stored.
        completed_indices = await loop.run_in_executor(
            None, self.mgr.get_completed_segments, asset_id,
        )
        pending_segments  = [(i, s) for i, s in enumerate(segments) if i not in completed_indices]
        n = len(segments)
        self.dash.set_total(asset_id, n * _TS_SEGMENT_HINT_BYTES)
        self.dash.add_segment_bar(asset_id, title[:36], n)

        # Advance progress for already-complete segments.
        for _ in range(len(completed_indices)):
            self.dash.segment_done(asset_id, _TS_SEGMENT_HINT_BYTES)

        # ── AES key prefetch ──────────────────────────────────────────────
        key_map: Dict[str, bytes] = {}
        session: Optional["aiohttp.ClientSession"] = None

        if HAS_AIOHTTP:
            ssl_ctx   = get_hardened_ssl_context()
            connector = aiohttp.TCPConnector(limit=self.cfg.hls_workers, ssl=ssl_ctx)
            session   = aiohttp.ClientSession(connector=connector)

        try:
            unique_key_urls = {s["key_url"] for s in segments if s["key_url"]}
            for kurl in unique_key_urls:
                if cached := _aes_key_cache.get(kurl):
                    key_map[kurl] = cached
                elif session:
                    key_map[kurl] = await _aio_fetch(session, kurl, hdrs)
                    _aes_key_cache.set(kurl, key_map[kurl])
                else:
                    key_map[kurl] = await _urllib_fetch(kurl, hdrs)
                    _aes_key_cache.set(kurl, key_map[kurl])

            seg_sem = asyncio.Semaphore(self.cfg.hls_workers)

            async def fetch_seg(idx: int, seg: Dict[str, Any]) -> None:
                async with seg_sem:
                    data = (await _aio_fetch(session, seg["url"], hdrs)
                            if session else await _urllib_fetch(seg["url"], hdrs))

                    if seg["key_url"]:
                        if not HAS_AES:
                            raise RuntimeError(
                                "AES-128 stream requires pycryptodomex — "
                                "run: pip install pycryptodomex"
                            )
                        cipher = AES.new(key_map[seg["key_url"]], AES.MODE_CBC, iv=seg["iv"])
                        data   = cipher.decrypt(data)

                    await loop.run_in_executor(
                        None, self.mgr.store_single_chunk, asset_id, idx, data,
                    )
                    self.dash.segment_done(asset_id, len(data))

            await asyncio.gather(*(asyncio.create_task(fetch_seg(i, s)) for i, s in pending_segments))

        finally:
            if session:
                await session.close()

        # ── Finalise ──────────────────────────────────────────────────────
        await loop.run_in_executor(None, self.mgr.store_meta, asset_id, "type", "hls")

        total_bytes = await loop.run_in_executor(None, self.mgr.get_total_bytes, asset_id)
        await loop.run_in_executor(
            None, partial(self.mgr.update_status, asset_id, "complete", total_bytes=total_bytes),
        )

        ep_label = ep_num or ""
        self.dash.remuxing(asset_id, f"Ep {ep_label}" if ep_label else title[:36])

        exported = await loop.run_in_executor(
            None, self.mgr.export_to_file, asset_id, self.cfg.output_dir,
        )
        if self.cfg.purge_db:
            await loop.run_in_executor(None, self.mgr.delete_asset_chunks, asset_id)

        label = Path(exported).name if exported else f"✓ {title[:36]}"
        self.dash.complete(asset_id, f"✓ {label}")


# ═════════════════════════════════════════════════════════════════════════
# 13.  ANIMEPAHE SCANNER
# ═════════════════════════════════════════════════════════════════════════

_UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I
)


def _pahe_parse_url(url: str) -> Tuple[str, str]:
    """Validate an AnimePahe series URL and return (host, anime_uuid)."""
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"Unsupported scheme: {parsed.scheme!r}")
    if not parsed.netloc or "animepahe" not in parsed.netloc:
        raise ValueError(f"Not an AnimePahe URL: {url}")
    match = _UUID_RE.search(parsed.path)
    if not match:
        raise ValueError(
            "No anime UUID in URL.\n"
            "  Expected: https://animepahe.ru/anime/<uuid>\n"
            f"  Got:      {url}"
        )
    return parsed.netloc, match.group(0)


class AnimePaheScanner:
    def __init__(self, host: str, session: str = "") -> None:
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
        m = re.search(r"<h1[^>]*>\s*(.*?)\s*</h1>", html, re.IGNORECASE | re.DOTALL)
        if m:
            t = re.sub(r"<[^>]+>", "", m.group(1)).strip()
            # Some mirrors duplicate the title inside the h1 tag.
            if len(t) > 4:
                half = len(t) // 2
                if t[:half] == t[half:]:
                    return t[:half].strip()
            return t
        m = re.search(r"<title>([^<]+)</title>", html, re.IGNORECASE)
        if m:
            t = re.sub(r"\s*[|·].*$", "", m.group(1)).strip()
            return re.sub(r"^Watch\s+", "", t, flags=re.IGNORECASE).strip()
        return "Unknown Anime"

    def _parse_page(self, data: Dict) -> List[EpisodeInfo]:
        eps: List[EpisodeInfo] = []
        for item in data.get("data", []):
            if not (ep_session := item.get("session", "")):
                continue
            eps.append(EpisodeInfo(
                number   = float(item.get("episode", 0) or 0),
                session  = ep_session,
                title    = (item.get("title") or "").strip(),
                fansub   = (item.get("fansub") or "").strip(),
                audio    = (item.get("audio") or "jpn").strip().lower(),  # ← normalized
                play_url = f"https://{self.host}/play/{self.session}/{ep_session}",
            ))
        return eps

    def scan(self, prefer_audio: str = "jpn") -> AnimeInfo:
        """
        Fetch the full episode list and optionally deduplicate by episode
        number, keeping the entry that matches `prefer_audio` ("jpn" or "eng").
        If a preferred-language entry doesn't exist, the available one is kept.
        """
        console.print("  [dim]Fetching episode list …[/dim]", end="\r")
        first = self._fetch_page(1)
        if not first:
            raise RuntimeError(
                "Failed to fetch episode list.\n"
                "  • Is FlareSolverr running?\n"
                "  • Is the URL an /anime/ series page (not a /play/ episode link)?"
            )

        last_page = int(first.get("last_page", 1))
        total     = int(first.get("total", 0))
        anime     = AnimeInfo(session=self.session, title=self._fetch_title(),
                              host=self.host, total=total)
        anime.episodes.extend(self._parse_page(first))

        for page in range(2, last_page + 1):
            time.sleep(REQUEST_DELAY)
            console.print(f"  [dim]Fetching page {page}/{last_page} …[/dim]", end="\r")
            if data := self._fetch_page(page):
                anime.episodes.extend(self._parse_page(data))

        console.print(" " * 60, end="\r")   # clear the spinner line

        # ── Deduplicate by episode number, honouring audio preference ─────
        if prefer_audio:
            best: Dict[float, EpisodeInfo] = {}
            for ep in anime.episodes:
                if ep.number not in best:
                    best[ep.number] = ep
                elif ep.audio == prefer_audio:
                    best[ep.number] = ep  # override with preferred
            anime.episodes = sorted(best.values(), key=lambda e: e.number)
        else:
            anime.episodes.sort(key=lambda e: e.number)

        return anime


# ═════════════════════════════════════════════════════════════════════════
# 14.  EPISODE SELECTION  (interactive + non-interactive)
# ═════════════════════════════════════════════════════════════════════════

def _parse_ep_range(raw: str, all_eps: List[EpisodeInfo]) -> List[float]:
    """Parse a range string like '1-12', '1,4,7', '13-' into episode numbers."""
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


def _compact_ep_list(episodes: List[EpisodeInfo]) -> str:
    """Summarise selected episodes as a human-readable range string."""
    if not episodes:
        return "none"
    nums = sorted(ep.number for ep in episodes)
    if len(nums) == 1:
        n = nums[0]
        return str(int(n) if n == int(n) else n)

    ranges: List[Tuple[float, float]] = []
    start = prev = nums[0]
    for n in nums[1:]:
        if n == prev + 1:
            prev = n
        else:
            ranges.append((start, prev))
            start = prev = n
    ranges.append((start, prev))

    parts: List[str] = []
    for s, e in ranges:
        si = int(s) if s == int(s) else s
        ei = int(e) if e == int(e) else e
        parts.append(str(si) if s == e else f"{si}–{ei}")
    return ", ".join(parts)


def _print_ep_table(episodes: List[EpisodeInfo], selected: Set[str]) -> None:
    t = Table(box=box.SIMPLE, show_header=True, header_style="bold cyan", padding=(0, 1))
    t.add_column("",      width=2,  justify="center")
    t.add_column("Ep",    width=6,  justify="right", style="dim")
    t.add_column("Title", style="white")
    t.add_column("Audio", width=5)
    for ep in episodes:
        check = "[green]✓[/green]" if ep.session in selected else " "
        audio = _audio_display(ep.audio)
        t.add_row(check, ep.ep_str, ep.title or "—", audio)
    console.print(t)


def select_episodes(anime: AnimeInfo) -> List[EpisodeInfo]:
    """Interactive episode picker presented to the user."""
    console.print()
    console.print(Rule(f"[bold white] Episode Selection — {anime.title} [/bold white]", style="cyan"))
    console.print(
        f"  [cyan]{len(anime.episodes)}[/cyan] episodes found"
        f"  [dim]({anime.total} total in series)[/dim]\n"
    )
    console.print(Panel(
        "  [bold white]A[/bold white]  All episodes\n"
        "  [bold white]R[/bold white]  Range    [dim]e.g. 1-12  or  1,4,7  or  13-[/dim]\n"
        "  [bold white]L[/bold white]  Toggle   [dim]interactive checklist[/dim]\n"
        "  [bold white]N[/bold white]  Latest N [dim]most recently aired[/dim]\n"
        "  [bold white]S[/bold white]  Skip     [dim]exit without downloading[/dim]",
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
        console.print(f"  [green]✓[/green] Selected latest [cyan]{len(chosen)}[/cyan] episodes.")
        return chosen

    if mode == "R":
        console.print(
            "  Enter numbers or ranges — examples: "
            "[dim]1-12[/dim]  [dim]1,4,7[/dim]  [dim]5-[/dim]  [dim]1-6,10[/dim]"
        )
        raw    = Prompt.ask("  [cyan]Episodes[/cyan]").strip()
        nums   = _parse_ep_range(raw, anime.episodes)
        chosen = [eps_by_num[n] for n in nums if n in eps_by_num]
        console.print(f"  [green]✓[/green] Selected [cyan]{len(chosen)}[/cyan] episodes.")
        return chosen

    # mode == "L" — interactive toggle checklist
    selected: Set[str] = set()
    while True:
        console.clear()
        console.print(Rule(f"[bold white] {anime.title} [/bold white]", style="cyan"))
        _print_ep_table(anime.episodes, selected)
        console.print(
            "  [dim]Commands:[/dim] [white]a[/white]=select all  "
            "[white]n[/white]=clear  [white]<num>[/white]=toggle  "
            "[white]done[/white]=confirm"
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
    anime:      AnimeInfo,
    mode:       Literal["all", "range", "latest"],
    range_str:  str = "",
    latest_n:   int = 1,
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
# 15.  BATCH ORCHESTRATION
# ═════════════════════════════════════════════════════════════════════════

def _wizard_config(defaults: DownloadConfig) -> DownloadConfig:
    """Interactive download-settings wizard."""
    console.print()
    console.print(Rule("[bold white] Download Settings [/bold white]", style="cyan"))

    # ── Action mode ───────────────────────────────────────────────────────
    console.print(Panel(
        "  [bold white]1[/bold white]  [cyan]Download Locally[/cyan]  "
        "[dim]· Use internal HLS engine to save .mp4 files[/dim]\n"
        "  [bold white]2[/bold white]  [cyan]Export Links[/cyan]      "
        "[dim]· Get M3U8 URLs + Headers for external downloaders[/dim]\n"
        "  [bold white]3[/bold white]  [cyan]Stream via MPV[/cyan]    "
        "[dim]· Watch episodes now in high quality[/dim]",
        title="[cyan]Action[/cyan]", border_style="dim cyan", box=box.ROUNDED, padding=(0, 2),
    ))
    if defaults.export_mode:
        _default_action = "2"
    elif defaults.stream_mode:
        _default_action = "3"
    else:
        _default_action = "1"
    action_key  = Prompt.ask("  [cyan]Select[/cyan]", choices=["1", "2", "3"], default=_default_action)
    export_mode = action_key == "2"
    stream_mode = action_key == "3"

    # ── Quality ───────────────────────────────────────────────────────────
    _q_default = {360: "1", 720: "2", 1080: "3"}.get(defaults.quality, "3")
    console.print(Panel(
        "  [bold white]1[/bold white]  [dim cyan]360p [/dim cyan]  [dim]~50 MB/ep   · mobile / slow connection[/dim]\n"
        "  [bold white]2[/bold white]  [cyan]720p [/cyan]  [dim]~90 MB/ep   · recommended[/dim]\n"
        "  [bold white]3[/bold white]  [bold cyan]1080p[/bold cyan]  [dim]~150 MB/ep  · best quality[/dim]",
        title="[cyan]Quality[/cyan]", border_style="dim cyan", box=box.ROUNDED, padding=(0, 2),
    ))
    quality = {1: 360, 2: 720, 3: 1080}[int(Prompt.ask("  [cyan]Select[/cyan]",
                                                          choices=["1", "2", "3"], default=_q_default))]

    # ── Audio Language ────────────────────────────────────────────────────
    _audio_default = "1" if defaults.audio_lang == "jpn" else "2"
    console.print(Panel(
        "  [bold white]1[/bold white]  [cyan]Subbed[/cyan]  [dim](Japanese original)[/dim]\n"
        "  [bold white]2[/bold white]  [yellow]Dubbed[/yellow]  [dim](English)[/dim]",
        title="[cyan]Audio Language[/cyan]", border_style="dim cyan",
        box=box.ROUNDED, padding=(0, 2),
    ))
    audio_choice = Prompt.ask("  [cyan]Select[/cyan]", choices=["1", "2"], default=_audio_default)
    audio_lang = "jpn" if audio_choice == "1" else "eng"

    if stream_mode:
        return DownloadConfig(quality=quality, stream_mode=True, audio_lang=audio_lang)

    # ── Output directory ──────────────────────────────────────────────────
    output_dir = Prompt.ask("  [cyan]Output directory[/cyan]", default=defaults.output_dir).strip()
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    if export_mode:
        return DownloadConfig(output_dir=output_dir, quality=quality,
                              export_mode=True, audio_lang=audio_lang)

    # ── Concurrency ───────────────────────────────────────────────────────
    console.print(Panel(
        "  [bold white]1[/bold white]  [dim]Safe    — 1 download at a time[/dim]\n"
        "  [bold white]2[/bold white]  [cyan]Default[/cyan] — 2 simultaneous  [dim](recommended)[/dim]\n"
        "  [bold white]4[/bold white]  [dim]Fast    — 4 simultaneous  · higher CPU / RAM[/dim]\n"
        "  [bold white]6[/bold white]  [dim]Max     — 6 simultaneous  · may get rate-limited[/dim]",
        title="[cyan]Concurrent Downloads[/cyan]", border_style="dim cyan",
        box=box.ROUNDED, padding=(0, 2),
    ))
    max_parallel = max(1, min(6, IntPrompt.ask("  [cyan]Select[/cyan]", default=defaults.max_parallel)))
    hls_workers  = max(4, min(32, IntPrompt.ask(
        "  [cyan]HLS segment workers per download[/cyan] [dim](4–32, default 16)[/dim]",
        default=defaults.hls_workers,
    )))
    purge_db = Confirm.ask("  [cyan]Delete temp database after download?[/cyan]", default=defaults.purge_db)

    cfg = DownloadConfig(
        output_dir=output_dir, max_parallel=max_parallel, hls_workers=hls_workers,
        purge_db=purge_db, quality=quality, audio_lang=audio_lang,
    )
    console.print()
    console.print(Panel(
        f"  [dim]Output:[/dim]      {cfg.output_dir}\n"
        f"  [dim]Quality:[/dim]     [cyan]{cfg.quality}p[/cyan]\n"
        f"  [dim]Audio:[/dim]       [cyan]{'Subbed' if cfg.audio_lang == 'jpn' else 'Dubbed'}[/cyan]\n"
        f"  [dim]Parallel:[/dim]    [cyan]{cfg.max_parallel}[/cyan] downloads  ·  "
        f"[cyan]{cfg.hls_workers}[/cyan] segment workers\n"
        f"  [dim]Temp DB:[/dim]     {'purged after finish' if cfg.purge_db else 'kept'}",
        title="[bold green]✓ Ready[/bold green]", border_style="green", box=box.ROUNDED,
    ))
    return cfg


async def _run_batch(episodes: List[EpisodeInfo], cfg: DownloadConfig, db: VaultDB) -> None:
    mgr  = AssetManager(db)
    dash = Dashboard()
    dl   = Downloader(db, mgr, dash, cfg)
    loop = asyncio.get_running_loop()

    asset_ids: List[int] = []
    for ep in episodes:
        aid = await loop.run_in_executor(
            None, mgr.add_asset, ep.play_url, ep.title or f"Episode {ep.ep_str}", ep.ep_str,
        )
        asset_ids.append(aid)

    dash.start()
    try:
        await asyncio.gather(*(asyncio.create_task(dl.download_asset(aid)) for aid in asset_ids))
        await asyncio.sleep(0.5)
    finally:
        dash.stop()
        console.print("  [dim]Releasing FlareSolverr session …[/dim]", end="\r")
        Solver.destroy_session()
        console.print(" " * 50, end="\r")

    # ── Summary table ─────────────────────────────────────────────────────
    console.print()
    console.print(Rule("[bold green] All Done [/bold green]", style="green"))
    ok = fail = 0
    table = Table(box=box.SIMPLE_HEAVY, show_header=True, header_style="bold cyan", border_style="dim")
    table.add_column("Ep",     style="cyan",       width=6,  justify="right")
    table.add_column("Title",  style="bold white",  ratio=1)
    table.add_column("Status", justify="center",    width=10)
    table.add_column("Size",   justify="right",     width=10, style="cyan")
    table.add_column("File",   style="dim",         ratio=1)

    for ep, aid in zip(episodes, asset_ids):
        asset  = await loop.run_in_executor(None, mgr.get_asset, aid)
        status = asset["status"] if asset else "unknown"
        title  = asset["title"]  if asset else (ep.title or "—")
        badge  = "[bold green]✓  done[/bold green]" if status == "complete" else f"[red]✗ {status}[/red]"
        size   = f"{(asset['total_bytes'] or 0) / 1_048_576:.1f} MB" if asset else "—"

        ep_num = asset["ep_number"] if asset else ep.ep_str
        prefix = _make_ep_prefix(ep_num) if ep_num else ""
        full   = f"Ep {prefix} - {title}" if prefix else (title or "")
        fname  = f"{_sanitize_filename(full)}.mp4" if full else "—"

        table.add_row(ep.ep_str, (title or "—")[:38], badge, size, fname[:42])
        if status == "complete":
            ok += 1
        else:
            fail += 1

    console.print(table)
    status_line = f"[bold green]✓ {ok} succeeded[/bold green]"
    if fail:
        status_line += f"  [bold red]✗ {fail} failed[/bold red]"
    console.print(Panel(
        f"  {status_line}\n"
        f"  [dim]Saved to:[/dim]  {cfg.output_dir}",
        border_style="green" if not fail else "yellow", box=box.ROUNDED,
    ))

    # ── Database cleanup ──────────────────────────────────────────────────
    db_path = db.db_path
    await loop.run_in_executor(None, db.close)
    if cfg.purge_db and os.path.exists(db_path):
        try:
            os.remove(db_path)
            for suffix in ("-wal", "-shm"):
                with contextlib.suppress(OSError):
                    if os.path.exists(db_path + suffix):
                        os.remove(db_path + suffix)
            console.print("  [dim]Temp database cleaned up.[/dim]")
        except OSError as exc:
            console.print(f"  [yellow]⚠ Could not remove database:[/yellow] {exc}")


async def _run_export(episodes: List[EpisodeInfo], cfg: DownloadConfig) -> None:
    """Resolve M3U8 links for all episodes and write them to a text file."""
    console.print()
    console.print(Rule("[bold white] Exporting Links [/bold white]", style="cyan"))

    results: List[Dict[str, str]] = []
    with Progress(
        SpinnerColumn(),
        TextColumn("[bold white]{task.description:<46}"),
        BarColumn(bar_width=None, style="cyan"),
        MofNCompleteColumn(),
        console=console, expand=True,
    ) as progress:
        task = progress.add_task("Resolving links …", total=len(episodes))
        loop = asyncio.get_running_loop()

        for ep in episodes:
            progress.update(task, description=f"Resolving: {ep.label[:40]}")
            try:
                info       = await loop.run_in_executor(
                    None, extract_animepahe_stream, ep.play_url,
                    cfg.quality, cfg.audio_lang,
                )
                cookie_str = format_cookies(info["cookies"])
                ff_cmd     = (
                    f'ffmpeg -headers "User-Agent: {info["user_agent"]}\\r\\n'
                    f'Referer: {info["referer"]}\\r\\n'
                    f'Cookie: {cookie_str}\\r\\n" '
                    f'-i "{info["url"]}" -c copy "Ep_{ep.ep_str}.mp4"'
                )
                results.append({
                    "ep":     ep.ep_str,
                    "title":  ep.title or "—",
                    "url":    info["url"],
                    "ua":     info["user_agent"],
                    "ref":    info["referer"],
                    "cookie": cookie_str,
                    "ffmpeg": ff_cmd,
                })
            except Exception as exc:
                console.print(f"  [red]✗ Failed to resolve Ep {ep.ep_str}:[/red] {exc}")
            progress.advance(task)

    if not results:
        console.print("\n  [red]No links were successfully resolved.[/red]")
        return

    out_file = Path(cfg.output_dir) / "links_export.txt"
    try:
        with open(out_file, "w", encoding="utf-8") as f:
            f.write("=" * 80 + "\n")
            f.write(" PAHE-BATCHER LINK EXPORT\n")
            f.write(f" Generated: {time.ctime()}\n")
            f.write("=" * 80 + "\n\n")
            for item in results:
                f.write(f"EPISODE {item['ep']}: {item['title']}\n")
                f.write(f"  M3U8 URL:       {item['url']}\n")
                f.write(f"  User-Agent:     {item['ua']}\n")
                f.write(f"  Referer:        {item['ref']}\n")
                f.write(f"  Cookie:         {item['cookie']}\n")
                f.write(f"  FFmpeg Command:\n    {item['ffmpeg']}\n")
                f.write("-" * 40 + "\n\n")
        console.print()
        console.print(Panel(
            f"  [green]✓ Successfully exported [bold]{len(results)}[/bold] links.[/green]\n"
            f"  [dim]Saved to:[/dim]  [cyan]{out_file}[/cyan]",
            border_style="green", box=box.ROUNDED,
        ))
    except OSError as exc:
        console.print(f"  [red]✗ Failed to write export file:[/red] {exc}")
    finally:
        Solver.destroy_session()


async def _run_stream(anime_title: str, episodes: List[EpisodeInfo], cfg: DownloadConfig) -> None:
    """Resolve links and play them sequentially with MPV + interactive controls."""
    if not shutil.which("mpv"):
        console.print("\n  [red]✗ MPV not found![/red]")
        console.print("  [dim]Please install MPV (https://mpv.io) and ensure it is in your PATH.[/dim]")
        return

    console.print()
    console.print(Rule("[bold white] Streaming via MPV [/bold white]", style="cyan"))

    loop = asyncio.get_running_loop()
    idx  = 0
    while 0 <= idx < len(episodes):
        ep = episodes[idx]
        try:
            with Progress(
                SpinnerColumn(),
                TextColumn(f"[bold white]({idx + 1}/{len(episodes)}) Resolving: Ep {ep.ep_str}"),
                console=console, transient=True,
            ) as progress:
                progress.add_task("resolve", total=None)
                info = await loop.run_in_executor(
                    None, extract_animepahe_stream, ep.play_url,
                    cfg.quality, cfg.audio_lang,
                )

                # Update metadata if better info comes from the stream page.
                if info.get("audio"):
                    ep.audio = info["audio"]
                if info.get("fansub"):
                    ep.fansub = info["fansub"]

                if info.get("title") and (not ep.title or ep.title == "—" or "animepahe" in ep.title.lower()):
                    new_title = re.sub(r"\s*[|·].*$", "", info["title"]).strip()
                    new_title = re.sub(
                        r"^Watch\s+.*?\s+Episode\s+\d+\s+Online.*", "", new_title, flags=re.I,
                    ).strip()
                    # If we are playing SUB but the title says DUB, strip it.
                    if ep.audio == "jpn":
                        new_title = re.sub(r"\s+DUB\s*$", "", new_title, flags=re.I).strip()
                    
                    if new_title:
                        ep.title = new_title

            cookie_str = format_cookies(info["cookies"])
            cmd = [
                "mpv",
                f"--user-agent={info['user_agent']}",
                f"--referrer={info['referer']}",
                f"--http-header-fields=Cookie: {cookie_str}",
                "--demuxer-lavf-format=hls",
                f"--demuxer-lavf-o=cookies={cookie_str},referer={info['referer']}",
                f"--force-media-title={ep.title or f'Episode {ep.ep_str}'}",
                "--msg-level=all=warn,lavf=error,ffmpeg=error",
                info["url"],
            ]

            play_panel = Panel(
                Align.center(Group(
                    Text(anime_title, style="bold cyan underline"),
                    Text.from_markup(f"Now Playing: {ep.label}", style="bold green"),
                    Text(f"Quality: {cfg.quality}p  ·  Item {idx + 1}/{len(episodes)}", style="dim"),
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
                    err_msg = stderr.decode().strip()
                    if "failed" in err_msg.lower() or "error" in err_msg.lower():
                        live.stop()
                        console.print(f"  [red]✗ MPV Error:[/red] {err_msg[:200]}")

            # ── Playback menu ─────────────────────────────────────────────
            console.print()
            menu_table = Table.grid(padding=(0, 2))
            options: List[str] = []
            if idx < len(episodes) - 1:
                options.append("[bold green][N][/bold green] Next")
            if idx > 0:
                options.append("[bold cyan][P][/bold cyan] Previous")
            options += [
                "[bold yellow][R][/bold yellow] Replay",
                "[bold magenta][S][/bold magenta] Select Ep",
                "[bold red][Q][/bold red] Quit",
            ]
            menu_table.add_row(*options)
            console.print(Panel(menu_table, title="[dim]Playback Controls[/dim]",
                                border_style="dim", expand=False))

            _default_choice = "n" if idx < len(episodes) - 1 else "q"
            choice = Prompt.ask(
                "  [cyan]Action[/cyan]",
                choices=["n", "p", "r", "s", "q"],
                default=_default_choice,
            ).lower()

            if choice == "n":
                idx += 1
            elif choice == "p":
                idx -= 1
            elif choice == "r":
                continue
            elif choice == "s":
                sel_table = Table(box=box.SIMPLE, header_style="bold cyan",
                                  title="[bold white]Episode List[/bold white]")
                sel_table.add_column("#",     justify="right", style="dim")
                sel_table.add_column("Ep",    justify="right")
                sel_table.add_column("Title")
                for i, e in enumerate(episodes):
                    style = "bold green" if i == idx else ""
                    sel_table.add_row(str(i + 1), e.ep_str, e.title or "—", style=style)
                console.print(sel_table)
                num = IntPrompt.ask(
                    "  [cyan]Jump to item #[/cyan]",
                    choices=[i + 1 for i in range(len(episodes))],
                    default=idx + 1,
                )
                idx = num - 1
            elif choice == "q":
                break

        except KeyboardInterrupt:
            break
        except Exception as exc:
            console.print(f"  [red]✗ Error:[/red] {exc}")
            if not Confirm.ask("  [cyan]Try next episode?[/cyan]", default=True):
                break

    console.print("\n  [yellow]Playback session ended.[/yellow]")
    Solver.destroy_session()


# ═════════════════════════════════════════════════════════════════════════
# 16.  BANNER + ENTRY POINT
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


async def _main_async(args: argparse.Namespace) -> None:
    _print_banner()

    # ── Resolve URL ───────────────────────────────────────────────────────
    try:
        host, session = _pahe_parse_url(args.url)
    except ValueError as exc:
        console.print(f"\n  [red]✗ Invalid URL:[/red] {exc}")
        sys.exit(1)

    # ── Scan series ───────────────────────────────────────────────────────
    console.print(Rule("[bold white] Step 1/3 — Scanning Series [/bold white]", style="cyan"))
    console.print(f"  [dim]FlareSolverr:[/dim] {FLARESOLVERR_URL}")
    console.print(f"  [dim]Host:[/dim]         {host}")
    console.print(f"  [dim]Session:[/dim]      {session}\n")

    try:
        anime = AnimePaheScanner(host, session).scan(prefer_audio=args.audio_lang)
    except RuntimeError as exc:
        console.print(f"\n  [red]✗ Scan failed:[/red] {exc}")
        sys.exit(1)

    # Show audio breakdown
    sub_count = sum(1 for ep in anime.episodes if ep.audio == "jpn")
    dub_count = len(anime.episodes) - sub_count
    audio_info = f"{sub_count} JPN, {dub_count} DUB" if dub_count else "JPN audio"
    console.print(
        f"  [green]✓[/green] [bold]{anime.title}[/bold]"
        f"  — [cyan]{len(anime.episodes)}[/cyan] episodes found  [dim]({audio_info})[/dim]\n"
    )

    # ── List-only mode ────────────────────────────────────────────────────
    if args.list_only:
        table = Table(box=box.SIMPLE, show_header=True, header_style="bold cyan")
        table.add_column("Ep",    width=6,  justify="right")
        table.add_column("Title", style="white")
        table.add_column("Audio", width=5)
        for ep in anime.episodes:
            audio = _audio_display(ep.audio)
            table.add_row(ep.ep_str, ep.title or "—", audio)
        console.print(table)
        return

    # ── Episode selection ─────────────────────────────────────────────────
    console.print(Rule("[bold white] Step 2/3 — Select Episodes [/bold white]", style="cyan"))

    if args.all:
        chosen = noninteractive_episodes(anime, "all")
    elif args.range:
        chosen = noninteractive_episodes(anime, "range", range_str=args.range)
        if not chosen:
            console.print(f"  [red]✗ No episodes matched range:[/red] {args.range}")
            sys.exit(1)
    elif args.latest:
        chosen = noninteractive_episodes(anime, "latest", latest_n=args.latest)
    else:
        chosen = select_episodes(anime)

    if not chosen:
        console.print("\n  [yellow]No episodes selected — exiting.[/yellow]")
        return

    # ── Confirm queue ─────────────────────────────────────────────────────
    ep_summary = _compact_ep_list(chosen)
    console.print()
    console.print(Panel(
        f"  Series:   [bold white]{anime.title}[/bold white]\n"
        f"  Episodes: [cyan]{ep_summary}[/cyan]  ([bold]{len(chosen)}[/bold] total)",
        title="[green]Download Queue[/green]", border_style="green", box=box.ROUNDED,
    ))

    _noninteractive = args.yes or args.all or args.range or args.latest or args.export or args.stream
    if not _noninteractive:
        if not Confirm.ask("  [bold cyan]Proceed?[/bold cyan]", default=True):
            console.print("  [yellow]Cancelled.[/yellow]")
            return

    # ── Configure ─────────────────────────────────────────────────────────
    console.print(Rule("[bold white] Step 3/3 — Configure & Download [/bold white]", style="cyan"))

    safe_title = _sanitize_filename(anime.title)
    series_dir = os.path.join(args.output, safe_title)

    defaults = DownloadConfig(
        output_dir   = series_dir,
        max_parallel = args.parallel,
        hls_workers  = args.workers,
        purge_db     = args.purge_db,
        quality      = args.quality,
        export_mode  = args.export,
        stream_mode  = args.stream,
        audio_lang   = args.audio_lang,
    )

    if _noninteractive:
        cfg = defaults
        Path(cfg.output_dir).mkdir(parents=True, exist_ok=True)
        console.print(Panel(
            f"  [dim]Output:[/dim]      {cfg.output_dir}\n"
            f"  [dim]Quality:[/dim]     [cyan]{cfg.quality}p[/cyan]\n"
            f"  [dim]Audio:[/dim]       [cyan]{'Subbed' if cfg.audio_lang == 'jpn' else 'Dubbed'}[/cyan]\n"
            f"  [dim]Parallel:[/dim]    [cyan]{cfg.max_parallel}[/cyan] downloads  ·  "
            f"[cyan]{cfg.hls_workers}[/cyan] segment workers\n"
            f"  [dim]Temp DB:[/dim]     {'purged after finish' if cfg.purge_db else 'kept'}",
            title="[bold green]✓ Ready[/bold green]", border_style="green", box=box.ROUNDED,
        ))
    else:
        cfg = _wizard_config(defaults)

    # ── Export / Stream / Download ────────────────────────────────────────
    if cfg.export_mode:
        await _run_export(chosen, cfg)
        return

    if cfg.stream_mode:
        await _run_stream(anime.title, chosen, cfg)
        return

    if cfg.purge_db:
        fd, db_path = tempfile.mkstemp(suffix=".db", prefix="pahe_staging_")
        os.close(fd)
    else:
        db_path = "pahe_batcher.db"

    db = VaultDB(db_path)
    try:
        await _run_batch(chosen, cfg, db)
    finally:
        Solver.destroy_session()


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="pahe_batcher",
        description=f"pahe-batcher v{VERSION} — AnimePahe Batch Downloader",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  %(prog)s https://animepahe.ru/anime/<uuid>                       # wizard\n"
            "  %(prog)s https://animepahe.ru/anime/<uuid> --all                 # download all\n"
            "  %(prog)s https://animepahe.ru/anime/<uuid> --range 1-12          # season 1\n"
            "  %(prog)s https://animepahe.ru/anime/<uuid> --latest 3            # last 3 eps\n"
            "  %(prog)s https://animepahe.ru/anime/<uuid> --list                # list only\n"
            "  %(prog)s https://animepahe.ru/anime/<uuid> --audio eng --all     # dubbed all\n"
            "  %(prog)s https://animepahe.ru/anime/<uuid> --all -o ~/anime -j 3 # full flags\n"
        ),
    )

    parser.add_argument("url", metavar="URL",
                        help="AnimePahe series page URL  (https://animepahe.ru/anime/<uuid>)")

    sel = parser.add_mutually_exclusive_group()
    sel.add_argument("--all",    "-a", action="store_true",
                     help="Download every episode (no prompts)")
    sel.add_argument("--range",  "-r", metavar="RANGE",
                     help='Episode range, e.g. "1-12" or "1,4,7" or "13-"')
    sel.add_argument("--latest", "-n", metavar="N", type=int,
                     help="Download the latest N episodes")

    parser.add_argument("--list",    "-l", action="store_true", dest="list_only",
                        help="List episodes only — do not download")
    parser.add_argument("--export",  "-e", action="store_true",
                        help="Export M3U8 links and headers to a file instead of downloading")
    parser.add_argument("--stream",  "-s", action="store_true",
                        help="Stream episodes directly via MPV")

    parser.add_argument("-o", "--output",    default="./downloads",
                        help="Output directory (default: ./downloads)")
    parser.add_argument("-q", "--quality",   metavar="Q", type=int,
                        choices=[360, 720, 1080], default=1080,
                        help="Preferred quality: 360, 720, or 1080 (default: 1080)")
    parser.add_argument("--audio",           metavar="LANG", type=str,
                        choices=["jpn", "eng"], default="jpn", dest="audio_lang",
                        help="Preferred audio: jpn = subbed (default), eng = dubbed")
    parser.add_argument("-j", "--parallel",  metavar="N", type=int, default=2,
                        help="Max concurrent downloads (default: 2, max: 6)")
    parser.add_argument("-w", "--workers",   metavar="N", type=int, default=HLS_WORKERS,
                        help=f"HLS segment workers per download (default: {HLS_WORKERS}, max: 32)")
    parser.add_argument("--keep-db",         action="store_false", dest="purge_db",
                        help="Keep the SQLite database after download (default: deleted)")
    parser.add_argument("-y", "--yes",       action="store_true",
                        help="Skip all confirmation prompts")

    args = parser.parse_args()
    args.parallel = max(1, min(6, args.parallel))
    args.workers  = max(4, min(32, args.workers))

    try:
        asyncio.run(_main_async(args))
    except KeyboardInterrupt:
        console.print("\n  [yellow]Interrupted — cleaning up …[/yellow]")
        Solver.destroy_session()
        sys.exit(0)


if __name__ == "__main__":
    main()
