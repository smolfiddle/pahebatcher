"""Kwik/AnimePahe extraction — JS unpacking, M3U8 URL resolution, stream building."""

from __future__ import annotations

import contextlib
import logging
import re
from typing import TYPE_CHECKING
from urllib.parse import urljoin

import aiohttp

from pahebatcher.config import _KWIK_DOMAINS
from pahebatcher.models import StreamInfo

if TYPE_CHECKING:
    from pahebatcher.solver import Solver

log = logging.getLogger(__name__)


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
        digits = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"

        def enc(c: int) -> str:
            return digits[c] if c < base else enc(c // base) + digits[c % base]

        lookup = {
            enc(i): (mapping[i] if i < len(mapping) and mapping[i] else enc(i))
            for i in range(count)
        }
        return re.sub(r"\b\w+\b", lambda mo: lookup.get(mo.group(0), mo.group(0)), payload)


def _extract_m3u8(html: str) -> str | None:
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


async def _resolve_kwik(solver: Solver, url: str) -> StreamInfo | None:
    """Fetch Kwik page directly via aiohttp with Referer bypass, fallback to FlareSolverr."""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
        "Referer": "https://animepahe.com/",
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=30)) as r:
                html = await r.text("utf-8", errors="replace")
                cookies: list[dict] = []
                if cookie_hdr := r.headers.get("Set-Cookie"):
                    for part in cookie_hdr.split(","):
                        if "=" in part:
                            name_val = part.split(";")[0].strip()
                            if "=" in name_val:
                                n, v = name_val.split("=", 1)
                                cookies.append({"name": n, "value": v})
                direct = re.search(r'(https?://[^\s"\']+\.(?:m3u8|mp4)[^\s"\']*)', html)
                video_url = direct.group(1) if direct else _extract_m3u8(html)
                if video_url:
                    return StreamInfo(
                        url=video_url, cookies=cookies,
                        user_agent=headers["User-Agent"], referer=url,
                    )
    except Exception as exc:
        log.debug("Direct Kwik resolve failed: %s", exc)

    # Fallback to FlareSolverr
    sol = await solver.request(url, cache=False)
    if not sol:
        return None
    html = sol.get("response", "")
    cookies = sol.get("cookies", [])
    user_agent = sol.get("userAgent", "Mozilla/5.0")
    direct = re.search(r'(https?://[^\s"\']+\.(?:m3u8|mp4)[^\s"\']*)', html)
    video_url = direct.group(1) if direct else _extract_m3u8(html)
    if video_url:
        return StreamInfo(url=video_url, cookies=cookies, user_agent=user_agent, referer=url)
    return None


def _parse_resolution_buttons(html: str) -> list[tuple[int, str, bool, str]]:
    entries: list[tuple[int, str, bool, str]] = []
    menu_m = re.search(
        r'<div[^>]+id=["\']resolutionMenu["\'][^>]*>(.*?)</div>', html, re.I | re.S,
    )
    if not menu_m:
        return entries
    for btn_m in re.finditer(
        r"<button\b([^>]*?)>(.*?)</button>", menu_m.group(1), re.I | re.S,
    ):
        attrs = btn_m.group(1)
        text = btn_m.group(2).strip()
        src_m = re.search(r'data-src=["\']([^"\']+kwik\.[^"\']+)["\']', attrs, re.I)
        if not src_m:
            continue
        kwik_url = src_m.group(1)
        res_m = re.search(r'data-resolution=["\']?(\d+)["\']?', attrs, re.I)
        if res_m:
            res = int(res_m.group(1))
        else:
            res_m = re.match(r"(\d+)\s*p", text, re.I)
            if not res_m:
                continue
            res = int(res_m.group(1))

        is_dub = False
        if re.search(r'''data-audio\s*=\s*["']eng["']''', attrs, re.I):
            is_dub = True
        elif re.search(r'''class\s*=\s*["'][^"']*eng[^"']*["']''', attrs, re.I):
            is_dub = True
        elif "eng" in attrs.lower() or "dub" in text.lower():
            is_dub = True

        fansub_m = re.search(r'data-fansub=["\']([^"\']+)["\']', attrs, re.I)
        fansub = fansub_m.group(1) if fansub_m else (text.split("\u00b7")[0].strip())
        entries.append((res, kwik_url, is_dub, fansub))
    return entries


async def extract_stream(solver: Solver, play_url: str, quality: int = 1080, audio: str = "jpn") -> StreamInfo:
    sol = await solver.request(play_url, cache=True)
    if not sol:
        raise RuntimeError("FlareSolverr failed to fetch episode page")
    html = sol["response"]
    entries = _parse_resolution_buttons(html)

    quality_map: dict[int, tuple[str, bool, str]] = {}
    if entries:
        target_dub = audio == "eng"
        filtered = [e for e in entries if e[2] == target_dub]
        if not filtered:
            log.warning("Requested audio %s not found on play page — using available", audio)
            filtered = entries
        for res, url, is_dub, fansub in filtered:
            if res in quality_map:
                existing_is_dub = quality_map[res][1]
                if existing_is_dub != target_dub and is_dub == target_dub:
                    quality_map[res] = (url, is_dub, fansub)
            else:
                quality_map[res] = (url, is_dub, fansub)
    else:
        for url_match, q_str in re.findall(
            r'(?:href|data-src)=["\']([^"\']*kwik\.[^"\']+)["\'][^>]*>\s*(?:\S+\s+)?(\d+)p',
            html, re.I,
        ):
            with contextlib.suppress(ValueError):
                is_dub_found = "eng" in url_match.lower() or "dub" in url_match.lower()
                quality_map.setdefault(int(q_str), (url_match, is_dub_found, ""))

    if quality_map:
        qs = sorted(quality_map, reverse=True)
        chosen_q = next((q for q in qs if q <= quality), qs[-1])
        chosen_url, is_dub, fansub = quality_map[chosen_q]
    else:
        kwik_links = re.findall(
            rf'data-src=["\']?(https?://{_KWIK_DOMAINS}/[ef]/\w+)["\']?', html, re.I,
        )
        if not kwik_links:
            raise RuntimeError("No Kwik link found on episode page")
        chosen_url, is_dub, fansub = kwik_links[0], False, ""

    title_m = re.search(r"<title>([^<]+)</title>", html)
    title = title_m.group(1).strip() if title_m else ""

    info = await _resolve_kwik(solver, chosen_url)
    if not info:
        raise RuntimeError(f"Could not resolve Kwik URL: {chosen_url}")
    info.title = title
    info.audio = "eng" if is_dub else "jpn"
    info.fansub = fansub
    return info
