"""Rich table rendering helpers."""

from rich import box
from rich.table import Table

from pahebatcher.utils import fmt_bytes


def episode_table(anime, episodes, selected_set=None, show_audio=True):
    """Render episode list as Rich Table."""
    t = Table(box=box.SIMPLE, show_header=True, header_style="bold cyan", padding=(0, 1))
    t.add_column("", width=2, justify="center")
    t.add_column("Ep", width=6, justify="right", style="dim")
    t.add_column("Title", style="white")
    if show_audio:
        t.add_column("Audio", width=5)

    seen_nums = set()
    for ep in episodes:
        if ep.number in seen_nums:
            continue
        seen_nums.add(ep.number)
        check = "\u2713" if selected_set and ep.session in selected_set else " "
        row = [check, ep.ep_str, ep.title or "\u2014"]
        if show_audio:
            from pahebatcher.utils import audio_badge
            row.append(audio_badge(ep.audio))
        t.add_row(*row)
    return t


def summary_table(results, episodes, output_dir: str):
    """Render download completion summary."""
    from rich.console import Console
    from pahebatcher.utils import audio_badge

    console = Console()
    table = Table(box=box.SIMPLE_HEAVY, show_header=True, header_style="bold cyan", border_style="dim")
    table.add_column("Ep", style="cyan", width=6, justify="right")
    table.add_column("Title", style="bold white", ratio=1, overflow="ellipsis")
    table.add_column("Audio", width=5)
    table.add_column("Status", justify="center", width=10)
    table.add_column("Size", justify="right", width=10, style="cyan")
    table.add_column("File", style="dim", ratio=1, overflow="ellipsis")

    for ep in episodes:
        path = results.get(ep.session)
        badge = "[bold green]\u2713  done[/bold green]" if path else "[red]\u2717 failed[/red]"
        size = fmt_bytes(path.stat().st_size) if path and path.exists() else "\u2014"
        fname = path.name if path else "\u2014"
        table.add_row(ep.ep_str, ep.title or "\u2014", audio_badge(ep.audio), badge, size, fname)
    return table


def search_results_table(results, query):
    """Render interactive search results."""
    table = Table(
        box=box.ROUNDED, header_style="bold cyan",
        title=f"[bold white]Search Results: {query}[/bold white]",
    )
    table.add_column("#", justify="right", style="dim", width=4)
    table.add_column("Title", ratio=1)
    table.add_column("Type", justify="center", width=8)
    table.add_column("Year", justify="center", width=6)
    table.add_column("Eps", justify="center", width=6)
    table.add_column("Score", justify="center", width=6)
    for i, res in enumerate(results, 1):
        table.add_row(
            str(i), res.get("title", "Unknown"), res.get("type", "-"),
            str(res.get("year", "-")), str(res.get("episodes", "-")),
            str(res.get("score", "-")),
        )
    return table
