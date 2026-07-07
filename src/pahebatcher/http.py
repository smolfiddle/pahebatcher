"""Async HTTP client with shared aiohttp session and retry logic."""

from __future__ import annotations

import asyncio
import logging
import threading

import aiohttp

from pahebatcher.config import RETRY_ATTEMPTS, RETRY_BASE_DELAY
from pahebatcher.tls import make_ssl_ctx

log = logging.getLogger(__name__)

_curl_local = threading.local()


def _get_curl_session() -> object:
    sess = getattr(_curl_local, "session", None)
    if sess is None:
        import curl_cffi.requests  # type: ignore[import-untyped]
        sess = curl_cffi.requests.Session()
        _curl_local.session = sess
    return sess


def _curl_fetch(url: str, headers: dict[str, str] | None, timeout: int) -> bytes:
    sess = _get_curl_session()
    r = sess.get(url, impersonate="chrome120", headers=headers, timeout=timeout)
    r.raise_for_status()
    return r.content


class HttpClient:
    def __init__(self, hls_workers: int = 24) -> None:
        self._hls_workers = hls_workers
        self._session: aiohttp.ClientSession | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        connector = aiohttp.TCPConnector(
            limit=0,
            limit_per_host=self._hls_workers,
            ttl_dns_cache=300,
            keepalive_timeout=45,
            enable_cleanup_closed=True,
            ssl=make_ssl_ctx(),
            use_dns_cache=True,
        )
        self._session = aiohttp.ClientSession(
            connector=connector,
            timeout=aiohttp.ClientTimeout(total=None, connect=10, sock_read=45),
            connector_owner=True,
        )

    async def close(self) -> None:
        if self._session:
            await self._session.close()
            self._session = None

    @property
    def session(self) -> aiohttp.ClientSession:
        if not self._session:
            raise RuntimeError("HttpClient not started — call start() first")
        return self._session

    async def get(self, url: str, headers: dict[str, str] | None = None, timeout: int = 60) -> bytes:
        for attempt in range(RETRY_ATTEMPTS):
            try:
                async with self.session.get(
                    url, headers=headers,
                    timeout=aiohttp.ClientTimeout(total=timeout),
                ) as resp:
                    resp.raise_for_status()
                    return await resp.read()
            except aiohttp.ClientResponseError as exc:
                if exc.status == 403 and self._loop:
                    log.debug("aiohttp 403 on %s — curl_cffi fallback", url)
                    return await self._loop.run_in_executor(None, _curl_fetch, url, headers, timeout)
                if attempt == RETRY_ATTEMPTS - 1:
                    raise
                await asyncio.sleep(RETRY_BASE_DELAY * (2**attempt))
            except (aiohttp.ClientError, asyncio.TimeoutError, OSError):
                if attempt == RETRY_ATTEMPTS - 1:
                    raise
                await asyncio.sleep(RETRY_BASE_DELAY * (2**attempt))
        raise AssertionError("unreachable")
