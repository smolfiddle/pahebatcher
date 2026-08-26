"""Rich-based live dashboard for download progress."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from rich.live import Live
    from rich.progress import TaskID


from rich import box
from rich.console import Group
from rich.live import Live
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TaskID,
    TextColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)
from rich.rule import Rule
from rich.table import Table

from pahebatcher.config import VERSION
from pahebatcher.utils import fmt_bytes


class Dashboard:
    def __init__(self, total_eps: int, console: Any | None = None) -> None:
        self._total_eps = total_eps
        self._done_eps = 0
        self._tasks: dict[str, TaskID] = {}
        self._live: Live | None = None

        if console is None:
            from pahebatcher.ui.console import console as default_console
            console = default_console

        self._console = console
        self._progress = Progress(
            SpinnerColumn(style="cyan", finished_text=" "),
            TextColumn("[bold white]{task.description:<32}"),
            BarColumn(bar_width=16, style="cyan", complete_style="bold green"),
            MofNCompleteColumn(),
            TextColumn("[bold green]{task.percentage:>4.0f}%"),
            TransferSpeedColumn(),
            TimeRemainingColumn(),
            TextColumn("[dim cyan]{task.fields[size]:>10}[/dim cyan]"),
            console=console,
            expand=False,
        )

    def start(self) -> None:
        header = Table.grid(padding=(0, 1))
        header.add_column(width=3)
        header.add_column(width=32)
        header.add_column(width=16)
        header.add_column(width=10, justify="center")
        header.add_column(width=5, justify="center")
        header.add_column(width=11, justify="center")
        header.add_column(width=10, justify="center")
        header.add_column(width=10, justify="right")
        header.add_row(
            "", "[bold white]Episode Title[/bold white]",
            "[bold white]Progress[/bold white]", "[bold white]Segments[/bold white]",
            "[bold white]%[/bold white]", "[bold white]Speed[/bold white]",
            "[bold white]ETA[/bold white]", "[bold white]Size[/bold white]",
        )

        self._console.clear()
        self._live = Live(
            Panel(
                Group(header, Rule(style="dim"), self._progress),
                title=(
                    f"[bold cyan]pahe-batcher[/bold cyan]"
                    f"  [dim]v{VERSION}  -  {self._total_eps} episodes[/dim]"
                ),
                border_style="cyan", box=box.ROUNDED, padding=(0, 1),
            ),
            console=self._console, refresh_per_second=8,
        )
        self._live.start()

    def stop(self) -> None:
        if self._live:
            self._live.stop()
        time.sleep(0.1)

    def add_ep(self, key: str, label: str, total_segments: int = 0) -> None:
        if key not in self._tasks:
            kwargs: dict[str, Any] = {"size": "0 B", "bytes_done": 0}
            if total_segments:
                kwargs["total"] = total_segments
            self._tasks[key] = self._progress.add_task(label[:32], **kwargs)
        else:
            tid = self._tasks[key]
            self._progress.update(tid, description=label[:32])
            if total_segments and self._progress.tasks[tid].total is None:
                self._progress.update(tid, total=total_segments)

    def set_total(self, key: str, n_segments: int) -> None:
        if (tid := self._tasks.get(key)) is not None:
            self._progress.update(tid, total=n_segments)

    def seg_done(self, key: str, nbytes: int) -> None:
        if (tid := self._tasks.get(key)) is not None:
            task = self._progress.tasks[tid]
            new_bytes = (task.fields.get("bytes_done", 0)) + nbytes
            self._progress.update(tid, advance=1, bytes_done=new_bytes, size=fmt_bytes(new_bytes))

    def mark_resolving(self, key: str, label: str) -> None:
        if (tid := self._tasks.get(key)) is not None:
            self._progress.update(tid, description=f"[cyan]\u27f3 {label[:30]}[/cyan]", size="resolving")

    def mark_waiting(self, key: str, label: str) -> None:
        if (tid := self._tasks.get(key)) is not None:
            self._progress.update(tid, description=f"[dim]\u22ef {label[:30]}[/dim]", size="waiting")

    def mark_queued(self, key: str, label: str) -> None:
        if (tid := self._tasks.get(key)) is not None:
            self._progress.update(tid, description=f"[dim cyan]\u231b {label[:30]}[/dim cyan]", size="queued")

    def mark_downloading(self, key: str, label: str) -> None:
        if (tid := self._tasks.get(key)) is not None:
            self._progress.update(tid, description=f"[bold white]{label[:32]}[/bold white]")

    def mark_remuxing(self, key: str, label: str) -> None:
        if (tid := self._tasks.get(key)) is not None:
            self._progress.update(
                tid, description=f"[yellow]\u27f3 Remuxing {label[:30]}[/yellow]", size="muxing",
            )
            self._progress.stop_task(tid)

    def mark_done(self, key: str, label: str) -> None:
        self._done_eps += 1
        if (tid := self._tasks.get(key)) is not None:
            t = self._progress.tasks[tid]
            total = t.total or t.completed or 1
            final_size = t.fields.get("size", "done")
            self._progress.update(
                tid, description=f"[bold green]\u2713 {label[:32]}[/bold green]",
                completed=total, total=total, size=final_size,
            )
            self._progress.stop_task(tid)

    def mark_retry(self, key: str, label: str) -> None:
        if (tid := self._tasks.get(key)) is not None:
            self._progress.update(tid, description=f"[yellow]\u27f3 {label[:30]}[/yellow]", size="retry")

    def mark_fail(self, key: str, reason: str) -> None:
        if (tid := self._tasks.get(key)) is not None:
            self._progress.update(tid, description=f"[red]\u2717 {reason[:32]}[/red]", size="fail")
            self._progress.stop_task(tid)
