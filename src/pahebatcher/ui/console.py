"""Rich console singleton and banner."""

from rich import box
from rich.align import Align
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from pahebatcher.config import VERSION

console = Console()

_BANNER = r"""
 ____       _            ____        _       _
|  _ \ __ _| |__   ___  | __ )  __ _| |_ ___| |__   ___ _ __
| |_) / _` | '_ \ / _ \ |  _ \ / _` | __/ __| '_ \ / _ \ '__|
|  __/ (_| | | | |  __/ | |_) | (_| | || (__| | | |  __/ |
|_|   \__,_|_| |_|\___| |____/ \__,_|\__\___|_| |_|\___|_|
"""


def print_banner() -> None:
    console.print(Panel(
        Align.center(
            Text(_BANNER, style="bold cyan") +
            Text(f"\n  v{VERSION}  \u00b7  AnimePahe Batch Downloader\n", style="dim cyan")
        ),
        border_style="cyan", box=box.DOUBLE, padding=(0, 2),
    ))
