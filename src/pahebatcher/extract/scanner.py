"""AnimePahe series scanner — URL parsing, variant discovery, episode listing."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from pahebatcher.config import REQUEST_DELAY
from pahebatcher.models import AnimeInfo, EpisodeInfo
from pahebatcher.utils import sanitize

if TYPE_CHECKING:
    from pathlib import Path

    from pahebatcher.solver import Solver

log = logging.getLogger(__name__)

_UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I,
)


def parse_anime_url(url: str) -> tuple[str, str]:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"Unsupported scheme: {parsed.scheme!r}")
    if not parsed.netloc or "animepahe" not in parsed.netloc:
        raise ValueError(f"Not an AnimePahe URL: {url!r}")
    m = _UUID_RE.search(parsed.path)
    if not m:
        raise ValueError(
            f"No anime UUID in URL.\n"
            f"  Expected: https://animepahe.pw/anime/<uuid>\n"
            f"  Got:      {url}"
        )
    return parsed.netloc, m.group(0)


class AnimePaheScanner:
    _current_host: str = "animepahe.com"

    def __init__(self, solver: Solver, host: str, session: str) -> None:
        self.solver = solver
        self.host = host
        self.session = session

    @classmethod
    async def search(cls, solver: Solver, host: str, query: str) -> list[dict[str, Any]]:
        import urllib.parse

        hosts = list(dict.fromkeys([host, "animepahe.pw", "animepahe.com", "animepahe.org"]))
        for h in hosts:
            url = f"https://{h}/api?m=search&q={urllib.parse.quote(query)}"
            try:
                data = await solver.fetch_json(url)
                if data and "data" in data:
                    cls._current_host = h
                    result = data.get("data", [])
                    return list(result) if isinstance(result, list) else []
            except Exception as exc:
                log.debug("Search failed on %s: %s", h, exc)
                continue
        return []

    async def _fetch_page(self, page: int) -> dict[str, Any] | None:
        url = (
            f"https://{self.host}/api?m=release&id={self.session}"
            f"&sort=episode_asc&page={page}"
        )
        data = await self.solver.fetch_json(url)
        if data is None:
            alt = (
                f"https://{self.host}/api/{self.session}"
                f"/releases?sort=episode_asc&page={page}"
            )
            data = await self.solver.fetch_json(alt)
        return data

    async def _fetch_title(self) -> str:
        result = await self.solver.fetch_html(f"https://{self.host}/anime/{self.session}")
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
            t = re.sub(r"\s*[|\u00b7].*$", "", m.group(1)).strip()
            return re.sub(r"^Watch\s+", "", t, flags=re.I).strip()
        return "Unknown Anime"

    @staticmethod
    def _parse_episode_page(data: dict[str, Any], host: str, session: str) -> list[EpisodeInfo]:
        eps: list[EpisodeInfo] = []
        for item in data.get("data", []):
            ep_sess = item.get("session", "")
            if not ep_sess:
                continue
            audio = (item.get("audio") or "jpn").strip().lower()
            title = (item.get("title") or "").strip()
            if audio == "jpn" and "dub" in title.lower():
                audio = "eng"
            elif audio == "eng" and "sub" in title.lower():
                audio = "jpn"
            eps.append(EpisodeInfo(
                number=float(item.get("episode", 0) or 0),
                session=ep_sess,
                title=("" if title == "?" else title),
                fansub=(item.get("fansub") or "").strip(),
                audio=audio,
                play_url=f"https://{host}/play/{session}/{ep_sess}",
            ))
        return eps

    @classmethod
    async def discover_all_sessions(cls, solver: Solver, host: str, session: str) -> list[str]:
        url = f"https://{host}/api?m=release&id={session}&sort=episode_asc&page=1"
        data = await solver.fetch_json(url)
        if not data or "anime" not in data:
            return [session]

        anime_data = data["anime"]
        title = (anime_data.get("title") or "").strip()
        if not title:
            return [session]

        def _norm(t: str) -> str:
            return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", "", t.lower())).strip()

        norm_title = _norm(title)
        search_results = await cls.search(solver, host, title)

        sessions: set[str] = {session}
        for res in search_results:
            res_session = res.get("session", "")
            if res_session and _norm(res.get("title", "")) == norm_title:
                sessions.add(res_session)
        return list(sessions)

    @staticmethod
    def _cache_path(cache_dir: Path, session: str, title: str) -> Path:
        safe = sanitize(title) or session
        return cache_dir / f"{safe}_{session}" / "_scan_cache.json"

    @staticmethod
    def _load_cache(cache_path: Path, cache_ttl: int) -> AnimeInfo | None:
        if not cache_path.exists() or cache_ttl <= 0:
            return None
        try:
            raw = json.loads(cache_path.read_text(encoding="utf-8"))
        except Exception:
            return None
        age = time.time() - raw.get("cached_at", 0)
        if age > cache_ttl * 60:
            return None
        episodes = [
            EpisodeInfo(**ep) for ep in raw.get("episodes", [])
        ]
        return AnimeInfo(
            session=raw["session"],
            title=raw["title"],
            host=raw["host"],
            total=raw.get("total", len(episodes)),
            episodes=episodes,
        )

    @staticmethod
    def _save_cache(cache_path: Path, anime: AnimeInfo) -> None:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "cached_at": time.time(),
            "title": anime.title,
            "session": anime.session,
            "host": anime.host,
            "total": anime.total,
            "episodes": [
                {"number": e.number, "session": e.session, "title": e.title,
                 "fansub": e.fansub, "audio": e.audio, "play_url": e.play_url}
                for e in anime.episodes
            ],
        }
        try:
            cache_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception:
            pass

    async def scan(self, cache_dir: Path, prefer_audio: str = "jpn", cache_ttl: int = 60) -> AnimeInfo:
        from pahebatcher.ui.console import console

        # Try cache first — stable lookup via glob so title variation doesn't invalidate cache
        if cache_ttl > 0:
            # Prefer exact expected path but also scan for any prior cache with same session
            candidates: list[Path] = []
            # 1. legacy placeholder path (for completeness)
            candidates.append(self._cache_path(cache_dir, self.session, "Unknown Anime"))
            # 2. any folder ending with _{session}/_scan_cache.json
            if cache_dir.exists():
                candidates.extend(cache_dir.glob(f"*_{self.session}/_scan_cache.json"))
            seen: set[Path] = set()
            for cp in candidates:
                if cp in seen or not cp.exists():
                    continue
                seen.add(cp)
                cached = self._load_cache(cp, cache_ttl)
                if cached:
                    safe_title = sanitize(cached.title)
                    session_path = cache_dir / f"{safe_title}_{self.session}"
                    cached.has_session = session_path.exists()
                    console.print(f"  [dim]Using cached scan ({cache_ttl} min TTL)[/dim]")
                    return cached

        title = "Unknown Anime"

        console.print("  [dim]Discovering all variants ...[/dim]", end="\r")
        all_sessions = await self.discover_all_sessions(self.solver, self.host, self.session)
        title = await self._fetch_title()
        if title == "Unknown Anime":
            proxy_hint = os.getenv("FLARESOLVERR_PROXY")
            proxy_msg = (
                " Or route through a proxy/VPN via FLARESOLVERR_PROXY env var."
                if not proxy_hint else
                " Proxy is set — try a different proxy or check your VPN connection."
            )
            console.print(
                "\n  [yellow]Could not fetch series information.[/yellow]"
                f"\n  [dim]FlareSolverr may be unable to bypass Cloudflare."
                f" Try updating: docker pull ghcr.io/flaresolverr/flaresolverr"
                f"{proxy_msg}[/dim]\n"
            )
        anime = AnimeInfo(session=self.session, title=title, host=self.host)

        safe_title = sanitize(title)
        session_path = cache_dir / f"{safe_title}_{self.session}"
        anime.has_session = session_path.exists()

        unique_episodes: dict[tuple[float, str], EpisodeInfo] = {}
        for s in all_sessions:
            console.print(f"  [dim]Scanning session {s} ...[/dim]", end="\r")
            sub_scanner = AnimePaheScanner(self.solver, self.host, s)
            page = 1
            while True:
                data = await sub_scanner._fetch_page(page)
                if not data or not data.get("data"):
                    break
                for ep in self._parse_episode_page(data, self.host, s):
                    key = (ep.number, ep.audio)
                    if key not in unique_episodes:
                        unique_episodes[key] = ep
                if page >= int(data.get("last_page", 1)):
                    break
                page += 1
                await asyncio.sleep(REQUEST_DELAY)

        anime.episodes = sorted(unique_episodes.values(), key=lambda e: (e.number, e.audio))
        anime.total = len({e.number for e in anime.episodes})
        console.print(" " * 60, end="\r")

        # Save cache
        cache_path = self._cache_path(cache_dir, self.session, title)
        self._save_cache(cache_path, anime)

        return anime
