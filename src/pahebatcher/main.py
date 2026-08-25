"""Main entry point — CLI args, service wiring, action dispatch."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import os
import shutil
import sys
import time
from pathlib import Path

from rich import box
from rich.panel import Panel
from rich.prompt import Prompt
from rich.rule import Rule
from rich.table import Table

from pahebatcher.config import VERSION
from pahebatcher.config_manager import ConfigManager
from pahebatcher.downloader import BatchOrchestrator
from pahebatcher.extract.scanner import AnimePaheScanner, parse_anime_url
from pahebatcher.http import HttpClient
from pahebatcher.models import AppContext
from pahebatcher.sessions import SessionManager
from pahebatcher.solver import Solver
from pahebatcher.stream import run_stream
from pahebatcher.ui.console import console, print_banner
from pahebatcher.ui.prompts import (
    confirm_download,
    interactive_discovery,
    noninteractive_episodes,
    select_episodes,
    wizard_config,
)
from pahebatcher.utils import audio_badge, compact_ep_range, fmt_bytes, sanitize

log = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pahebatcher",
        description=f"pahe-batcher v{VERSION} \u2014 AnimePahe Batch Downloader",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  %(prog)s https://animepahe.ru/anime/<uuid>                        # wizard\n"
            "  %(prog)s https://animepahe.ru/anime/<uuid> --all                  # all eps\n"
            "  %(prog)s https://animepahe.ru/anime/<uuid> --range 1-12           # season 1\n"
            "  %(prog)s https://animepahe.ru/anime/<uuid> --latest 3             # last 3\n"
            "  %(prog)s https://animepahe.ru/anime/<uuid> --list                 # list only\n"
            "  %(prog)s https://animepahe.ru/anime/<uuid> --audio eng --all      # dubbed\n"
            "  %(prog)s https://animepahe.ru/anime/<uuid> -s                     # stream\n"
            "\n"
            "Configuration:\n"
            "  %(prog)s config show                                               # view settings\n"
            "  %(prog)s config set quality 720                                    # save default quality\n"
            "  %(prog)s config reset                                              # reset to defaults\n"
        ),
    )

    parser.add_argument("url", metavar="URL", nargs="?", help="AnimePahe series URL")
    sel = parser.add_mutually_exclusive_group()
    sel.add_argument("--all", "-a", action="store_true", help="Download every episode")
    sel.add_argument("--range", "-r", metavar="RANGE", help='Episode range, e.g. "1-12" "1,4,7" "13-"')
    sel.add_argument("--latest", "-n", metavar="N", type=int, help="Download the latest N episodes")
    sel.add_argument("--stream", "-s", action="store_true", help="Stream episodes via MPV")
    parser.add_argument("--list", "-l", action="store_true", dest="list_only", help="List episodes and exit")
    parser.add_argument("-o", "--output", default="./downloads", help="Output directory")
    parser.add_argument(
        "-q", "--quality", metavar="Q", type=int, choices=[360, 720, 1080],
        default=None, help="Quality: 360, 720, or 1080",
    )
    parser.add_argument(
        "--audio", metavar="LANG", type=str, choices=["jpn", "eng"],
        default=None, dest="audio_lang", help="Audio: jpn=subbed, eng=dubbed",
    )
    parser.add_argument(
        "-j", "--parallel", metavar="N", type=int, default=None,
        help="Concurrent downloads (1-6)",
    )
    parser.add_argument(
        "-w", "--workers", metavar="N", type=int, default=None,
        help="HLS segment workers per episode (8-32)",
    )
    parser.add_argument("--keep-temp", action="store_true", help="Keep raw segment files")
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable debug logging")
    return parser


def build_config_parser() -> argparse.ArgumentParser:
    cfg = argparse.ArgumentParser(prog="pahebatcher config", add_help=True)
    cfg_sub = cfg.add_subparsers(dest="config_action")
    cfg_sub.add_parser("show", help="Show current configuration")
    set_p = cfg_sub.add_parser("set", help="Set a configuration value")
    set_p.add_argument("key", choices=[
        "quality", "audio_lang", "max_parallel", "hls_workers",
        "output_dir", "keep_temp", "resolve_ahead", "cache_ttl", "cookie_string",
    ])
    set_p.add_argument("value")
    cfg_sub.add_parser("reset", help="Reset all settings to defaults")
    return cfg


async def run(args: argparse.Namespace) -> None:
    # ── Load persistent config as defaults (CLI args override) ──────────────
    cm = ConfigManager()
    cm.load()

    print_banner()

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    quality = args.quality if args.quality is not None else int(cm.get("quality"))
    audio_lang = args.audio_lang if args.audio_lang is not None else str(cm.get("audio_lang"))
    parallel = args.parallel if args.parallel is not None else int(cm.get("max_parallel"))
    workers = args.workers if args.workers is not None else int(cm.get("hls_workers"))
    parallel = max(1, min(6, parallel))
    workers = max(8, min(32, workers))
    resolve_ahead = int(cm.get("resolve_ahead"))
    cache_ttl = int(cm.get("cache_ttl"))
    cookie_string = str(cm.get("cookie_string"))
    flaresolverr_url = os.getenv("FLARESOLVERR_URL", "http://localhost:8191/v1")
    flaresolverr_proxy = os.getenv("FLARESOLVERR_PROXY") or None
    cache_dir = Path("pahe_cache")

    # Prerequisites check
    console.print(Rule("[bold white] Checking Prerequisites [/bold white]", style="cyan"))
    console.print(f"  [dim]FlareSolverr:[/dim] {flaresolverr_url}  ", end="")

    solver = Solver(flaresolverr_url, proxy=flaresolverr_proxy, user_cookies=cookie_string)
    await solver.start()
    try:
        if not await solver.ping():
            console.print("[red]\u2717 not responding[/red]")
            console.print(
                "\n  [red bold]FlareSolverr is not running![/red bold]\n"
                "  [dim]Start it with Docker:[/dim]\n"
                "    docker run -d --name=flaresolverr -p 8191:8191 ghcr.io/flaresolverr/flaresolverr\n"
                "  [dim]Or set FLARESOLVERR_URL env var if it's on a different host.[/dim]"
            )
            sys.exit(1)
        console.print("[green]\u2713 reachable[/green]")

        http = HttpClient(workers)
        await http.start()

        try:
            # Discovery
            url = args.url
            if not url:
                url = await interactive_discovery(solver, "animepahe.com")
                if not url:
                    console.print("\n  [yellow]No anime selected. Exiting.[/yellow]")
                    return

            host, session = parse_anime_url(url)

            # Scan
            scanner = AnimePaheScanner(solver, host, session)
            anime = await scanner.scan(cache_dir, prefer_audio=audio_lang, cache_ttl=cache_ttl)

            badge = " [bold yellow][PARTIAL DOWNLOAD FOUND][/bold yellow]" if anime.has_session else ""
            console.print(
                f"  [green]\u2713[/green] [bold]{anime.title}[/bold]{badge}\n"
                f"  \u2014 [cyan]{len(anime.episodes)}[/cyan] episodes  "
                f"({compact_ep_range(anime.episodes)})"
            )

            if args.list_only:
                t = Table(box=box.SIMPLE, show_header=True, header_style="bold cyan")
                t.add_column("Ep", width=6, justify="right")
                t.add_column("Title", style="white")
                t.add_column("Audio", width=5)
                for ep in anime.episodes:
                    t.add_row(ep.ep_str, ep.title or "\u2014", audio_badge(ep.audio))
                console.print(t)
                return

            _scripted = bool(args.all or args.range or args.latest or args.stream)
            safe_title = sanitize(anime.title)
            default_output = os.path.join(args.output, safe_title)

            while True:
                if _scripted:
                    mode = "stream" if args.stream else "download"
                else:
                    console.print()
                    console.print(Rule("[bold white] Action [/bold white]", style="cyan"))
                    sessions = SessionManager.get_sessions(cache_dir)
                    total_cache = sum(s["size"] for s in sessions)
                    cache_hint = f" [dim]({fmt_bytes(total_cache)})[/dim]" if total_cache > 0 else ""

                    console.print(Panel(
                        "  [bold white]1[/bold white]  [cyan]Download[/cyan]"
                        "  [dim]\u00b7 save .mp4 files[/dim]\n"
                        "  [bold white]2[/bold white]  [cyan]Stream[/cyan]"
                        "    [dim]\u00b7 play in MPV[/dim]\n"
                        f"  [bold white]3[/bold white]  [cyan]Sessions & Cache[/cyan]{cache_hint}\n"
                        "  [bold white]4[/bold white]  [cyan]List[/cyan]"
                        "      [dim]\u00b7 show episode table[/dim]\n"
                        "  [bold white]5[/bold white]  [red]Exit[/red]",
                        title=f"[bold cyan]{anime.title}[/bold cyan]",
                        border_style="cyan", box=box.ROUNDED, padding=(0, 2),
                    ))
                    choice = Prompt.ask(
                        "  [cyan]Select action[/cyan]",
                        choices=["1", "2", "3", "4", "5"], default="1", show_choices=False,
                    )
                    if choice == "5":
                        break
                    if choice == "4":
                        t = Table(box=box.SIMPLE, show_header=True, header_style="bold cyan")
                        t.add_column("Ep", width=6, justify="right")
                        t.add_column("Title", style="white")
                        t.add_column("Audio", width=5)
                        for ep in anime.episodes:
                            t.add_row(ep.ep_str, ep.title or "\u2014", audio_badge(ep.audio))
                        console.print(t)
                        continue
                    if choice == "3":
                        new_url = SessionManager.run(cache_dir)
                        if new_url and new_url != args.url:
                            args.url = new_url
                            return await run(args)
                        continue
                    mode = {"1": "download", "2": "stream"}[choice]

                # Episode selection
                if args.all:
                    chosen = noninteractive_episodes(anime, "all")
                elif args.range:
                    chosen = noninteractive_episodes(anime, "range", range_str=args.range)
                    if not chosen:
                        console.print(f"  [red]\u2717 No episodes matched:[/red] {args.range}")
                        console.print(f"    Available: [cyan]{compact_ep_range(anime.episodes)}[/cyan]")
                        if _scripted:
                            sys.exit(1)
                        continue
                elif args.latest:
                    chosen = noninteractive_episodes(anime, "latest", latest_n=args.latest)
                else:
                    chosen = select_episodes(anime)

                if not chosen:
                    if _scripted:
                        break
                    continue

                if _scripted:
                    ctx = AppContext(
                        output_dir=default_output, cache_dir=cache_dir,
                        quality=quality, audio_lang=audio_lang,
                        max_parallel=parallel, hls_workers=workers,
                        keep_temp=args.keep_temp, list_only=False,
                        flaresolverr_url=flaresolverr_url,
                        resolve_ahead=resolve_ahead, cache_ttl=cache_ttl,
                        cookie_string=cookie_string,
                    )
                elif cm.is_customized():
                    audio_badge_text = "[cyan]SUB[/cyan]" if audio_lang == "jpn" else "[yellow]DUB[/yellow]"
                    console.print(
                        f"\n  [dim]Using saved settings from pahebatcher.toml:"
                        f"  {audio_badge_text}  {quality}p  {parallel} concurrent  {workers} workers[/dim]\n"
                    )
                    ctx = AppContext(
                        output_dir=default_output, cache_dir=cache_dir,
                        quality=quality, audio_lang=audio_lang,
                        max_parallel=parallel, hls_workers=workers,
                        keep_temp=False, list_only=False,
                        flaresolverr_url=flaresolverr_url,
                        resolve_ahead=resolve_ahead, cache_ttl=cache_ttl,
                        cookie_string=cookie_string,
                    )
                else:
                    _defaults = AppContext(
                        output_dir=default_output, cache_dir=cache_dir,
                        quality=quality, audio_lang=audio_lang,
                        max_parallel=parallel, hls_workers=workers,
                        keep_temp=False, list_only=False,
                        flaresolverr_url=flaresolverr_url,
                        resolve_ahead=resolve_ahead, cache_ttl=cache_ttl,
                        cookie_string=cookie_string,
                    )
                    ctx = wizard_config(_defaults, mode=mode)
                    # Persist choices from wizard
                    cm.set("quality", ctx.quality)
                    cm.set("audio_lang", ctx.audio_lang)
                    cm.set("max_parallel", ctx.max_parallel)
                    cm.set("hls_workers", ctx.hls_workers)
                    try:
                        cm.save()
                        console.print("  [dim]Settings saved to [bold]pahebatcher.toml[/bold][/dim]\n")
                    except Exception:
                        pass

                if mode == "stream":
                    await run_stream(ctx, anime, chosen, solver)
                else:
                    if not _scripted and not confirm_download(anime, chosen, ctx):
                        continue
                    orch = BatchOrchestrator(ctx, anime, http, solver)
                    await orch.download(chosen)

                break

        finally:
            await http.close()
    finally:
        await solver.close()

    # Cleanup orphaned cache (>24h old)
    with contextlib.suppress(Exception):
        now = time.time()
        for p in cache_dir.glob("*/*/*.ts"):
            if now - p.stat().st_mtime > 86400:
                with contextlib.suppress(Exception):
                    shutil.rmtree(p.parent.parent)

    console.print("\n  [bold cyan]Session finished.[/bold cyan]")


def main() -> None:
        # Config subcommand uses its own parser to avoid argparse
        # subparser greediness with URL positional arguments.
        if len(sys.argv) > 1 and sys.argv[1] == "config":
            cfg_parser = build_config_parser()
            cfg_args = cfg_parser.parse_args(sys.argv[2:])
            if cfg_args.config_action == "show":
                ConfigManager.cli_show()
            elif cfg_args.config_action == "set":
                ConfigManager.cli_set(cfg_args.key, cfg_args.value)
            elif cfg_args.config_action == "reset":
                ConfigManager.cli_reset()
            return

        parser = build_parser()
        args = parser.parse_args()
        try:
            asyncio.run(run(args))
        except (KeyboardInterrupt, asyncio.CancelledError):
            console.print("\n  [yellow]Interrupted.[/yellow]")
            sys.exit(0)
        except Exception as exc:
            console.print(f"\n  [red]\u2717 Fatal Error:[/red] {exc}")
            if args.verbose:
                raise
            sys.exit(1)


if __name__ == "__main__":
    main()
