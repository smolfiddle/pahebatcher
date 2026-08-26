"""Shared utility functions."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pahebatcher.models import EpisodeInfo


def sanitize(name: str) -> str:
    safe = re.sub(r"[^\w\s\-.]", "", name).strip().replace(" ", "_")
    return re.sub(r"_+", "_", safe)


def ep_prefix(ep_num: str) -> str:
    try:
        return f"{float(ep_num):05.1f}" if "." in ep_num else f"{int(ep_num):03d}"
    except (ValueError, TypeError):
        return ep_num


def fmt_bytes(n: int) -> str:
    size = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def compact_ep_range(episodes: list[EpisodeInfo]) -> str:
    if not episodes:
        return "none"
    nums = sorted({ep.number for ep in episodes})
    if len(nums) == 1:
        n = nums[0]
        return str(int(n) if n == int(n) else n)
    ranges: list[tuple[float, float]] = []
    s = p = nums[0]
    for n in nums[1:]:
        if n == p + 1:
            p = n
        else:
            ranges.append((s, p))
            s = p = n
    ranges.append((s, p))
    parts: list[str] = []
    for a, b in ranges:
        ai: int | float = int(a) if a == int(a) else a
        bi: int | float = int(b) if b == int(b) else b
        parts.append(str(ai) if a == b else f"{ai}\u2013{bi}")
    return ", ".join(parts)


def audio_badge(audio: str) -> str:
    return "SUB" if audio == "jpn" else "DUB"
