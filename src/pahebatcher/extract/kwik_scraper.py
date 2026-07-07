"""Multi-backend Kwik page resolver — direct, curl_cffi, cloudscraper, FlareSolverr, domain rotation."""

from __future__ import annotations

import asyncio
import logging
import re
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from pahebatcher.extract.kwik import _extract_m3u8
from pahebatcher.models import StreamInfo

if TYPE_CHECKING:
    from pahebatcher.solver import Solver

log = logging.getLogger(__name__)

KWIK_DOMAIN_ORDER = ["cx", "gg", "si", "me", "net", "in", "cc", "to", "pw"]

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://animepahe.com/",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}


def _parse_kwik_page(html: str, url: str, cookies: list[dict[str, str]] | None = None) -> StreamInfo | None:
    video_url = _extract_m3u8(html)
    if not video_url:
        return None
    return StreamInfo(
        url=video_url,
        cookies=cookies or [],
        user_agent=_HEADERS["User-Agent"],
        referer=url,
    )


def _is_blocked(text: str) -> bool:
    return any(
        kw in text.lower()[:1000]
        for kw in ["attention required", "your ip has been blocked", "sorry, you have been blocked"]
    )


def _swap_domain(url: str, new_tld: str) -> str:
    parsed = urlparse(url)
    parts = parsed.netloc.split(".")
    if len(parts) >= 2:
        parts[-1] = new_tld
    return f"{parsed.scheme}://{'.'.join(parts)}{parsed.path}{'?' + parsed.query if parsed.query else ''}"


def _resolve_sync_curl_cffi(url: str) -> tuple[str, list[dict[str, str]]] | None:
    try:
        from curl_cffi import requests as curl_req
    except ImportError:
        return None
    try:
        r = curl_req.get(url, impersonate="chrome120", headers=_HEADERS, timeout=30)
        if r.status_code == 200 and not _is_blocked(r.text):
            video_url = _extract_m3u8(r.text)
            if video_url:
                cookies = [{"name": k, "value": v} for k, v in r.cookies.items()]
                return video_url, cookies
    except Exception as exc:
        log.debug("curl_cffi failed for %s: %s", url, exc)
    return None


def _resolve_sync_cloudscraper(url: str) -> tuple[str, list[dict[str, str]]] | None:
    try:
        import cloudscraper  # type: ignore[import-untyped]
    except ImportError:
        return None
    try:
        scraper = cloudscraper.create_scraper()
        r = scraper.get(url, headers=_HEADERS, timeout=30)
        if r.status_code == 200 and not _is_blocked(r.text):
            video_url = _extract_m3u8(r.text)
            if video_url:
                cookies = [{"name": k, "value": v} for k, v in r.cookies.items()]
                return video_url, cookies
    except Exception as exc:
        log.debug("cloudscraper failed for %s: %s", url, exc)
    return None


async def resolve_kwik(solver: Solver, url: str) -> StreamInfo | None:
    """Try every backend to resolve a Kwik URL into a StreamInfo."""

    # 1. Direct aiohttp with Chrome UA + Referer
    try:
        import aiohttp
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url, headers=_HEADERS, timeout=aiohttp.ClientTimeout(total=30),
            ) as r:
                html = await r.text("utf-8", errors="replace")
                cookies: list[dict[str, str]] = []
                if cookie_hdr := r.headers.get("Set-Cookie"):
                    for part in cookie_hdr.split(","):
                        if "=" in part:
                            name_val = part.split(";")[0].strip()
                            if "=" in name_val:
                                n, v = name_val.split("=", 1)
                                cookies.append({"name": n, "value": v})
                if r.status == 200 and not _is_blocked(html):
                    if parsed := _parse_kwik_page(html, url, cookies):
                        log.info("Kwik resolved via direct aiohttp")
                        return parsed
    except Exception as exc:
        log.debug("Direct aiohttp failed: %s", exc)

    # 2. curl_cffi with Chrome TLS impersonation
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(None, _resolve_sync_curl_cffi, url)
    if result:
        video_url, cookies = result
        log.info("Kwik resolved via curl_cffi")
        return StreamInfo(url=video_url, cookies=cookies, user_agent=_HEADERS["User-Agent"], referer=url)

    # 3. cloudscraper
    result = await loop.run_in_executor(None, _resolve_sync_cloudscraper, url)
    if result:
        video_url, cookies = result
        log.info("Kwik resolved via cloudscraper")
        return StreamInfo(url=video_url, cookies=cookies, user_agent=_HEADERS["User-Agent"], referer=url)

    # 4. Domain rotation — try the same path on other Kwik TLDs
    parsed_url = urlparse(url)
    current_tld = parsed_url.netloc.rsplit(".", 1)[-1]
    for tld in KWIK_DOMAIN_ORDER:
        if tld == current_tld:
            continue
        alt_url = _swap_domain(url, tld)
        log.debug("Trying alt domain: %s", alt_url)
        result = await loop.run_in_executor(None, _resolve_sync_curl_cffi, alt_url)
        if result:
            video_url, cookies = result
            log.info("Kwik resolved via alt domain kwik.%s", tld)
            return StreamInfo(url=video_url, cookies=cookies, user_agent=_HEADERS["User-Agent"], referer=alt_url)
        result = await loop.run_in_executor(None, _resolve_sync_cloudscraper, alt_url)
        if result:
            video_url, cookies = result
            log.info("Kwik resolved via alt domain kwik.%s (cloudscraper)", tld)
            return StreamInfo(url=video_url, cookies=cookies, user_agent=_HEADERS["User-Agent"], referer=alt_url)

    # 5. FlareSolverr (with longer timeout for Kwik)
    sol = await solver.request(url, cache=False, max_timeout=180000, wait=5000)
    if sol:
        html = sol.get("response", "")
        cookies = sol.get("cookies", [])
        user_agent = sol.get("userAgent", _HEADERS["User-Agent"])
        m3u8_match = re.search(r"(https?://[^\s\"']+\.(?:m3u8|mp4)[^\s\"']*)", html)
        resolved_url: str | None = m3u8_match.group(1) if m3u8_match else _extract_m3u8(html)
        if resolved_url:
            log.info("Kwik resolved via FlareSolverr")
            return StreamInfo(url=resolved_url, cookies=cookies, user_agent=user_agent, referer=url)

    # 6. FlareSolverr with domain rotation
    for tld in KWIK_DOMAIN_ORDER:
        if tld == current_tld:
            continue
        alt_url = _swap_domain(url, tld)
        sol = await solver.request(alt_url, cache=False, max_timeout=120000, wait=3000)
        if sol:
            html = sol.get("response", "")
            cookies = sol.get("cookies", [])
            user_agent = sol.get("userAgent", _HEADERS["User-Agent"])
            m3u8_match = re.search(r"(https?://[^\s\"']+\.(?:m3u8|mp4)[^\s\"']*)", html)
            resolved_url = m3u8_match.group(1) if m3u8_match else _extract_m3u8(html)
            if resolved_url:
                log.info("Kwik resolved via FlareSolverr + alt domain kwik.%s", tld)
                return StreamInfo(url=video_url, cookies=cookies, user_agent=user_agent, referer=alt_url)

    return None
