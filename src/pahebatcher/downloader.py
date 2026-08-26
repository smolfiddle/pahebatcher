"""Episode and batch download orchestrator with two-stage prefetch pipeline."""

from __future__ import annotations

import asyncio
import logging
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from rich import box
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table

from pahebatcher.cache import TTLCache
from pahebatcher.extract.kwik import extract_stream
from pahebatcher.extract.m3u8 import fetch_m3u8, resolve_m3u8
from pahebatcher.models import AnimeInfo, AppContext, EpisodeInfo, StreamInfo
from pahebatcher.store import SegmentStore
from pahebatcher.ui.dashboard import Dashboard
from pahebatcher.utils import audio_badge, ep_prefix, fmt_bytes, sanitize

if TYPE_CHECKING:
    from pahebatcher.http import HttpClient
    from pahebatcher.solver import Solver

log = logging.getLogger(__name__)


class EpisodeDownloader:
    def __init__(
        self, ctx: AppContext, anime: AnimeInfo, dash: Dashboard,
        http: HttpClient, solver: Solver,
        aes_key_cache: TTLCache | None = None,
    ) -> None:
        self.ctx = ctx
        self.anime = anime
        self.dash = dash
        self.http = http
        self.solver = solver
        self._aes_key_cache = aes_key_cache or TTLCache(ttl=3600.0, max_size=128)

    async def run(self, ep: EpisodeInfo, info: StreamInfo) -> Path | None:
        key = ep.ep_str
        title = ep.title or info.title or f"Episode {ep.ep_str}"
        label = f"Ep {ep.ep_str} \u2014 {title}"
        store = SegmentStore(self.ctx.cache_dir, self.anime.title, self.anime.session, ep.ep_str, ep.audio)
        loop = asyncio.get_running_loop()

        await loop.run_in_executor(None, store.save_metadata, self.anime.title, self.anime.session)

        outdir = Path(self.ctx.output_dir)
        outdir.mkdir(parents=True, exist_ok=True)
        prefix = ep_prefix(ep.ep_str)
        fname = sanitize(f"Ep {prefix} - {title}") or f"ep_{ep.ep_str}"
        out = outdir / f"{fname}.mp4"

        if out.exists() and out.stat().st_size > 0:
            self.dash.mark_done(key, f"Ep {ep.ep_str} (already exists)")
            return out

        self.dash.add_ep(key, label)
        max_attempts = self.ctx.auto_retry + 1
        for attempt in range(max_attempts):
            try:
                hdrs = info.headers

                m3u8_txt = await fetch_m3u8(self.http, info.url, hdrs)
                segments = await resolve_m3u8(self.http, m3u8_txt, info.url, hdrs)
                if not segments:
                    raise RuntimeError("No segments found in M3U8")

                n = len(segments)
                done_set = await loop.run_in_executor(None, store.done_indices)
                pending = [(i, s) for i, s in enumerate(segments) if i not in done_set]

                self.dash.set_total(key, n)
                self.dash.mark_downloading(key, label)
                for _ in done_set:
                    self.dash.seg_done(key, 0)

                key_map: dict[str, bytes] = {}
                unique_keys: set[str] = {str(s["key_url"]) for s in segments if s["key_url"]}
                for kurl in unique_keys:
                    cached_any: Any = await self._aes_key_cache.get(kurl)
                    if isinstance(cached_any, bytes):
                        key_map[kurl] = cached_any
                    else:
                        key_data = await self.http.get(kurl, hdrs)
                        key_map[kurl] = key_data
                        await self._aes_key_cache.set(kurl, key_data)

                seg_sem = asyncio.Semaphore(self.ctx.hls_workers)

                async def fetch_one(idx: int, seg: dict[str, Any]) -> None:
                    async with seg_sem:
                        raw = await self.http.get(seg["url"], hdrs)
                        if seg["key_url"]:
                            from Cryptodome.Cipher import AES
                            cipher = AES.new(key_map[seg["key_url"]], AES.MODE_CBC, iv=seg["iv"])
                            raw = cipher.decrypt(raw)
                        await loop.run_in_executor(None, store.write_seg, idx, raw)
                        self.dash.seg_done(key, len(raw))

                # Use return_exceptions to avoid cancelling siblings on first failure
                results = await asyncio.gather(
                    *(asyncio.create_task(fetch_one(i, s)) for i, s in pending),
                    return_exceptions=True,
                )
                failed_segs = [r for r in results if isinstance(r, BaseException)]
                if failed_segs:
                    raise RuntimeError(f"{len(failed_segs)}/{len(pending)} segments failed")

                self.dash.mark_remuxing(key, f"Ep {ep.ep_str}")
                ok = await loop.run_in_executor(None, store.assemble, n, out)
                if not ok:
                    raise RuntimeError("Assembly failed")

                if not self.ctx.keep_temp:
                    await loop.run_in_executor(None, store.cleanup)

                self.dash.mark_done(key, f"Ep {ep.ep_str} \u2014 {title[:34]}")
                return out
            except Exception as exc:
                is_last = attempt == max_attempts - 1
                # Don't retry permanent parse errors
                msg = str(exc)
                is_permanent = "No Kwik link" in msg or "No segments" in msg
                if is_permanent:
                    is_last = True
                if is_last:
                    log.exception("Episode %s failed after %d attempts", ep.ep_str, max_attempts)
                    self.dash.mark_fail(key, f"Ep {ep.ep_str} \u2014 {exc!s:.40}")
                    return None
                log.warning(
                    "Episode %s failed (attempt %d/%d): %s — retrying",
                    ep.ep_str, attempt + 1, max_attempts, exc,
                )
                self.dash.mark_retry(key, f"Ep {ep.ep_str} retry {attempt+1}/{max_attempts-1}")
                await asyncio.sleep(1.5 * (2**attempt))
        return None


class BatchOrchestrator:
    def __init__(self, ctx: AppContext, anime: AnimeInfo, http: HttpClient, solver: Solver) -> None:
        self.ctx = ctx
        self.anime = anime
        self.http = http
        self.solver = solver
        self._results: dict[str, Path | None] = {}

    def _find_existing(self, ep: EpisodeInfo) -> Path | None:
        outdir = Path(self.ctx.output_dir)
        if not outdir.exists():
            return None
        prefix = ep_prefix(ep.ep_str)
        for p in outdir.iterdir():
            if not p.is_file() or p.suffix not in (".mp4",):
                continue
            is_target = p.name.startswith(f"Ep {prefix}") or p.name.startswith(f"Ep_{prefix}")
            if is_target and p.stat().st_size > 0:
                return p
        return None

    async def download(self, episodes: list[EpisodeInfo]) -> dict[str, Path | None]:
        loop = asyncio.get_running_loop()
        start = time.time()
        max_batch_attempts = self.ctx.auto_retry + 1
        remaining: list[EpisodeInfo] = list(episodes)
        shared_key_cache = TTLCache(ttl=3600.0, max_size=128)

        for batch_attempt in range(max_batch_attempts):
            if not remaining:
                break
            is_retry = batch_attempt > 0
            if is_retry:
                from pahebatcher.ui.console import console

                console.print(
                    f"\n  [yellow]Retrying {len(remaining)} failed episode(s) "
                    f"(attempt {batch_attempt+1}/{max_batch_attempts})...[/yellow]"
                )
                await asyncio.sleep(2 * batch_attempt)

            # Dashboard for this batch attempt
            dash_episodes = remaining if is_retry else episodes
            dash = Dashboard(len(dash_episodes))
            for ep in dash_episodes:
                label = f"Ep {ep.ep_str} \u2014 {ep.title or 'Pending...'}"
                dash.add_ep(ep.ep_str, label)
                dash.mark_waiting(ep.ep_str, label)
            # For retry, also show already-done eps as done
            if is_retry:
                for ep in episodes:
                    if ep.session in self._results and self._results[ep.session] is not None:
                        dash.add_ep(ep.ep_str, f"Ep {ep.ep_str} \u2014 {ep.title or ''}")
                        dash.mark_done(ep.ep_str, f"Ep {ep.ep_str} (done)")

            resolve_queue: asyncio.Queue[EpisodeInfo] = asyncio.Queue()
            for ep in remaining:
                await resolve_queue.put(ep)

            resolve_ahead = self.ctx.resolve_ahead
            download_queue: asyncio.Queue[tuple[EpisodeInfo, StreamInfo] | None] = asyncio.Queue(
                maxsize=resolve_ahead + self.ctx.max_parallel,
            )

            async def resolver() -> None:
                while not resolve_queue.empty():
                    raw_ep = await resolve_queue.get()
                    key = raw_ep.ep_str
                    variants = self.anime.get_all_variants(raw_ep.number)
                    ep = self.anime.get_variant(raw_ep.number, self.ctx.audio_lang)
                    if not ep:
                        ep = variants[0]

                    display_title = re.sub(
                        r"\s+\(?(?:dub|sub)\)?$", "",
                        ep.title or "Episode " + ep.ep_str, flags=re.I,
                    ).strip()

                    existing = await loop.run_in_executor(None, self._find_existing, ep)
                    if existing:
                        self._results[ep.session] = existing
                        dash.add_ep(key, f"Ep {ep.ep_str} \u2014 {display_title}")
                        dash.mark_done(key, f"Ep {ep.ep_str} (already exists)")
                        resolve_queue.task_done()
                        continue

                    dash.mark_resolving(key, f"Ep {ep.ep_str} \u2014 Resolving...")
                    max_attempts = self.ctx.auto_retry + 1
                    info: StreamInfo | None = None
                    for r_attempt in range(max_attempts):
                        try:
                            info = await asyncio.wait_for(
                                extract_stream(
                                    self.solver, ep.play_url, self.ctx.quality,
                                    self.ctx.audio_lang, self.ctx.cookie_string,
                                ),
                                timeout=120.0,
                            )
                            break
                        except TimeoutError:
                            is_last = r_attempt == max_attempts - 1
                            if is_last:
                                log.error(
                                    "Resolution timed out for Ep %s after %d attempts",
                                    ep.ep_str, max_attempts,
                                )
                                dash.mark_fail(key, f"Ep {ep.ep_str}: Resolution Timeout")
                                self._results[ep.session] = None
                            else:
                                log.warning(
                                    "Resolution timeout Ep %s (attempt %d/%d)",
                                    ep.ep_str, r_attempt + 1, max_attempts,
                                )
                                dash.mark_retry(key, f"Ep {ep.ep_str} retry {r_attempt+1}/{max_attempts-1}")
                                await asyncio.sleep(2 * (2**r_attempt))
                        except Exception as exc:
                            msg = str(exc)
                            is_permanent = "No Kwik link" in msg or "No anime UUID" in msg
                            is_last = r_attempt == max_attempts - 1 or is_permanent
                            if is_last:
                                log.error("Failed to resolve Ep %s: %s", ep.ep_str, exc)
                                dash.mark_fail(key, f"Ep {ep.ep_str}: {exc!s:.35}")
                                self._results[ep.session] = None
                                break
                            log.warning(
                                "Failed to resolve Ep %s (attempt %d/%d): %s — retrying",
                                ep.ep_str, r_attempt + 1, max_attempts, exc,
                            )
                            dash.mark_retry(key, f"Ep {ep.ep_str} retry {r_attempt+1}/{max_attempts-1}")
                            await asyncio.sleep(1.5 * (2**r_attempt))
                    if info is not None:
                        ep.audio = info.audio
                        if info.title and (not ep.title or ep.title == "?"):
                            ep.title = info.title
                        clean_label = re.sub(
                            r"\s+\(?(?:dub|sub)\)?$", "",
                            ep.title or f"Episode {ep.ep_str}", flags=re.I,
                        ).strip()
                        real_label = f"Ep {ep.ep_str} \u2014 {clean_label}"
                        dash.add_ep(key, real_label)
                        dash.mark_queued(key, real_label)
                        await download_queue.put((ep, info))
                    resolve_queue.task_done()

                for _ in range(self.ctx.max_parallel):
                    await download_queue.put(None)

            async def download_worker() -> None:
                ep_dl = EpisodeDownloader(
                    self.ctx, self.anime, dash, self.http, self.solver, aes_key_cache=shared_key_cache,
                )
                while True:
                    item = await download_queue.get()
                    if item is None:
                        break
                    ep, info = item
                    path = await ep_dl.run(ep, info)
                    self._results[ep.session] = path
                    download_queue.task_done()

            dash.start()
            try:
                tasks = [asyncio.create_task(resolver())]
                tasks.extend(
                    asyncio.create_task(download_worker()) for _ in range(self.ctx.max_parallel)
                )
                await asyncio.gather(*tasks)
                await asyncio.sleep(0.4)
            finally:
                dash.stop()

            # Check for still-failed after this batch attempt
            failed = [ep for ep in remaining if self._results.get(ep.session) is None]
            if not failed:
                remaining = []
                break
            remaining = failed
            if batch_attempt == max_batch_attempts - 1:
                break

        self._print_summary(episodes, time.time() - start)
        return self._results

    def _print_summary(self, episodes: list[EpisodeInfo], elapsed: float) -> None:
        from pahebatcher.ui.console import console

        h, rem = divmod(int(elapsed), 3600)
        m, s = divmod(rem, 60)
        time_str = f"{h}h {m}m {s}s" if h else f"{m}m {s}s" if m else f"{s}s"
        ok = sum(1 for v in self._results.values() if v is not None)
        fail = len(self._results) - ok

        console.print()
        console.print(Rule("[bold green] Download Complete [/bold green]", style="green"))

        table = Table(
            box=box.SIMPLE_HEAVY, show_header=True,
            header_style="bold cyan", border_style="dim",
        )
        table.add_column("Ep", style="cyan", width=6, justify="right")
        table.add_column("Title", style="bold white", ratio=1, overflow="ellipsis")
        table.add_column("Audio", width=5)
        table.add_column("Status", justify="center", width=10)
        table.add_column("Size", justify="right", width=10, style="cyan")
        table.add_column("File", style="dim", ratio=1, overflow="ellipsis")

        for ep in episodes:
            path = self._results.get(ep.session)
            badge = "[bold green]\u2713  done[/bold green]" if path else "[red]\u2717 failed[/red]"
            size = fmt_bytes(path.stat().st_size) if path and path.exists() else "\u2014"
            fname = path.name if path else "\u2014"
            table.add_row(ep.ep_str, ep.title or "\u2014", audio_badge(ep.audio), badge, size, fname)

        console.print(table)
        status_line = f"[bold green]\u2713 {ok} completed[/bold green]"
        if fail:
            status_line += f"  [bold red]\u2717 {fail} failed[/bold red]"
        console.print(Panel(
            f"  {status_line}\n"
            f"  [dim]Time:[/dim]      [cyan]{time_str}[/cyan]\n"
            f"  [dim]Saved to:[/dim]  {self.ctx.output_dir}",
            border_style="green" if not fail else "yellow", box=box.ROUNDED,
        ))
