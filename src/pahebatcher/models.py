"""Data models for pahebatcher."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class EpisodeInfo:
    number: float
    session: str
    title: str
    fansub: str
    audio: str
    play_url: str

    @property
    def ep_str(self) -> str:
        return str(int(self.number)) if self.number == int(self.number) else str(self.number)

    @property
    def label(self) -> str:
        num = int(self.number) if self.number == int(self.number) else self.number
        dub = "  [yellow]DUB[/yellow]" if self.audio == "eng" else ""
        import re

        titl = re.sub(r"\s+(?:DUB|SUB)\s*$", "", self.title or "", flags=re.I).strip() or "\u2014"
        return f"Ep [cyan]{num:>4}[/cyan]  {titl}{dub}"


@dataclass
class AnimeInfo:
    session: str
    title: str
    host: str
    total: int = 0
    episodes: list[EpisodeInfo] = field(default_factory=list)
    has_session: bool = False

    def get_variant(self, number: float, audio: str) -> EpisodeInfo | None:
        for ep in self.episodes:
            if ep.number == number and ep.audio == audio:
                return ep
        return None

    def get_all_variants(self, number: float) -> list[EpisodeInfo]:
        return [ep for ep in self.episodes if ep.number == number]


@dataclass
class StreamInfo:
    url: str
    cookies: list[dict]
    user_agent: str
    referer: str
    title: str = ""
    audio: str = "jpn"
    fansub: str = ""

    @property
    def headers(self) -> dict[str, str]:
        hdrs = {"User-Agent": self.user_agent, "Referer": self.referer}
        if self.cookies:
            hdrs["Cookie"] = "; ".join(f"{c['name']}={c['value']}" for c in self.cookies)
        return hdrs


@dataclass
class AppContext:
    output_dir: str
    cache_dir: Path
    quality: int
    audio_lang: str
    max_parallel: int
    hls_workers: int
    keep_temp: bool
    list_only: bool
    flaresolverr_url: str

    @classmethod
    def defaults(cls) -> AppContext:
        import os

        return cls(
            output_dir="./downloads",
            cache_dir=Path("pahe_cache"),
            quality=1080,
            audio_lang="jpn",
            max_parallel=2,
            hls_workers=24,
            keep_temp=False,
            list_only=False,
            flaresolverr_url=os.getenv("FLARESOLVERR_URL", "http://localhost:8191/v1"),
        )
