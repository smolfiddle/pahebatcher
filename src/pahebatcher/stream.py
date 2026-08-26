"""MPV stream player with live playback panel and navigation."""

from __future__ import annotations

import asyncio
import contextlib
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


async def _prepare_local_hls(
    info: StreamInfo, http_client: object, port: int = 0
) -> tuple[str, object, object]:
    """Fetch m3u8 via HttpClient (handles 403 fallback) and serve locally for MPV.

    Returns (local_url, runner, site) — caller must cleanup via runner.cleanup().
    Falls back to remote URL if local serve fails.
    """
    from aiohttp import web

    from pahebatcher.extract.m3u8 import fetch_m3u8
    from pahebatcher.http import HttpClient

    # Ensure http_client is HttpClient and started
    http: HttpClient
    if isinstance(http_client, HttpClient):
        http = http_client
    else:
        http = HttpClient(hls_workers=8)
        await http.start()

    # Fetch original m3u8 via HttpClient (handles curl fallback for 403)
    try:
        m3u8_text = await fetch_m3u8(http, info.url, info.headers)
    except Exception as exc:
        log.warning("Failed to fetch m3u8 via HttpClient, falling back to remote: %s", exc)
        return info.url, None, None

    # Parse and collect segment/key URLs to proxy
    # For now, serve the original m3u8 text rewritten to local URLs
    # We will serve m3u8 at /uwu.m3u8 and proxy segments/keys via same server
    # Rewrite segment URLs and key URI to local
    # Keep original m3u8 text for debugging, but rewrite for local serving
    import re as _re

    # Find remote base for segments (for proxying, we need to map local path -> remote URL)
    # We'll create a dict local_path -> remote_url
    remote_map: dict[str, str] = {}
    # Extract key URI
    key_match = _re.search(r'#EXT-X-KEY:METHOD=AES-128,URI="([^"]+)"', m3u8_text)
    if key_match:
        raw_key_url = key_match.group(1)
        # Normalize key URL (may be relative)
        from urllib.parse import urljoin

        remote_key_url = raw_key_url if raw_key_url.startswith("http") else urljoin(info.url, raw_key_url)
        remote_map["/mon.key"] = remote_key_url
        m3u8_text = m3u8_text.replace(raw_key_url, "http://127.0.0.1:{port}/mon.key")

    # Extract segment URLs (lines not starting with #)
    lines = m3u8_text.splitlines()
    for line in lines:
        line = line.strip()
        if line and not line.startswith("#"):
            remote_seg = line
            # Normalize
            from urllib.parse import urljoin, urlparse

            if not remote_seg.startswith("http"):
                remote_seg = urljoin(info.url, remote_seg)
            # Map to local path based on last part
            parsed = urlparse(remote_seg)
            local_seg_path = "/" + parsed.path.split("/")[-1]
            # Ensure unique
            if local_seg_path not in remote_map:
                remote_map[local_seg_path] = remote_seg
            # Replace in m3u8 text
            m3u8_text = m3u8_text.replace(line, f"http://127.0.0.1:{{port}}{local_seg_path}")

    # Handler to serve m3u8
    async def handle_m3u8(request: web.Request) -> web.Response:
        # Replace port placeholder with actual port
        body = m3u8_text.replace("{port}", str(request.app["port"]))
        if request.method == "HEAD":
            return web.Response(
                status=200,
                headers={
                    "Content-Type": "application/vnd.apple.mpegurl",
                    "Content-Length": str(len(body.encode())),
                },
            )
        return web.Response(
            text=body, content_type="application/vnd.apple.mpegurl", headers={"Accept-Ranges": "bytes"},
        )

    # Generic handler for segments/keys
    async def handle_proxy(request: web.Request) -> web.Response:
        local_path = request.path
        remote_url = request.app["remote_map"].get(local_path)
        if not remote_url:
            return web.Response(status=404, text="Not found")
        # Fetch remote via HttpClient with original headers (handles fallback)
        try:
            data = await http.get(remote_url, headers=info.headers, timeout=60)
            # Determine content type
            ctype = "application/octet-stream"
            if local_path.endswith(".key"):
                ctype = "application/octet-stream"
            elif local_path.endswith(".jpg") or local_path.endswith(".ts"):
                ctype = "video/MP2T"
            # Handle Range header for MPV probing
            range_hdr = request.headers.get("Range")
            if range_hdr and request.method == "GET":
                # Simple bytes= start-end handling
                import re as _re2

                m = _re2.match(r"bytes=(\d*)-(\d*)", range_hdr)
                if m:
                    start_s, end_s = m.groups()
                    start = int(start_s) if start_s else 0
                    end = int(end_s) if end_s else len(data) - 1
                    end = min(end, len(data) - 1)
                    if start <= end:
                        sliced = data[start : end + 1]
                        headers = {
                            "Content-Range": f"bytes {start}-{end}/{len(data)}",
                            "Accept-Ranges": "bytes",
                            "Content-Length": str(len(sliced)),
                            "Content-Type": ctype,
                        }
                        return web.Response(status=206, body=sliced, headers=headers)
            if request.method == "HEAD":
                return web.Response(
                    status=200,
                    headers={
                        "Content-Type": ctype,
                        "Content-Length": str(len(data)),
                        "Accept-Ranges": "bytes",
                    },
                )
            return web.Response(body=data, content_type=ctype, headers={"Accept-Ranges": "bytes"})
        except Exception as exc:
            log.warning("Local HLS proxy failed for %s: %s", local_path, exc)
            return web.Response(status=502, text=f"Proxy failed: {exc}")

    app = web.Application()
    app["remote_map"] = remote_map
    app["port"] = port  # placeholder, will update after site start
    app.router.add_route("*", "/uwu.m3u8", handle_m3u8)
    app.router.add_route("*", "/{tail:.*}", handle_proxy)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", port)
    await site.start()
    # Get actual port
    sockets = site._server.sockets if site._server else []  # type: ignore[attr-defined]
    actual_port = sockets[0].getsockname()[1] if sockets else 8080
    app["port"] = actual_port
    local_url = f"http://127.0.0.1:{actual_port}/uwu.m3u8"
    # Verify local m3u8 fetch works (quick check)
    try:
        # Quick fetch via http to ensure server works
        import aiohttp

        async with (
            aiohttp.ClientSession() as sess,
            sess.get(local_url, timeout=aiohttp.ClientTimeout(total=5)) as resp,
        ):
            if resp.status != 200:
                raise RuntimeError(f"Local server check failed {resp.status}")
    except Exception as exc:
        log.warning("Local HLS server check failed, falling back to remote: %s", exc)
        with contextlib.suppress(Exception):
            await runner.cleanup()
        return info.url, None, None

    return local_url, runner, site


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
            audio_str += (
                f"  [dim]([cyan]{alt_name} available \u2014 press [bold]A[/bold] to switch[/cyan])[/dim]"
            )

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

    # Shared HttpClient for HLS proxy (handles 403 fallback)
    from pahebatcher.http import HttpClient

    http = HttpClient(hls_workers=8)
    await http.start()

    try:
        while 0 <= idx < len(playlist):
            ep = playlist[idx]
            local_runner = None
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
                    info = await extract_stream(
                        solver, ep.play_url, ctx.quality, audio_pref, ctx.cookie_string,
                    )

                ep.audio = info.audio
                _clean_ep_title(ep, info)
                if info.fansub:
                    ep.fansub = info.fansub

                mpv_title = re.sub(
                    r"\s+(?:DUB|SUB)\s*$", "",
                    ep.title or f"Episode {ep.ep_str}", flags=re.I,
                ).strip()
                audio_tag = "SUB" if ep.audio == "jpn" else "DUB"

                # Try local HLS proxy first (avoids MPV 403 on HTTP/1.1)
                local_url = info.url
                try:
                    local_url, local_runner, _ = await _prepare_local_hls(info, http, port=0)
                    if local_url != info.url:
                        log.debug("Using local HLS proxy: %s -> %s", info.url, local_url)
                except Exception as exc:
                    log.warning("Local HLS proxy failed, falling back to remote: %s", exc)
                    local_url = info.url
                    local_runner = None

                # Build MPV command — use local_url, no headers needed for local
                if local_runner is not None:
                    cmd = [
                        "mpv",
                        f"--force-media-title={mpv_title} [{audio_tag}]",
                        "--ytdl=no",
                        "--msg-level=all=warn,lavf=error,ffmpeg=error",
                        local_url,
                    ]
                else:
                    # Fallback to remote with headers (original)
                    # Only include Cookie header if present to avoid malformed
                    http_fields = f"Cookie: {info.cookie_str}" if info.cookie_str else ""
                    cmd = [
                        "mpv",
                        f"--user-agent={info.user_agent}",
                        f"--referrer={info.referer}",
                    ]
                    if http_fields:
                        cmd.append(f"--http-header-fields={http_fields}")
                    cmd.extend([
                        "--demuxer-lavf-format=hls",
                        (
                            f"--demuxer-lavf-o=cookies={info.cookie_str},referer={info.referer}"
                            if info.cookie_str
                            else f"--demuxer-lavf-o=referer={info.referer}"
                        ),
                        f"--force-media-title={mpv_title} [{audio_tag}]",
                        "--ytdl=no",
                        "--msg-level=all=warn,lavf=error,ffmpeg=error",
                        info.url,
                    ])

                with Live(render_play_panel(ep, "playing"), console=console, refresh_per_second=4) as live:
                    proc = await asyncio.create_subprocess_exec(
                        *cmd,
                        stdout=asyncio.subprocess.DEVNULL,
                        stderr=asyncio.subprocess.PIPE,
                    )
                    _, stderr = await proc.communicate()
                    # Log MPV result for diagnostics
                    if proc.returncode not in (0, None):
                        err_text = (stderr.decode(errors="ignore")[:800] if stderr else "")
                        log.warning("MPV exit %s: %s", proc.returncode, err_text)
                        if ctx.verbose if hasattr(ctx, "verbose") else False:
                            console.print(f"[dim]MPV stderr: {err_text}[/dim]")
                        # Also show to user if failed quickly
                        if err_text and "HTTP error 403" in err_text:
                            console.print("[yellow]MPV got 403 — retrying via local proxy may help.[/yellow]")

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

                    valid_choices.insert(0, "a")
                    prompt_parts.append("[bold](A)[/bold]udio")
                    other_lang = "eng" if audio_pref == "jpn" else "jpn"
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
                    audio_pref = other_lang
                    playlist = _build_playlist(audio_pref)
                    idx = max(0, min(idx, len(playlist) - 1))
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
            finally:
                if local_runner is not None:
                    with contextlib.suppress(Exception):
                        await local_runner.cleanup()  # type: ignore[attr-defined]

        console.print("\n  [yellow]Playback session ended.[/yellow]")
    finally:
        with contextlib.suppress(Exception):
            await http.close()
