"""Interactive prompts — episode selection, download confirmation, wizard."""

from __future__ import annotations

import contextlib
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING

from rich import box
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.prompt import Confirm, IntPrompt, Prompt
from rich.rule import Rule
from rich.table import Table

from pahebatcher.utils import compact_ep_range

if TYPE_CHECKING:
    from pahebatcher.models import AnimeInfo, AppContext, EpisodeInfo
    from pahebatcher.solver import Solver


def _parse_ep_range(raw: str, all_eps: list[EpisodeInfo]) -> list[float]:
    numbers = sorted({ep.number for ep in all_eps})
    result: set[float] = set()
    for token in re.split(r"[,\s]+", raw):
        token = token.strip()
        if not token:
            continue
        if m := re.match(r"^(\d+(?:\.\d+)?)-(\d+(?:\.\d+)?)$", token):
            lo, hi = float(m.group(1)), float(m.group(2))
            result.update(n for n in numbers if lo <= n <= hi)
        elif m := re.match(r"^(\d+(?:\.\d+)?)-$", token):
            lo = float(m.group(1))
            result.update(n for n in numbers if n >= lo)
        elif m := re.match(r"^(\d+(?:\.\d+)?)$", token):
            result.add(float(m.group(1)))
    return sorted(result)


def _print_ep_table(anime: AnimeInfo, episodes: list[EpisodeInfo], selected: set[str]) -> None:
    from pahebatcher.ui.console import console

    t = Table(box=box.SIMPLE, show_header=True, header_style="bold cyan", padding=(0, 1))
    t.add_column("", width=2, justify="center")
    t.add_column("Ep", width=6, justify="right", style="dim")
    t.add_column("Title", style="white")

    seen_nums = set()
    for ep in episodes:
        if ep.number in seen_nums:
            continue
        seen_nums.add(ep.number)
        variants = anime.get_all_variants(ep.number)
        check = "[green]\u2713[/green]" if ep.session in selected else " "
        if any(v.session in selected for v in variants):
            check = "[green]\u2713[/green]"
        t.add_row(check, ep.ep_str, ep.title or "\u2014")
    from pahebatcher.ui.console import console
    console.print(t)


def select_episodes(anime: AnimeInfo) -> list[EpisodeInfo]:
    from pahebatcher.ui.console import console

    console.print()
    console.print(Rule(f"[bold white] Episode Selection \u2014 {anime.title} [/bold white]", style="cyan"))

    unique_eps: list[EpisodeInfo] = list({ep.number: ep for ep in anime.episodes}.values())
    unique_eps.sort(key=lambda e: e.number)

    available = compact_ep_range(unique_eps)
    console.print(
        f"  [cyan]{anime.total}[/cyan] episodes: "
        f"[bold cyan]{available}[/bold cyan]"
        f"  [dim]({anime.total} total in series)[/dim]\n"
    )
    console.print(Panel(
        "  [bold white]A[/bold white]  All episodes\n"
        "  [bold white]R[/bold white]  Range    [dim]e.g. 1-12  or  1,4,7  or  13-[/dim]\n"
        "  [bold white]L[/bold white]  Toggle   [dim]interactive checklist[/dim]\n"
        "  [bold white]N[/bold white]  Latest N [dim]most recently aired[/dim]\n"
        "  [bold white]S[/bold white]  Skip",
        title="[cyan]Select mode[/cyan]", border_style="dim cyan",
        box=box.ROUNDED, padding=(0, 2),
    ))

    mode = Prompt.ask(
        "  [cyan]Select mode[/cyan]",
        choices=["A", "a", "R", "r", "L", "l", "N", "n", "S", "s"],
        default="A", show_choices=False,
    ).upper()

    eps_by_num = {ep.number: ep for ep in anime.episodes}

    if mode == "S":
        return []
    if mode == "A":
        console.print(f"  [green]\u2713[/green] All [cyan]{anime.total}[/cyan] episodes selected.")
        return unique_eps
    if mode == "N":
        n = IntPrompt.ask("  Latest [cyan]N[/cyan] episodes", default=1)
        chosen = unique_eps[-max(1, min(n, len(unique_eps))):]
        console.print(f"  [green]\u2713[/green] Latest [cyan]{len(chosen)}[/cyan] selected.")
        return chosen
    if mode == "R":
        console.print(
            "  Enter numbers or ranges \u2014 e.g. [dim]1-12[/dim]  "
            "[dim]1,4,7[/dim]  [dim]5-[/dim]  [dim]1-6,10[/dim]"
        )
        raw = Prompt.ask("  [cyan]Episodes[/cyan]").strip()
        nums = _parse_ep_range(raw, unique_eps)
        chosen = [eps_by_num[n] for n in nums if n in eps_by_num]
        if not chosen:
            console.print(f"  [yellow]\u26a0 Nothing matched [bold]{raw}[/bold]. "
                          f"Available: [cyan]{available}[/cyan][/yellow]")
        else:
            console.print(f"  [green]\u2713[/green] [cyan]{len(chosen)}[/cyan] episodes selected.")
        return chosen

    # Toggle mode
    selected: set[str] = set()
    while True:
        console.clear()
        console.print(Rule(f"[bold white] {anime.title} [/bold white]", style="cyan"))
        _print_ep_table(anime, unique_eps, selected)
        console.print(
            "  [dim]a[/dim]=all  [dim]n[/dim]=none  "
            "[dim]<num>[/dim]=toggle  [dim]done[/dim]=confirm"
        )
        cmd = Prompt.ask("  [cyan]>[/cyan]").strip().lower()
        if cmd in ("done", "d", ""):
            break
        elif cmd == "a":
            selected = {ep.session for ep in unique_eps}
        elif cmd == "n":
            selected.clear()
        else:
            for num in _parse_ep_range(cmd, unique_eps):
                variants = anime.get_all_variants(num)
                if not variants:
                    continue
                if any(v.session in selected for v in variants):
                    for v in variants:
                        selected.discard(v.session)
                else:
                    selected.add(variants[0].session)

    chosen = [ep for ep in unique_eps if ep.session in selected]
    console.print(f"  [green]\u2713[/green] [cyan]{len(chosen)}[/cyan] episodes selected.")
    return chosen


def noninteractive_episodes(anime: AnimeInfo, mode: str, range_str: str = "", latest_n: int = 1) -> list[EpisodeInfo]:
    unique_eps = sorted(
        {ep.number: ep for ep in anime.episodes}.values(),
        key=lambda e: e.number,
    )
    if mode == "all":
        return unique_eps
    if mode == "latest":
        return unique_eps[-max(1, min(latest_n, len(unique_eps))):]
    if mode == "range":
        eps_by_num = {ep.number: ep for ep in unique_eps}
        return [eps_by_num[n] for n in _parse_ep_range(range_str, unique_eps) if n in eps_by_num]
    return []


def confirm_download(anime: AnimeInfo, episodes: list[EpisodeInfo], ctx: AppContext) -> bool:
    from pahebatcher.store import SegmentStore
    from pahebatcher.ui.console import console

    n = len(episodes)
    ep_range_str = compact_ep_range(episodes)

    reused_count = 0
    if anime.has_session:
        for ep in episodes:
            target_ep = anime.get_variant(ep.number, ctx.audio_lang) or ep
            store = SegmentStore(ctx.cache_dir, anime.title, anime.session, target_ep.ep_str, target_ep.audio)
            reused_count += len(store.done_indices())

    est_mb_per = {360: 50, 720: 90, 1080: 150}.get(ctx.quality, 120)
    est_total = n * est_mb_per
    audio_str = "[cyan]SUB[/cyan]" if ctx.audio_lang == "jpn" else "[yellow]DUB[/yellow]"

    stats = [
        f"  [dim]Series:[/dim]    [bold white]{anime.title}[/bold white]",
        f"  [dim]Episodes:[/dim]  [cyan]{n}[/cyan]  ({ep_range_str})",
        f"  [dim]Audio:[/dim]     {audio_str}",
        f"  [dim]Quality:[/dim]   [cyan]{ctx.quality}p[/cyan]",
        f"  [dim]Output:[/dim]    {ctx.output_dir}",
    ]
    if reused_count > 0:
        stats.append(f"  [dim]Reusing:[/dim]   [bold green]{reused_count}[/bold green] segments from previous session")
    stats.append(f"  [dim]Est. size:[/dim] [cyan]~{est_total} MB[/cyan]  [dim](~{est_mb_per} MB/ep \u00d7 {n} eps)[/dim]")

    console.print()
    console.print(Panel(
        "\n".join(stats),
        title=f"[bold green]Ready to Download \u2014 {n} episode{'s' if n != 1 else ''}[/bold green]",
        border_style="green", box=box.ROUNDED,
    ))
    return Confirm.ask("  [cyan]Start download?[/cyan]", default=True)


def wizard_config(defaults: AppContext, mode: str = "download") -> AppContext:
    from dataclasses import replace
    from pahebatcher.ui.console import console

    console.print()
    console.print(Rule("[bold white] Download Settings [/bold white]", style="cyan"))
    console.print(
        "  [dim]Settings are saved to [bold]pahebatcher.toml[/bold] and reused on future runs.[/dim]\n"
        "  [dim]After this setup, run [bold]pahebatcher config set KEY VALUE[/bold] to change defaults.[/dim]\n"
    )

    q_default = {360: "1", 720: "2", 1080: "3"}.get(defaults.quality, "3")
    console.print(Panel(
        "  [bold white]1[/bold white]  [dim cyan]360p [/dim cyan]  [dim]~50 MB/ep[/dim]\n"
        "  [bold white]2[/bold white]  [cyan]720p [/cyan]  [dim]~90 MB/ep   \u00b7 recommended[/dim]\n"
        "  [bold white]3[/bold white]  [bold cyan]1080p[/bold cyan]  [dim]~150 MB/ep  \u00b7 best quality[/dim]",
        title="[cyan]Quality[/cyan]", border_style="dim cyan", box=box.ROUNDED, padding=(0, 2),
    ))
    quality = {1: 360, 2: 720, 3: 1080}[int(
        Prompt.ask("  [cyan]Select quality[/cyan]", choices=["1", "2", "3"], default=q_default, show_choices=False)
    )]

    audio_default = "1" if defaults.audio_lang == "jpn" else "2"
    console.print(Panel(
        "  [bold white]1[/bold white]  [cyan]Subbed[/cyan]  [dim](Japanese audio)[/dim]\n"
        "  [bold white]2[/bold white]  [yellow]Dubbed[/yellow]  [dim](English audio)[/dim]",
        title="[cyan]Audio Language[/cyan]", border_style="dim cyan", box=box.ROUNDED, padding=(0, 2),
    ))
    audio_lang = "jpn" if Prompt.ask(
        "  [cyan]Select audio[/cyan]", choices=["1", "2"], default=audio_default, show_choices=False,
    ) == "1" else "eng"

    if mode == "download":
        output_dir = Prompt.ask("  [cyan]Output directory[/cyan]", default=defaults.output_dir).strip()
        Path(output_dir).mkdir(parents=True, exist_ok=True)

        console.print(Panel(
            "  [bold white]1[/bold white]  [dim]1 download   \u00b7 safest[/dim]\n"
            "  [bold white]2[/bold white]  [cyan]2 simultaneous  \u00b7 recommended[/cyan]\n"
            "  [bold white]4[/bold white]  [dim]4 simultaneous  \u00b7 faster, more RAM[/dim]\n"
            "  [bold white]6[/bold white]  [dim]6 simultaneous  \u00b7 may trigger rate-limits[/dim]",
            title="[cyan]Concurrent Downloads[/cyan]", border_style="dim cyan",
            box=box.ROUNDED, padding=(0, 2),
        ))
        max_parallel = max(1, min(6, IntPrompt.ask("  [cyan]Select[/cyan]", default=defaults.max_parallel)))
        hls_workers_val = max(8, min(32, IntPrompt.ask(
            "  [cyan]HLS workers per episode[/cyan] [dim](8-32, default 24)[/dim]",
            default=defaults.hls_workers,
        )))
        return replace(defaults, output_dir=output_dir, quality=quality, audio_lang=audio_lang,
                       max_parallel=max_parallel, hls_workers=hls_workers_val)
    return replace(defaults, quality=quality, audio_lang=audio_lang)


async def interactive_discovery(solver: Solver, host: str) -> str | None:
    from pahebatcher.extract.scanner import AnimePaheScanner
    from pahebatcher.ui.console import console
    from pahebatcher.ui.tables import search_results_table

    while True:
        console.print()
        console.print(Rule("[bold white] Search & Discovery [/bold white]", style="cyan"))
        query = Prompt.ask("  [cyan]Search Anime[/cyan] [dim](or 'q' to quit)[/dim]").strip()
        if not query or query.lower() == "q":
            return None

        with Progress(
            SpinnerColumn(), TextColumn(f"[bold white]Searching for '{query}'..."),
            console=console, transient=True,
        ) as prog:
            prog.add_task("", total=None)
            results = await AnimePaheScanner.search(solver, host, query)

        if not results:
            console.print(f"  [yellow]\u26a0 No results found for '[bold]{query}[/bold]'[/yellow]")
            continue

        console.print(search_results_table(results, query))
        choice = Prompt.ask(
            f"  [cyan]Select # (1-{len(results)})[/cyan] [dim](or 's' to search again, 'q' to quit)[/dim]",
            default="1",
        ).lower()
        if choice == "q":
            return None
        if choice == "s":
            continue
        with contextlib.suppress(ValueError):
            idx = int(choice)
            if 1 <= idx <= len(results):
                target = results[idx - 1]
                session = target.get("session")
                if session:
                    return f"https://{AnimePaheScanner._current_host}/anime/{session}"
        console.print("  [red]\u26a0 Invalid selection.[/red]")
