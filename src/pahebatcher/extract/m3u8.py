"""HLS M3U8 manifest parser."""

from __future__ import annotations

import contextlib
import re
from typing import Any
from urllib.parse import urljoin

from Cryptodome.Cipher import AES

from pahebatcher.http import HttpClient


def parse_m3u8(content: str, base_url: str) -> list[dict[str, Any]]:
    lines = content.splitlines()

    if "#EXT-X-STREAM-INF" in content:
        variants = [
            urljoin(base_url, lines[i + 1].strip())
            for i, line in enumerate(lines)
            if line.startswith("#EXT-X-STREAM-INF") and i + 1 < len(lines)
        ]
        if variants:
            return []  # caller must re-fetch the variant

    segments: list[dict[str, Any]] = []
    key_url: str | None = None
    key_iv: bytes | None = None
    seq_num = 0

    for line in lines:
        line = line.strip()
        if not line:
            continue
        if line.startswith("#EXT-X-MEDIA-SEQUENCE:"):
            with contextlib.suppress(ValueError):
                seq_num = int(line.split(":", 1)[1])
        elif line.startswith("#EXT-X-KEY:"):
            kv = {
                k: vq or vr
                for k, vq, vr in re.findall(r'([^,="]+)=(?:"([^"]+)"|([^,]+))', line[11:])
            }
            if kv.get("METHOD") == "AES-128":
                if "URI" in kv:
                    key_url = urljoin(base_url, kv["URI"])
                if iv_hex := kv.get("IV"):
                    h = iv_hex.lstrip("0xX").lstrip("0X")
                    key_iv = bytes.fromhex(("0" + h) if len(h) % 2 else h)
                else:
                    key_iv = None
            else:
                key_url = key_iv = None
        elif not line.startswith("#"):
            iv = key_iv or (seq_num.to_bytes(16, "big") if key_url else None)
            segments.append({
                "url": urljoin(base_url, line),
                "key_url": key_url,
                "iv": iv,
            })
            seq_num += 1

    return segments


async def fetch_m3u8(http: HttpClient, url: str, headers: dict[str, str] | None = None) -> str:
    data = await http.get(url, headers=headers)
    return data.decode("utf-8", errors="replace")


async def resolve_m3u8(http: HttpClient, content: str, base_url: str, headers: dict[str, str] | None = None) -> list[dict[str, Any]]:
    segments = parse_m3u8(content, base_url)
    if segments:
        return segments
    # Must be a master playlist — follow the last variant
    lines = content.splitlines()
    variants = [
        urljoin(base_url, lines[i + 1].strip())
        for i, line in enumerate(lines)
        if line.startswith("#EXT-X-STREAM-INF") and i + 1 < len(lines)
    ]
    if variants:
        sub = await fetch_m3u8(http, variants[-1], headers)
        return parse_m3u8(sub, variants[-1])
    return []
