"""Persistent watchlist for pahebatcher — add/list/show/remove/check."""

from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from pahebatcher.config_manager import ConfigManager
from pahebatcher.extract.scanner import AnimePaheScanner, parse_anime_url
from pahebatcher.utils import audio_badge, sanitize

DEFAULT_WATCHLIST_PATH = Path("watchlist.json")


def get_watchlist_path(path: Path | None = None) -> Path:
    if path is not None:
        return path
    env = os.getenv("WATCHLIST_PATH")
    if env:
        return Path(env)
    return DEFAULT_WATCHLIST_PATH


@dataclass
class WatchlistEntry:
    url: str
    session: str
    host: str
    title: str
    quality: int
    audio_lang: str
    output_dir: str
    max_parallel: int
    hls_workers: int
    keep_temp: bool
    auto_retry: int
    added_at: float
    last_checked: float | None = None
    downloaded: list[float] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WatchlistEntry:
        # downloaded history: list of episode numbers (float)
        raw_dl = data.get("downloaded", [])
        dl_list: list[float] = []
        if isinstance(raw_dl, list):
            for v in raw_dl:
                try:
                    dl_list.append(float(v))
                except Exception:
                    continue
        return cls(
            url=data["url"],
            session=data["session"],
            host=data["host"],
            title=data.get("title", data["session"]),
            quality=int(data.get("quality", 1080)),
            audio_lang=str(data.get("audio_lang", "jpn")),
            output_dir=str(data.get("output_dir", "./downloads")),
            max_parallel=int(data.get("max_parallel", 2)),
            hls_workers=int(data.get("hls_workers", 24)),
            keep_temp=bool(data.get("keep_temp", False)),
            auto_retry=int(data.get("auto_retry", 2)),
            added_at=float(data.get("added_at", time.time())),
            last_checked=data.get("last_checked"),
            downloaded=dl_list,
        )


class WatchlistManager:
    @staticmethod
    def load(path: Path | None = None) -> list[WatchlistEntry]:
        p = get_watchlist_path(path)
        if not p.exists():
            return []
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return []
        # Support both {"entries": [...]} and plain list for backward compat
        if isinstance(raw, dict) and "entries" in raw:
            raw_list = raw["entries"]
        elif isinstance(raw, list):
            raw_list = raw
        else:
            return []
        entries: list[WatchlistEntry] = []
        for item in raw_list:
            try:
                entries.append(WatchlistEntry.from_dict(item))
            except Exception:
                continue
        return entries

    @staticmethod
    def save(entries: list[WatchlistEntry], path: Path | None = None) -> None:
        p = get_watchlist_path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        data = [e.to_dict() for e in entries]
        # Atomic write via tmp rename
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.rename(p)

    @staticmethod
    def find_entry(
        entries: list[WatchlistEntry], identifier: str
    ) -> tuple[int, WatchlistEntry] | None:
        ident = identifier.strip()
        # 1. numeric index (1-based)
        if ident.isdigit():
            idx = int(ident) - 1
            if 0 <= idx < len(entries):
                return idx, entries[idx]
        # 2. try parse as URL -> match session
        try:
            _, sess = parse_anime_url(ident)
            for i, e in enumerate(entries):
                if e.session == sess:
                    return i, e
        except Exception:
            pass
        # 3. direct session uuid match
        for i, e in enumerate(entries):
            if e.session == ident or e.url == ident:
                return i, e
        # 4. substring title/URL match (case-insensitive)
        low = ident.lower()
        for i, e in enumerate(entries):
            if low in e.title.lower() or low in e.url.lower() or low in e.session.lower():
                return i, e
        return None

    @staticmethod
    def add_entry(entry: WatchlistEntry, path: Path | None = None) -> tuple[bool, WatchlistEntry]:
        """Add or update entry. Returns (is_new, entry)."""
        entries = WatchlistManager.load(path)
        for i, e in enumerate(entries):
            if e.session == entry.session:
                # Update existing — keep added_at, downloaded history, and real title
                old_added = e.added_at
                old_downloaded = list(e.downloaded)
                if e.title and e.title != e.session and entry.title == entry.session:
                    entry.title = e.title
                entry.added_at = old_added
                # Preserve downloaded history unless caller explicitly set it
                if not entry.downloaded and old_downloaded:
                    entry.downloaded = old_downloaded
                entries[i] = entry
                WatchlistManager.save(entries, path)
                return False, entry
        entries.append(entry)
        WatchlistManager.save(entries, path)
        return True, entry

    @staticmethod
    def remove_entry(identifier: str, path: Path | None = None) -> WatchlistEntry | None:
        entries = WatchlistManager.load(path)
        found = WatchlistManager.find_entry(entries, identifier)
        if not found:
            return None
        idx, ent = found
        entries.pop(idx)
        WatchlistManager.save(entries, path)
        return ent


# ── CLI helpers ──────────────────────────────────────────────────────────


def cli_list(path: Path | None = None) -> None:
    from rich import box
    from rich.table import Table

    from pahebatcher.ui.console import console

    entries = WatchlistManager.load(path)
    if not entries:
        console.print("\n  [dim]No watchlist entries.[/dim]")
        console.print(
            "  [dim]Add one with:[/dim] "
            "[cyan]pahebatcher watchlist add <URL> [-q 720] [--audio eng] [-o DIR][/cyan]",
        )
        return
    t = Table(box=box.ROUNDED, header_style="bold cyan", title=f"watchlist ({len(entries)} series)")
    t.add_column("#", justify="right", style="dim", width=4)
    t.add_column("Title", ratio=1)
    t.add_column("Quality", justify="center", width=8)
    t.add_column("Audio", justify="center", width=6)
    t.add_column("Output", style="dim", overflow="ellipsis", max_width=24)
    t.add_column("URL", style="dim", overflow="ellipsis", max_width=40)
    for i, e in enumerate(entries, 1):
        t.add_row(
            str(i),
            e.title,
            f"{e.quality}p",
            audio_badge(e.audio_lang),
            e.output_dir,
            e.url,
        )
    console.print(t)


def cli_show(identifier: str, path: Path | None = None) -> None:
    from rich.panel import Panel

    from pahebatcher.ui.console import console

    entries = WatchlistManager.load(path)
    if not entries:
        console.print("\n  [dim]No watchlist entries.[/dim]")
        return
    found = WatchlistManager.find_entry(entries, identifier)
    if not found:
        console.print(f"\n  [red]✗ No entry found for:[/red] {identifier}")
        console.print("  [dim]Use 'pahebatcher watchlist list' to see available entries.[/dim]")
        sys.exit(1)
    _, e = found
    added = time.strftime("%Y-%m-%d %H:%M", time.localtime(e.added_at)) if e.added_at else "—"
    checked = time.strftime("%Y-%m-%d %H:%M", time.localtime(e.last_checked)) if e.last_checked else "never"
    audio_str = "SUB" if e.audio_lang == "jpn" else "DUB"
    dl_info = (
        f"{len(e.downloaded)} eps" if e.downloaded else "none"
    )
    # Show truncated list for brevity
    dl_detail = ""
    if e.downloaded:
        nums = sorted(e.downloaded)[:12]
        dl_detail = ", ".join(str(int(n) if n == int(n) else n) for n in nums)
        if len(e.downloaded) > 12:
            dl_detail += f" … +{len(e.downloaded)-12}"
        dl_detail = f" [dim]({dl_detail})[/dim]"
    console.print(Panel(
        f"  [dim]Title:[/dim]       [bold white]{e.title}[/bold white]\n"
        f"  [dim]URL:[/dim]         [cyan]{e.url}[/cyan]\n"
        f"  [dim]Session:[/dim]     {e.session}\n"
        f"  [dim]Host:[/dim]        {e.host}\n"
        f"  [dim]Quality:[/dim]     [cyan]{e.quality}p[/cyan]\n"
        f"  [dim]Audio:[/dim]       {audio_str} ({e.audio_lang})\n"
        f"  [dim]Output:[/dim]      {e.output_dir}\n"
        f"  [dim]Parallel:[/dim]    {e.max_parallel}  [dim]Workers:[/dim] {e.hls_workers}\n"
        f"  [dim]Keep-temp:[/dim] {e.keep_temp}  [dim]Retry:[/dim] {e.auto_retry}\n"
        f"  [dim]Downloaded:[/dim]  {dl_info}{dl_detail}\n"
        f"  [dim]Added:[/dim]       {added}\n"
        f"  [dim]Last checked:[/dim] {checked}",
        title=f"[bold cyan]{e.title}[/bold cyan]",
        border_style="cyan",
    ))


def cli_remove(identifier: str, path: Path | None = None) -> None:
    from pahebatcher.ui.console import console

    removed = WatchlistManager.remove_entry(identifier, path)
    if not removed:
        console.print(f"\n  [red]✗ No entry found for:[/red] {identifier}")
        sys.exit(1)
    remaining = len(WatchlistManager.load(path))
    console.print(f"  [green]✓ Removed[/green] '{removed.title}' [dim]({remaining} remaining)[/dim]")


def cli_reset(identifier: str, path: Path | None = None) -> None:
    from pahebatcher.ui.console import console

    entries = WatchlistManager.load(path)
    found = WatchlistManager.find_entry(entries, identifier)
    if not found:
        console.print(f"\n  [red]✗ No entry found for:[/red] {identifier}")
        sys.exit(1)
    _idx, entry = found
    cleared = len(entry.downloaded)
    entry.downloaded = []
    WatchlistManager.save(entries, path)
    console.print(f"  [green]✓ Reset[/green] '{entry.title}' [dim](cleared {cleared} history)[/dim]")


def cli_add(url: str, quality: int | None, audio_lang: str | None, output: str | None,
            parallel: int | None, workers: int | None, keep_temp: bool, retry: int | None,
            path: Path | None = None) -> None:
    from pahebatcher.ui.console import console

    try:
        host, session = parse_anime_url(url)
    except ValueError as exc:
        console.print(f"\n  [red]✗ Invalid URL:[/red] {exc}")
        sys.exit(1)

    cm = ConfigManager()
    cm.load()
    q = quality if quality is not None else int(cm.get("quality"))
    a = audio_lang if audio_lang is not None else str(cm.get("audio_lang"))
    o = output if output is not None else str(cm.get("output_dir"))
    par = parallel if parallel is not None else int(cm.get("max_parallel"))
    w = workers if workers is not None else int(cm.get("hls_workers"))
    par = max(1, min(6, par))
    w = max(8, min(32, w))
    auto_retry = retry if retry is not None else int(cm.get("auto_retry"))
    auto_retry = max(0, min(2, auto_retry))
    # Validate
    if q not in (360, 720, 1080):
        console.print(f"  [red]✗ Invalid quality:[/red] {q}")
        sys.exit(1)
    if a not in ("jpn", "eng"):
        console.print(f"  [red]✗ Invalid audio:[/red] {a}")
        sys.exit(1)
    # Persistent default guard: /tmp is volatile (tmpfs) and will be wiped on reboot
    if o == "/tmp" or o.startswith("/tmp/"):
        console.print(
            f"  [yellow]⚠ Output is volatile tmpfs:[/yellow] {o}\n"
            f"  [dim]Use a persistent path like ./downloads or ~/anime. "
            f"With /tmp, folder-gone reset will redownload after reboot.[/dim]",
        )

    canonical_url = f"https://{host}/anime/{session}"
    # Try to fetch title quickly via cache without network if possible? Use session as placeholder
    # We'll attempt to keep existing title if updating
    title = session  # placeholder, will be updated on first check
    existing_entries = WatchlistManager.load(path)
    for e in existing_entries:
        if e.session == session:
            title = e.title
            break

    entry = WatchlistEntry(
        url=canonical_url,
        session=session,
        host=host,
        title=title,
        quality=q,
        audio_lang=a,
        output_dir=o,
        max_parallel=par,
        hls_workers=w,
        keep_temp=keep_temp,
        auto_retry=auto_retry,
        added_at=time.time(),
    )
    is_new, saved = WatchlistManager.add_entry(entry, path)
    verb = "Added" if is_new else "Updated"
    audio_str = "SUB" if a == "jpn" else "DUB"
    console.print(f"  [green]✓ {verb}[/green] [bold]{saved.title}[/bold] [dim]({saved.session})[/dim]")
    console.print(f"    [dim]URL:[/dim]     [cyan]{saved.url}[/cyan]")
    console.print(
        f"    [dim]Quality:[/dim] {saved.quality}p  [dim]Audio:[/dim] {audio_str}"
        f"  [dim]Output:[/dim] {saved.output_dir}",
    )
    if is_new:
        console.print(
            f"  [dim]Saved to {get_watchlist_path(path)}"
            f" ({len(WatchlistManager.load(path))} entries)[/dim]",
        )
        console.print("  [dim]Run 'pahebatcher watchlist check' to download new episodes.[/dim]")
    else:
        console.print("  [dim]Entry updated.[/dim]")


async def run_watchlist_check(
    path: Path | None = None,
    verbose: bool = False,
    filter_id: str | None = None,
) -> None:
    import logging

    from rich import box
    from rich.panel import Panel
    from rich.rule import Rule
    from rich.table import Table

    from pahebatcher.downloader import BatchOrchestrator
    from pahebatcher.http import HttpClient
    from pahebatcher.models import AppContext
    from pahebatcher.solver import Solver
    from pahebatcher.ui.console import console, print_banner

    cm = ConfigManager()
    cm.load()

    if verbose:
        logging.basicConfig(level=logging.DEBUG, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    print_banner()

    entries = WatchlistManager.load(path)
    if not entries:
        console.print("\n  [dim]No watchlist entries. Add one with:[/dim]")
        console.print("  [cyan]pahebatcher watchlist add <URL>[/cyan]")
        return

    if filter_id:
        found = WatchlistManager.find_entry(entries, filter_id)
        if not found:
            console.print(f"\n  [red]✗ No watchlist entry for:[/red] {filter_id}")
            sys.exit(1)
        entries = [found[1]]

    console.print(Rule(f"[bold white] Watchlist check — {len(entries)} series [/bold white]", style="cyan"))

    flaresolverr_url = os.getenv("FLARESOLVERR_URL", "http://localhost:8191/v1")
    flaresolverr_proxy = os.getenv("FLARESOLVERR_PROXY") or None
    cache_dir = Path("pahe_cache")
    resolve_ahead = int(cm.get("resolve_ahead"))
    cache_ttl_watch = 0  # force fresh for watchlist check
    cookie_string = str(cm.get("cookie_string"))

    console.print(Rule("[bold white] Checking Prerequisites [/bold white]", style="cyan"))
    console.print(f"  [dim]FlareSolverr:[/dim] {flaresolverr_url}  ", end="")

    solver = Solver(flaresolverr_url, proxy=flaresolverr_proxy, user_cookies=cookie_string)
    await solver.start()
    try:
        if not await solver.ping():
            console.print("[red]✗ not responding[/red]")
            console.print(
                "\n  [red bold]FlareSolverr is not running![/red bold]\n"
                "  [dim]Start it with Docker:[/dim]\n"
                "    docker run -d --name=flaresolverr -p 8191:8191 ghcr.io/flaresolverr/flaresolverr\n"
            )
            sys.exit(1)
        console.print("[green]✓ reachable[/green]")

        # Use max workers for shared client, but per-entry ctx controls batch parallelism
        max_workers = max((e.hls_workers for e in entries), default=24)
        http = HttpClient(max_workers)
        await http.start()
        try:
            total_new = 0
            total_done = 0
            total_failed = 0
            per_entry_summary: list[dict[str, Any]] = []

            for idx, entry in enumerate(entries, 1):
                console.print(
                    f"\n  [cyan][{idx}/{len(entries)}][/cyan] [bold]{entry.title}[/bold]"
                    f" [dim]{entry.url}[/dim]",
                )
                # Need to update last_checked after each entry
                try:
                    scanner = AnimePaheScanner(solver, entry.host, entry.session)
                    # Force fresh scan (TTL 0) to detect new episodes promptly
                    anime = await scanner.scan(
                        cache_dir, prefer_audio=entry.audio_lang, cache_ttl=cache_ttl_watch,
                    )
                except Exception as exc:
                    console.print(f"  [red]✗ Scan failed:[/red] {exc}")
                    per_entry_summary.append(
                        {"title": entry.title, "new": 0, "done": 0, "failed": 1, "error": str(exc)},
                    )
                    # update last_checked even on failure
                    entry.last_checked = time.time()
                    WatchlistManager.save(entries, path)
                    total_failed += 1
                    continue

                # Update title if it was placeholder or changed
                if anime.title and anime.title != "Unknown Anime":
                    entry.title = anime.title

                # Deduplicate to unique episode numbers, prefer requested audio
                # Mirrors prompts.noninteractive_episodes and downloader resolver logic
                unique_by_num: dict[float, Any] = {}
                for ep in anime.episodes:
                    if ep.number not in unique_by_num:
                        unique_by_num[ep.number] = ep
                    else:
                        # Prefer audio_lang variant
                        existing = unique_by_num[ep.number]
                        if existing.audio != entry.audio_lang and ep.audio == entry.audio_lang:
                            unique_by_num[ep.number] = ep
                # Actually better to use get_variant logic per number
                pending: list[Any] = []
                skipped_deleted = 0
                # For _find_existing we need ctx output_dir with sanitized title appended
                safe_title = sanitize(anime.title)
                full_output = os.path.join(entry.output_dir, safe_title)
                ctx_for_check = AppContext(
                    output_dir=full_output,
                    cache_dir=cache_dir,
                    quality=entry.quality,
                    audio_lang=entry.audio_lang,
                    max_parallel=entry.max_parallel,
                    hls_workers=entry.hls_workers,
                    keep_temp=entry.keep_temp,
                    list_only=False,
                    flaresolverr_url=flaresolverr_url,
                    resolve_ahead=resolve_ahead,
                    cache_ttl=cache_ttl_watch,
                    cookie_string=cookie_string,
                    auto_retry=entry.auto_retry,
                )
                # Folder-reset: if series folder no longer exists, forget deleted skip history
                downloaded_set = set(float(v) for v in (entry.downloaded or []))
                folder_exists = Path(full_output).exists()
                if not folder_exists and downloaded_set:
                    console.print(
                        f"  [dim]Folder not found ({full_output}) — resetting skip history[/dim]",
                    )
                    downloaded_set.clear()
                    entry.downloaded = []
                    WatchlistManager.save(entries, path)
                # Temporary orchestrator just for _find_existing helper (no network)
                tmp_orch = BatchOrchestrator(ctx_for_check, anime, http, solver)

                for num in sorted(unique_by_num.keys()):
                    # Resolve to desired audio variant if available
                    ep_variant = anime.get_variant(num, entry.audio_lang)
                    if not ep_variant:
                        # fallback to any variant for that number
                        variants = anime.get_all_variants(num)
                        if not variants:
                            continue
                        ep_variant = variants[0]
                    # Use existing file check (idempotency) + deleted skip
                    existing = None
                    try:
                        existing = tmp_orch._find_existing(ep_variant)
                    except Exception:
                        existing = None
                    if existing is not None:
                        # Backfill history: file present means it was downloaded
                        if num not in downloaded_set:
                            downloaded_set.add(num)
                        continue
                    # Not on disk — if folder exists and we have history, skip deleted
                    if folder_exists and num in downloaded_set:
                        skipped_deleted += 1
                        continue
                    pending.append(ep_variant)

                # Persist backfilled history even when up-to-date
                if downloaded_set != set(float(v) for v in (entry.downloaded or [])):
                    entry.downloaded = sorted(downloaded_set)
                    WatchlistManager.save(entries, path)

                if not pending:
                    if skipped_deleted:
                        console.print(
                            f"  [dim]Up to date — {len(unique_by_num)} episodes, "
                            f"{skipped_deleted} skipped (deleted)[/dim]",
                        )
                    else:
                        console.print(f"  [dim]Up to date — {len(unique_by_num)} episodes, 0 new[/dim]")
                    per_entry_summary.append({"title": entry.title, "new": 0, "done": 0, "failed": 0})
                    entry.last_checked = time.time()
                    WatchlistManager.save(entries, path)
                    continue

                console.print(
                    f"  [yellow]{len(pending)} new episode(s)[/yellow]"
                    f" [dim]({len(unique_by_num)} total)[/dim] → downloading to [cyan]{full_output}[/cyan]",
                )

                # Now actually download using orchestrator (reuses resolver + segment resume)
                orch = BatchOrchestrator(ctx_for_check, anime, http, solver)
                try:
                    results = await orch.download(pending)
                    # results is dict[session, Path|None]
                    done = sum(1 for v in results.values() if v is not None)
                    failed = len(results) - done
                    total_new += len(pending)
                    total_done += done
                    total_failed += failed
                    per_entry_summary.append(
                        {"title": entry.title, "new": len(pending), "done": done, "failed": failed},
                    )
                    # Update history for successfully downloaded episodes
                    for ep in pending:
                        if results.get(ep.session) is not None:
                            downloaded_set.add(float(ep.number))
                    entry.downloaded = sorted(downloaded_set)
                    if failed:
                        console.print(f"  [yellow]⚠ {failed} failed, {done} done[/yellow]")
                    else:
                        console.print(f"  [green]✓ {done} episode(s) completed[/green]")
                except Exception as exc:
                    console.print(f"  [red]✗ Download failed:[/red] {exc}")
                    if verbose:
                        raise
                    per_entry_summary.append(
                        {"title": entry.title, "new": len(pending), "done": 0,
                         "failed": len(pending), "error": str(exc)},
                    )
                    total_failed += len(pending)
                finally:
                    entry.last_checked = time.time()
                    # Persist history even if download failed partially (only successes counted)
                    if 'downloaded_set' in locals():
                        entry.downloaded = sorted(downloaded_set)
                    WatchlistManager.save(entries, path)
                    # Orphan cleanup per entry not needed here

            # Final summary
            console.print()
            console.print(Rule("[bold green] Watchlist check complete [/bold green]", style="green"))
            table = Table(
                box=box.SIMPLE_HEAVY, show_header=True,
                header_style="bold cyan", border_style="dim",
            )
            table.add_column("Series", style="bold white", ratio=1, overflow="ellipsis")
            table.add_column("New", justify="center", width=6)
            table.add_column("Done", justify="center", width=6, style="green")
            table.add_column("Failed", justify="center", width=8, style="red")
            for s in per_entry_summary:
                table.add_row(s["title"], str(s["new"]), str(s["done"]), str(s["failed"]))
            console.print(table)
            status = f"[green]✓ {total_done} new episodes downloaded[/green]"
            if total_failed:
                status += f"  [red]✗ {total_failed} failed[/red]"
            if total_new == 0:
                status = "[dim]No new episodes[/dim]"
            console.print(Panel(
                status, border_style="green" if not total_failed else "yellow", box=box.ROUNDED,
            ))
            if total_failed:
                console.print(
                    "  [dim]Tip: re-run 'pahebatcher watchlist check' to retry failed.[/dim]",
                )

        finally:
            await http.close()
    finally:
        await solver.close()

    # Cleanup orphaned cache (>24h) — reuse main.py logic
    import contextlib as _ctxlib
    import shutil as _shutil

    with _ctxlib.suppress(Exception):
        now = time.time()
        for p in cache_dir.glob("*/*/*.ts"):
            if now - p.stat().st_mtime > 86400:
                with _ctxlib.suppress(Exception):
                    _shutil.rmtree(p.parent.parent)
