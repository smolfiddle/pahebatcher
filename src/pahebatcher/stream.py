"""MPV stream player with live playback panel and navigation."""

from __future__ import annotations

import asyncio
import logging
import re
import shutil
from typing import TYPE_CHECKING

from rich import box
from rich.align import Align
from rich.console import Group
from rich.live import Live
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.prompt import Confirm, IntPrompt, Prompt
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

from pahebatcher.extract.kwik import extract_stream

if TYPE_CHECKING:
    from pahebatcher.models import AnimeInfo, AppContext, EpisodeInfo, StreamInfo
    from pahebatcher.solver import Solver

log = logging.getLogger(__name__)


def _clean_ep_title(ep: EpisodeInfo, info: StreamInfo) -> None:
    if not info.title:
        return
    if ep.title and "animepahe" not in ep.title.lower() and "?" not in ep.title:
        return
    t = info.title
    t = re.sub(r"\s*::\s*animepahe.*$", "", t, flags=re.I).strip()
    t = re.sub(r"\s*-\s*animepahe.*$", "", t, flags=re.I).strip()
    t = re.sub(r"\s*[|\u00b7].*$", "", t).strip()
    t = re.sub(r"^Watch\s+.*?Episode\s+\d+.*", "", t, flags=re.I).strip()
    t = re.sub(r"\((?:1080|720|360)p\).*", "", t, flags=re.I).strip()
    t = re.sub(r"\[SubsPlease\].*", "", t, flags=re.I).strip()
    t = re.sub(r"AnimePahe_", "", t, flags=re.I).strip()
    t = t.replace("_", " ").strip()
    t = re.sub(r"\s+(?:DUB|SUB)\s*$", "", t, flags=re.I).strip()
    if t:
        ep.title = t


def _display_title(ep: EpisodeInfo) -> str:
    t = re.sub(r"\s+(?:DUB|SUB)\s*$", "", ep.title or "", flags=re.I).strip()
    return t or f"Episode {ep.ep_str}"


def _audio_pill(lang: str) -> str:
    return "[cyan]SUB[/cyan]" if lang == "jpn" else "[yellow]DUB[/yellow]"


async def run_stream(
    ctx: AppContext,
    anime: AnimeInfo,
    chosen_episodes: list[EpisodeInfo],
    solver: Solver,
) -> None:
    from pahebatcher.ui.console import console

    if not shutil.which("mpv"):
        console.print("\n  [red]\u2717 MPV not found![/red]")
        console.print("  [dim]Install MPV from https://mpv.io and ensure it is in your PATH.[/dim]")
        return

    audio_pref = ctx.audio_lang

    def _build_playlist(lang: str) -> list[EpisodeInfo]:
        result: list[EpisodeInfo] = []
        for ep in chosen_episodes:
            variant = anime.get_variant(ep.number, lang) or ep
            if variant not in result:
                result.append(variant)
        return result

    playlist = _build_playlist(audio_pref)
    idx = 0

    console.print()
    console.print(Rule("[bold white] Streaming via MPV [/bold white]", style="cyan"))

    def render_play_panel(
        ep: EpisodeInfo, state: str = "playing", choices_ui: str = "",
    ) -> Panel:
        cur_lang = ep.audio
        other_lang = "eng" if cur_lang == "jpn" else "jpn"
        has_alt = anime.get_variant(ep.number, other_lang) is not None
        audio_str = _audio_pill(cur_lang)
        if has_alt:
            alt_name = "DUB" if other_lang == "eng" else "SUB"
            audio_str += f"  [dim]([cyan]{alt_name} available \u2014 press [bold]A[/bold] to switch[/cyan])[/dim]"

        ep_title = _display_title(ep)
        if state == "playing":
            content = Group(
                Text(anime.title, style="bold cyan underline"),
                Text.from_markup(
                    f"\u25b6  Ep [cyan]{ep.ep_str}[/cyan]  [bold white]{ep_title}[/bold white]",
                    style="bold green",
                ),
                Text.from_markup(
                    f"   Audio: {audio_str}  \u00b7  Quality: {ctx.quality}p", style="dim",
                ),
                Text.from_markup(
                    f"   Episode [cyan]{idx + 1}[/cyan] of [cyan]{len(playlist)}[/cyan]", style="dim",
                ),
                Rule(style="dim", characters="\u2500"),
                Text("Close MPV window to return to controls", style="italic dim"),
            )
            title_p = "[bold cyan]\u25b6  Live Playback[/bold cyan]"
            border_c = "green"
        else:
            content = Group(
                Text(anime.title, style="bold cyan underline"),
                Text.from_markup(
                    f"\u25a0  Ep [cyan]{ep.ep_str}[/cyan]  [dim]{ep_title}[/dim]  {audio_str}",
                    style="dim",
                ),
                Rule(style="dim", characters="\u2500"),
                Text.from_markup(choices_ui or ""),
            )
            title_p = "[bold yellow]\u25a0  Playback Ended[/bold yellow]"
            border_c = "yellow"
        return Panel(
            Align.center(content),
            title=title_p, border_style=border_c, box=box.ROUNDED, padding=(1, 2),
        )

    while 0 <= idx < len(playlist):
        ep = playlist[idx]
        try:
            with Progress(
                SpinnerColumn(),
                TextColumn(
                    f"[bold white]({idx + 1}/{len(playlist)})"
                    f"  Resolving Ep [cyan]{ep.ep_str}[/cyan]"
                    f"  {_audio_pill(audio_pref)}\u2026"
                ),
                console=console, transient=True,
            ) as prog:
                prog.add_task("", total=None)
                info = await extract_stream(solver, ep.play_url, ctx.quality, audio_pref)

            ep.audio = info.audio
            _clean_ep_title(ep, info)
            if info.fansub:
                ep.fansub = info.fansub

            mpv_title = re.sub(
                r"\s+(?:DUB|SUB)\s*$", "",
                ep.title or f"Episode {ep.ep_str}", flags=re.I,
            ).strip()
            audio_tag = "SUB" if ep.audio == "jpn" else "DUB"

            cmd = [
                "mpv",
                f"--user-agent={info.user_agent}",
                f"--referrer={info.referer}",
                f"--http-header-fields=Cookie: {info.cookie_str}",
                "--demuxer-lavf-format=hls",
                f"--demuxer-lavf-o=cookies={info.cookie_str},referer={info.referer}",
                f"--force-media-title={mpv_title} [{audio_tag}]",
                "--msg-level=all=warn,lavf=error,ffmpeg=error",
                info.url,
            ]

            with Live(render_play_panel(ep, "playing"), console=console, refresh_per_second=4) as live:
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.PIPE,
                )
                await proc.communicate()

                valid_choices: list[str] = ["r", "s", "q"]
                prompt_parts: list[str] = []
                ui_options: list[str] = []

                if idx > 0:
                    valid_choices.insert(0, "p")
                    prompt_parts.append("[bold](P)[/bold]rev")
                    ui_options.append("[cyan]P[/cyan]rev")
                if idx < len(playlist) - 1:
                    valid_choices.insert(0, "n")
                    prompt_parts.append("[bold](N)[/bold]ext")
                    ui_options.append("[green]N[/green]ext")

                other_lang = "eng" if ep.audio == "jpn" else "jpn"
                has_alt = anime.get_variant(ep.number, other_lang) is not None
                if has_alt:
                    valid_choices.insert(0, "a")
                    prompt_parts.append("[bold](A)[/bold]udio")
                    alt_name = "DUB" if other_lang == "eng" else "SUB"
                    ui_options.append(f"[yellow]A[/yellow]udio\u2192{alt_name}")

                ui_options += ["[white]R[/white]eplay", "[magenta]S[/magenta]elect", "[red]Q[/red]uit"]
                prompt_parts += ["[bold](R)[/bold]eplay", "[bold](S)[/bold]elect", "[bold](Q)[/bold]uit"]

                choices_ui = "  \u00b7  ".join(ui_options)
                live.update(render_play_panel(ep, "ended", choices_ui))

            default = "n" if idx < len(playlist) - 1 else "q"
            all_choices = valid_choices + [c.upper() for c in valid_choices]
            choice = Prompt.ask(
                "  [cyan]" + "  ".join(prompt_parts) + "[/cyan]",
                choices=all_choices, default=default, show_choices=False,
            ).lower()

            if choice == "n":
                idx += 1
            elif choice == "p":
                idx -= 1
            elif choice == "r":
                continue
            elif choice == "q":
                break
            elif choice == "a":
                other_lang = "eng" if audio_pref == "jpn" else "jpn"
                if anime.get_variant(ep.number, other_lang):
                    audio_pref = other_lang
                    playlist = _build_playlist(audio_pref)
                else:
                    audio_pref = other_lang
                continue
            elif choice == "s":
                console.print()
                sel = Table(
                    box=box.ROUNDED, header_style="bold cyan",
                    title=f"[bold white]Select Episode \u2014 {anime.title}[/bold white]",
                )
                sel.add_column("#", justify="right", style="dim", width=4)
                sel.add_column("Ep", justify="right", width=6)
                sel.add_column("Title", ratio=1)
                for i, e in enumerate(playlist):
                    style_s = "bold green" if i == idx else "dim"
                    pointer = "\u2192 " if i == idx else "  "
                    sel.add_row(f"{pointer}{i + 1}", e.ep_str, _display_title(e), style=style_s)
                console.print(sel)
                num = IntPrompt.ask(
                    f"  [cyan]Jump to # (1\u2013{len(playlist)})[/cyan]", default=idx + 1,
                )
                idx = max(0, min(num - 1, len(playlist) - 1))
        except KeyboardInterrupt:
            break
        except Exception as exc:
            console.print(f"  [red]\u2717 Error:[/red] {exc}")
            if not Confirm.ask("  [cyan]Try next episode?[/cyan]", default=True):
                break

    console.print("\n  [yellow]Playback session ended.[/yellow]")
