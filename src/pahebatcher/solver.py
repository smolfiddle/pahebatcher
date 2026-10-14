"""FlareSolverr client — instance-based with async HTTP and TTL caching."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
from typing import Any
from urllib.parse import urlparse

import aiohttp

from pahebatcher.cache import TTLCache
from pahebatcher.config import RETRY_ATTEMPTS, RETRY_BASE_DELAY

log = logging.getLogger(__name__)

_CHALLENGE_MARKERS = (
    "just a moment",
    "__cf_chl",
    "cf-chl",
    "checking your browser",
    "attention required",
    "verify you are human",
)

_DIRECT_TIMEOUT = 15
_DIRECT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def _host_of(url: str) -> str:
    return urlparse(url).netloc.lower()


def _is_challenge_body(text: str) -> bool:
    low = text.lower()
    return any(marker in low for marker in _CHALLENGE_MARKERS)


def _cookie_header(cookies: list[dict[str, str]]) -> str:
    return "; ".join(f"{c['name']}={c['value']}" for c in cookies)


def _direct_fetch_sync(
    url: str, cookie_header: str, timeout: int
) -> tuple[int, str, list[dict[str, str]]] | None:
    """Direct fetch with clearance cookies. Returns (status, body, set-cookies).

    Returns None on transport errors (caller falls back to the browser path).
    Challenge detection is left to the caller, which owns the store.
    Never raises.
    """
    try:
        import curl_cffi.requests
    except ImportError:
        return None
    try:
        headers = {"User-Agent": _DIRECT_UA}
        if cookie_header:
            headers["Cookie"] = cookie_header
        r: Any = curl_cffi.requests.get(url, impersonate="chrome120", headers=headers, timeout=timeout)
        text = bytes(r.content).decode("utf-8", errors="replace")
        jar: Any = r.cookies
        set_cookies = [{"name": str(k), "value": str(v)} for k, v in jar.items()]
        return int(r.status_code), text, set_cookies
    except Exception:
        return None


def is_termux() -> bool:
    """True when running inside Termux on Android."""
    if os.path.exists("/data/data/com.termux"):
        return True
    return "com.termux" in os.environ.get("PREFIX", "")


def install_hint() -> str:
    """How to get FlareSolverr running, tailored to the current platform."""
    if is_termux():
        return (
            "Start it on Termux:\n"
            "    pkg install -y x11-repo && pkg install -y flaresolverr\n"
            "    flaresolverr &\n"
            "  Or the smolfiddle fork for native headless / headed LXQt:\n"
            "    https://github.com/smolfiddle/FlareSolverr (see termux/README-termux.md)"
        )
    return (
        "Start it with Docker:\n"
        "    docker run -d --name=flaresolverr -p 8191:8191 ghcr.io/flaresolverr/flaresolverr\n"
        "  Or set FLARESOLVERR_URL env var if it's on a different host."
    )


def update_hint() -> str:
    """How to update FlareSolverr, tailored to the current platform."""
    if is_termux():
        return "Try updating: pkg upgrade flaresolverr (or pull the smolfiddle fork)"
    return "Try updating: docker pull ghcr.io/flaresolverr/flaresolverr"


class SolverError(Exception):
    """FlareSolverr communication or resolution failure."""


class Solver:
    def __init__(
        self,
        base_url: str,
        proxy: str | None = None,
        user_cookies: str = "",
        clearance_ttl: float = 1200.0,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._proxy = proxy
        self._session_id: str | None = None
        self._sem = asyncio.Semaphore(1)
        self._lock = asyncio.Lock()
        self._solver_cache = TTLCache(ttl=120.0, max_size=256)
        self._http_session: aiohttp.ClientSession | None = None
        self._user_cookies = self._parse_cookie_string(user_cookies)
        # Clearance store: live cf_clearance per domain, harvested from
        # successful responses. clearance_ttl <= 0 disables the store and
        # the direct fast path (legacy behavior: transplant always sent).
        self._clearance_ttl = clearance_ttl
        self._clearance = TTLCache(ttl=clearance_ttl, max_size=64)
        self._clearance_dead: set[str] = set()

    @staticmethod
    def _parse_cookie_string(s: str) -> list[dict[str, str]]:
        if not s:
            return []
        cookies: list[dict[str, str]] = []
        for part in s.split(";"):
            part = part.strip()
            if "=" in part:
                name, _, value = part.partition("=")
                cookies.append({"name": name.strip(), "value": value.strip()})
        return cookies

    async def _clearance_for(self, host: str) -> tuple[list[dict[str, str]], str]:
        """Pick clearance cookies for host: transplant unless marked dead, else harvested.

        Returns (cookies, source) with source in {"user", "harvested", "none"}.
        Memory only — harvested values are never persisted to disk.
        """
        if self._user_cookies:
            async with self._lock:
                dead = host in self._clearance_dead
            if not dead:
                return list(self._user_cookies), "user"
        if self._clearance_ttl > 0:
            hit = await self._clearance.get(host)
            if isinstance(hit, list) and hit:
                return [dict(c) for c in hit if isinstance(c, dict)], "harvested"
        return [], "none"

    async def _note_clearance_success(self, host: str, cookies: list[Any]) -> None:
        """Harvest cf_clearance from a successful response for later reuse."""
        if self._clearance_ttl <= 0:
            return
        found: list[dict[str, str]] = []
        for c in cookies:
            if not isinstance(c, dict):
                continue
            name, value = str(c.get("name", "")), c.get("value", "")
            if name == "cf_clearance" and value:
                found.append({"name": name, "value": str(value)})
        if found:
            await self._clearance.set(host, found)

    async def _note_clearance_dead(self, host: str, source: str) -> None:
        """Drop clearance that just failed a challenge for host."""
        if self._clearance_ttl <= 0:
            return
        await self._clearance.discard(host)
        if source == "user":
            async with self._lock:
                self._clearance_dead.add(host)

    async def _direct_request(
        self, url: str, host: str, cookies: list[dict[str, str]], source: str
    ) -> dict[str, Any] | None:
        """Direct fetch shaped like a FlareSolverr solution, or None to fall back."""
        loop = asyncio.get_running_loop()
        fetched = await loop.run_in_executor(
            None, _direct_fetch_sync, url, _cookie_header(cookies), _DIRECT_TIMEOUT
        )
        if fetched is None:
            return None  # transport error or no curl — transient, keep store
        status, text, set_cookies = fetched
        if _is_challenge_body(text):
            await self._note_clearance_dead(host, source)
            return None
        if status != 200:
            return None  # e.g. rate limit — transient, keep store
        await self._note_clearance_success(host, set_cookies)
        return {"response": text, "cookies": set_cookies, "userAgent": _DIRECT_UA}

    async def start(self) -> None:
        self._http_session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=90),
            headers={"Content-Type": "application/json"},
        )

    async def close(self) -> None:
        await self.destroy_session()
        if self._http_session:
            await self._http_session.close()
            self._http_session = None

    @property
    def _session(self) -> aiohttp.ClientSession:
        if not self._http_session:
            raise RuntimeError("Solver not started — call start() first")
        return self._http_session

    async def ping(self) -> bool:
        base = self._base
        if base.endswith("/v1"):
            base = base[:-3]
        try:
            async with self._session.get(f"{base}/health", timeout=aiohttp.ClientTimeout(total=5)) as r:
                data: Any = await r.json()
                if isinstance(data, dict):
                    return bool(data.get("status") == "ok")
                return False
        except Exception:
            return False

    async def _post(self, body: dict[str, Any], timeout: int = 90) -> dict[str, Any] | None:
        try:
            async with self._session.post(
                self._base, json=body, timeout=aiohttp.ClientTimeout(total=timeout),
            ) as r:
                result: Any = await r.json()
                return result if isinstance(result, dict) else None
        except Exception as exc:
            log.debug("FlareSolverr POST error: %s", exc)
            return None

    async def _ensure_session(self) -> None:
        async with self._lock:
            if self._session_id:
                return
        data = await self._post({"cmd": "sessions.create"}, timeout=15)
        if data and data.get("status") == "ok":
            sid = data.get("session")
            async with self._lock:
                self._session_id = sid
            log.info("FlareSolverr session created: %s", sid)

    async def destroy_session(self) -> None:
        async with self._lock:
            sid, self._session_id = self._session_id, None
        if sid:
            await self._post({"cmd": "sessions.destroy", "session": sid}, timeout=10)
            log.info("FlareSolverr session %s destroyed.", sid)

    async def request(
        self, url: str, cache: bool = True,
        max_timeout: int = 120000, wait: int = 2000,
    ) -> dict[str, Any] | None:
        """Fetch a page via FlareSolverr.

        ``max_timeout`` and ``wait`` are milliseconds. ``wait`` is sent as
        FlareSolverr's ``waitInSeconds`` settle delay; the client timeout
        tracks ``max_timeout`` plus a buffer so slow devices are not cut
        off while the server is still solving.
        """
        if cache and (hit := await self._solver_cache.get(url)):
            return hit  # type: ignore[no-any-return]

        host = _host_of(url)
        # Fast path: direct fetch with live clearance (transplant or
        # harvested). Falls back to the browser path transparently.
        if self._clearance_ttl > 0:
            fast_cookies, fast_source = await self._clearance_for(host)
            if fast_cookies:
                sol = await self._direct_request(url, host, fast_cookies, fast_source)
                if sol is not None:
                    if cache:
                        await self._solver_cache.set(url, sol)
                    return sol

        async with self._sem:
            await self._ensure_session()
            session_rotated = False
            timeout = max_timeout
            page_wait = wait
            for attempt in range(RETRY_ATTEMPTS):
                send_cookies, send_source = await self._clearance_for(host)
                body: dict[str, Any] = {
                    "cmd": "request.get",
                    "url": url,
                    "maxTimeout": timeout,
                    "waitInSeconds": max(1, round(page_wait / 1000)),
                }
                if self._proxy:
                    body["proxy"] = self._proxy
                if send_cookies:
                    body["cookies"] = send_cookies
                async with self._lock:
                    if self._session_id:
                        body["session"] = self._session_id

                data = await self._post(body, timeout=timeout // 1000 + 30)
                if not data:
                    if attempt < RETRY_ATTEMPTS - 1:
                        await asyncio.sleep(RETRY_BASE_DELAY * (2**attempt))
                    continue

                if data.get("status") == "ok":
                    sol = data.get("solution")
                    if sol:
                        cookies_val = sol.get("cookies", [])
                        cookies_list: list[dict[str, Any]] = (
                            list(cookies_val) if isinstance(cookies_val, list) else []
                        )
                        await self._note_clearance_success(host, cookies_list)
                        if cache:
                            await self._solver_cache.set(url, sol)
                    return sol

                msg = data.get("message", "")
                log.warning("FlareSolverr: %s", msg)

                if "session" in msg.lower() or "not found" in msg.lower():
                    async with self._lock:
                        self._session_id = None
                    await self._ensure_session()
                elif "cloudflare" in msg.lower() or "challenge" in msg.lower():
                    await self._note_clearance_dead(host, send_source)
                    if not session_rotated:
                        log.warning("Cloudflare blocked — rotating session and retrying with longer timeout.")
                        await self.destroy_session()
                        session_rotated = True
                        timeout = min(timeout * 2, 300000)
                        page_wait = 5000
                        continue
                    log.warning(
                        "Cloudflare still blocking after session rotation.\n"
                        "  Set FLARESOLVERR_PROXY env var to route through a proxy/VPN."
                    )
                    break
                else:
                    break
        return None

    async def fetch_json(self, url: str) -> dict[str, Any] | None:
        sol = await self.request(url)
        body = (sol or {}).get("response", "")
        if not body:
            return None
        for m in re.finditer(r"<pre[^>]*>([\s\S]*?)</pre>", body, re.I):
            txt = (
                m.group(1)
                .replace("&amp;", "&")
                .replace("&lt;", "<")
                .replace("&gt;", ">")
                .replace("&quot;", '"')
                .replace("&#39;", "'")
                .strip()
            )
            with contextlib.suppress(json.JSONDecodeError):
                parsed: Any = json.loads(txt)
                return parsed if isinstance(parsed, dict) else None
        with contextlib.suppress(json.JSONDecodeError):
            parsed2: Any = json.loads(re.sub(r"<[^>]+>", "", body).strip())
            return parsed2 if isinstance(parsed2, dict) else None
        start = body.find("{")
        if start >= 0:
            depth = in_str = escape = 0
            for i, ch in enumerate(body[start:], start):
                if escape:
                    escape = 0
                    continue
                if ch == "\\" and in_str:
                    escape = 1
                    continue
                if ch == '"':
                    in_str ^= 1
                    continue
                if in_str:
                    continue
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        with contextlib.suppress(json.JSONDecodeError):
                            parsed3: Any = json.loads(body[start : i + 1])
                            if isinstance(parsed3, dict):
                                return parsed3
                        break
        return None

    async def fetch_html(self, url: str) -> tuple[str, list[dict[str, Any]]] | None:
        sol = await self.request(url)
        if not sol:
            return None
        resp = sol.get("response", "")
        cookies_val = sol.get("cookies", [])
        cookies_list: list[dict[str, Any]] = list(cookies_val) if isinstance(cookies_val, list) else []
        return str(resp), cookies_list
