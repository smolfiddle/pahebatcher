"""Rich console singleton and banner."""

from rich import box
from rich.align import Align
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from pahebatcher.config import VERSION

console = Console()

_BANNER_LINES = [
    r" ____       _            ____        _       _",
    r"|  _ \ __ _| |__   ___  | __ )  __ _| |_ ___| |__   ___ _ __",
    r"| |_) / _` | '_ \ / _ \ |  _ \ / _` | __/ __| '_ \ / _ \ '__|",
    r"|  __/ (_| | | | |  __/ | |_) | (_| | || (__| | | |  __/ |",
    r"|_|   \__,_|_| |_|\___| |____/ \__,_|\__\___|_| |_|\___|_|",
]

# AnimePahe pink gradient (logo by u/HellHarbinger, see assets/logo/pahebatcher.svg):
# hot pink up top fading to white at the bottom.
_BANNER_STYLES = [
    "bold #ff3f90",
    "bold #fb6cab",
    "bold #f9a8cd",
    "bold #fbdcef",
    "bold white",
]


def print_banner() -> None:
    banner = Text()
    for line, style in zip(_BANNER_LINES, _BANNER_STYLES, strict=True):
        banner.append(line + "\n", style=style)
    banner.append(f"  v{VERSION}  \u00b7  AnimePahe Batch Downloader\n", style="dim")
    console.print(Panel(
        Align.center(banner),
        border_style="#ff3f90", box=box.DOUBLE, padding=(0, 2),
    ))
