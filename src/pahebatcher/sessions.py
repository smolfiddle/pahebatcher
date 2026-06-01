"""Session manager for pahe_cache library."""

from __future__ import annotations

import contextlib
import json
import time
from pathlib import Path
from typing import Any

from rich import box
from rich.panel import Panel
from rich.prompt import Confirm, IntPrompt, Prompt
from rich.rule import Rule
from rich.table import Table

from pahebatcher.utils import fmt_bytes


class SessionManager:
    @staticmethod
    def get_sessions(cache_dir: Path) -> list[dict[str, Any]]:
        sessions: list[dict[str, Any]] = []
        if not cache_dir.exists():
            return sessions
        for folder in sorted(cache_dir.iterdir(), key=lambda f: f.stat().st_mtime, reverse=True):
            if not folder.is_dir():
                continue
            meta_file = folder / "session.json"
            if not meta_file.exists():
                continue
            try:
                meta = json.loads(meta_file.read_text(encoding="utf-8"))
                eps = [d for d in folder.iterdir() if d.is_dir() and d.name.startswith("Ep_")]
                segs = sum(len(list(d.glob("*.ts"))) for d in eps)
                size = sum(f.stat().st_size for f in folder.rglob("*"))
                sessions.append({
                    "path": folder,
                    "title": meta.get("title", folder.name),
                    "url": meta.get("url", ""),
                    "ep_count": len(eps),
                    "seg_count": segs,
                    "size": size,
                    "updated": meta.get("updated", folder.stat().st_mtime),
                })
            except Exception:
                continue
        return sessions

    @staticmethod
    def run(cache_dir: Path) -> str | None:
        from pahebatcher.ui.console import console

        while True:
            console.clear()
            console.print(Rule("[bold white] Session & Cache Manager [/bold white]", style="cyan"))

            sessions = SessionManager.get_sessions(cache_dir)
            total_size = sum(s["size"] for s in sessions)

            if not sessions:
                console.print("\n  [dim]No active sessions or cache found.[/dim]")
                Prompt.ask("\n  [cyan]Press Enter to return to menu[/cyan]")
                return None

            table = Table(box=box.ROUNDED, header_style="bold cyan", border_style="dim")
            table.add_column("#", justify="right", style="dim")
            table.add_column("Anime Title", ratio=1)
            table.add_column("Eps", justify="center")
            table.add_column("Segments", justify="center")
            table.add_column("Size", justify="right", style="green")
            table.add_column("Status", justify="center")

            for i, s in enumerate(sessions, 1):
                table.add_row(
                    str(i), s["title"], str(s["ep_count"]),
                    str(s["seg_count"]), fmt_bytes(s["size"]), "[yellow]Paused[/yellow]",
                )

            console.print(table)
            console.print(f"  [dim]Total Cache Size:[/dim] [bold cyan]{fmt_bytes(total_size)}[/bold cyan]\n")

            choices = ["B", "b", "R", "r", "D", "d", "C", "c"]
            prompt_text = (
                "  [cyan][R]esume[/cyan]  [cyan][D]elete[/cyan]  "
                "[cyan][C]lear All[/cyan]  [white][B]ack[/white] > "
            )
            choice = Prompt.ask(prompt_text, choices=choices, default="B", show_choices=False).upper()

            if choice == "B":
                return None
            if choice == "C":
                if Confirm.ask("  [red]Wipe entire cache folder?[/red]", default=False):
                    import shutil
                    shutil.rmtree(cache_dir, ignore_errors=True)
                    console.print("  [green]\u2713 Cache cleared.[/green]")
                    time.sleep(0.5)
                continue
            if choice in ("R", "D"):
                idx = IntPrompt.ask(f"  Select # to {'Resume' if choice == 'R' else 'Delete'}", default=1)
                if 1 <= idx <= len(sessions):
                    target = sessions[idx - 1]
                    if choice == "R":
                        return target["url"]
                    else:
                        if Confirm.ask(f"  [red]Delete session for '{target['title']}'?[/red]", default=True):
                            import shutil
                            shutil.rmtree(target["path"], ignore_errors=True)
                            console.print(f"  [green]\u2713 Deleted '{target['title']}'.[/green]")
                            time.sleep(0.5)
                continue
