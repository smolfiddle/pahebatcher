"""FlareSolverr client — instance-based with async HTTP and TTL caching."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
from typing import Any

import aiohttp

from pahebatcher.cache import TTLCache
from pahebatcher.config import RETRY_ATTEMPTS, RETRY_BASE_DELAY

log = logging.getLogger(__name__)


class SolverError(Exception):
    """FlareSolverr communication or resolution failure."""


class Solver:
    def __init__(
        self,
        base_url: str,
        proxy: str | None = None,
        user_cookies: str = "",
        request_delay: float = 2.0,  # <-- 1. Added configurable delay between requests (in seconds)
    ) -> None:
        self._base = base_url.rstrip("/")
        self._proxy = proxy
        self._session_id: str | None = None
        self._sem = asyncio.Semaphore(1)
        self._lock = asyncio.Lock()
        self._solver_cache = TTLCache(ttl=120.0, max_size=256)
        self._http_session: aiohttp.ClientSession | None = None
        self._user_cookies = self._parse_cookie_string(user_cookies)
        self._request_delay = request_delay

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
                data = await r.json()
                return data.get("status") == "ok"
        except Exception:
            return False

    async def _post(self, body: dict, timeout: int = 90) -> dict | None:
        try:
            async with self._session.post(
                self._base, json=body, timeout=aiohttp.ClientTimeout(total=timeout),
            ) as r:
                return await r.json()
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
        self,
        url: str,
        cache: bool = True,
        max_timeout: int = 120000,
        wait: int = 4000,  # <-- 2. Increased default FlareSolverr page wait time (4 seconds)
    ) -> dict | None:
        if cache:
            if hit := await self._solver_cache.get(url):
                return hit  # type: ignore[no-any-return]

        async with self._sem:
            # 3. Add sleep delay before hitting FlareSolverr to prevent flooding
            if self._request_delay > 0:
                await asyncio.sleep(self._request_delay)

            await self._ensure_session()
            session_rotated = False
            timeout = max_timeout
            page_wait = wait
            for attempt in range(RETRY_ATTEMPTS):
                body: dict[str, Any] = {
                    "cmd": "request.get",
                    "url": url,
                    "maxTimeout": timeout,
                    "wait": page_wait,
                }
                if self._proxy:
                    body["proxy"] = self._proxy
                if self._user_cookies:
                    body["cookies"] = self._user_cookies
                async with self._lock:
                    if self._session_id:
                        body["session"] = self._session_id

                data = await self._post(body)
                if not data:
                    if attempt < RETRY_ATTEMPTS - 1:
                        await asyncio.sleep(RETRY_BASE_DELAY * (2**attempt))
                    continue

                if data.get("status") == "ok":
                    sol = data.get("solution")
                    if sol and cache:
                        await self._solver_cache.set(url, sol)
                    return sol

                msg = data.get("message", "")
                log.warning("FlareSolverr: %s", msg)

                if "session" in msg.lower() or "not found" in msg.lower():
                    async with self._lock:
                        self._session_id = None
                    await self._ensure_session()
                elif "cloudflare" in msg.lower() or "challenge" in msg.lower():
                    if not session_rotated:
                        log.warning("Cloudflare blocked — rotating session and retrying with longer timeout.")
                        await self.destroy_session()
                        session_rotated = True
                        timeout = min(timeout * 2, 300000)
                        page_wait = 6000
                        await asyncio.sleep(3.0)  # <-- Brief pause after Cloudflare flag before retrying
                        continue
                    log.warning(
                        "Cloudflare still blocking after session rotation.\n"
                        "  Set FLARESOLVERR_PROXY env var to route through a proxy/VPN."
                    )
                    break
                else:
                    break
        return None

    async def fetch_json(self, url: str) -> dict | None:
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
                return json.loads(txt)  # type: ignore[no-any-return]
        with contextlib.suppress(json.JSONDecodeError):
            return json.loads(re.sub(r"<[^>]+>", "", body).strip())  # type: ignore[no-any-return]
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
                            return json.loads(body[start : i + 1])  # type: ignore[no-any-return]
                        break
        return None

    async def fetch_html(self, url: str) -> tuple[str, list[dict]] | None:
        sol = await self.request(url)
        if not sol:
            return None
        return sol.get("response", ""), sol.get("cookies", [])