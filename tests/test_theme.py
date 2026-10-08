"""Theme coherence — brand styles resolve and render without MissingStyle."""

from __future__ import annotations

from pathlib import Path

from rich.console import Console

from pahebatcher.models import AnimeInfo, EpisodeInfo
from pahebatcher.ui.tables import episode_table, search_results_table, summary_table
from pahebatcher.ui.theme import THEME


def _themed_console() -> Console:
    return Console(theme=THEME, force_terminal=True, color_system="truecolor", width=100)


class TestThemeNames:
    def test_all_names_resolve(self) -> None:
        console = _themed_console()
        for name in [
            "brand", "brand.bold", "brand.dim",
            "accent", "accent.bold", "soft", "soft.bold", "pale", "pale.bold",
        ]:
            console.get_style(name)  # raises MissingStyle if unknown

    def test_no_bare_attribute_combos_in_source(self) -> None:
        # Rich>=15 cannot parse "bold brand"/"dim brand" — dotted names only.
        import pahebatcher.ui.console as console_mod

        src = Path(console_mod.__file__).read_text(encoding="utf-8")
        assert "bold brand" not in src
        assert "dim brand" not in src


class TestThemeRenders:
    def test_banner_renders_pink(self) -> None:
        import pahebatcher.ui.console as console_mod

        console = _themed_console()
        old, console_mod.console = console_mod.console, console
        try:
            with console.capture() as cap:
                console_mod.print_banner()
            out = cap.get()
        finally:
            console_mod.console = old
        assert "255;63;144" in out  # brand pink ANSI present

    def test_tables_render(self, sample_anime: AnimeInfo, tmp_path: Path) -> None:
        console = _themed_console()
        out = tmp_path / "out.mp4"
        out.write_bytes(b"data")
        eps = [
            EpisodeInfo(1, "s1", "T1", "F", "jpn", "u"),
            EpisodeInfo(2, "s2", "T2", "F", "eng", "u"),
        ]
        with console.capture():
            console.print(episode_table(sample_anime, sample_anime.episodes))
            console.print(search_results_table([{
                "title": "A", "type": "TV", "year": "2020",
                "episodes": "12", "score": "8.5",
            }], "q"))
            console.print(summary_table({"s1": out, "s2": None}, eps, str(tmp_path)))

    def test_dashboard_renders(self) -> None:
        from pahebatcher.ui.dashboard import Dashboard

        console = _themed_console()
        dash = Dashboard(1, console=console)
        dash.start()
        try:
            dash.add_ep("1", "Ep 1")
            dash.set_total("1", 5)
            dash.mark_resolving("1", "resolving")
            dash.mark_queued("1", "queued")
            dash.mark_downloading("1", "downloading")
            dash.seg_done("1", 10)
            dash.mark_remuxing("1", "remux")
            dash.mark_done("1", "done")
            dash.mark_retry("1", "retry")
            dash.mark_fail("1", "fail")
        finally:
            dash.stop()
