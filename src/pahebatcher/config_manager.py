"""Persistent TOML configuration manager for pahebatcher."""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any


class ConfigManager:
    """Read/write persistent settings at ~/.config/pahebatcher/config.toml."""

    DEFAULT_PATH = Path.home() / ".config" / "pahebatcher" / "config.toml"

    DEFAULTS: dict[str, Any] = {
        "quality": 1080,
        "audio_lang": "jpn",
        "max_parallel": 2,
        "hls_workers": 24,
        "output_dir": ".",
        "keep_temp": False,
    }

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or self.DEFAULT_PATH
        self.data: dict[str, Any] = dict(self.DEFAULTS)

    def load(self) -> None:
        if not self.path.exists():
            return
        raw = self.path.read_bytes()
        loaded = tomllib.loads(raw.decode("utf-8"))
        for key in self.DEFAULTS:
            if key in loaded:
                self.data[key] = loaded[key]

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lines: list[str] = ["# pahebatcher configuration", ""]
        for key, default in self.DEFAULTS.items():
            val = self.data.get(key, default)
            lines.append(f"{key} = {self._format(val)}")
        lines.append("")
        self.path.write_text("\n".join(lines), encoding="utf-8")

    def get(self, key: str) -> Any:
        return self.data.get(key, self.DEFAULTS.get(key))

    def set(self, key: str, value: Any) -> None:
        validated = self._validate(key, value)
        self.data[key] = validated

    def show(self) -> dict[str, Any]:
        return dict(self.data)

    # ── CLI interface ──────────────────────────────────────────────────────

    @staticmethod
    def cli_show(path: Path | None = None) -> None:
        from pahebatcher.ui.console import console
        from rich.table import Table
        from rich import box

        cm = ConfigManager(path)
        cm.load()
        t = Table(box=box.ROUNDED, header_style="bold cyan", title="pahebatcher configuration")
        t.add_column("Key", style="cyan")
        t.add_column("Value", style="white")
        t.add_column("Default", style="dim")
        for key in cm.DEFAULTS:
            t.add_row(key, str(cm.get(key)), str(cm.DEFAULTS[key]))
        console.print(t)

    @staticmethod
    def cli_set(key: str, value: str, path: Path | None = None) -> None:
        from pahebatcher.ui.console import console

        cm = ConfigManager(path)
        cm.load()
        converted = cm._convert(key, value)
        cm.set(key, converted)
        cm.save()
        console.print(f"  [green]\u2713[/green] {key} = {converted}")

    @staticmethod
    def cli_reset(path: Path | None = None) -> None:
        from pahebatcher.ui.console import console

        cm = ConfigManager(path)
        cm.data = dict(cm.DEFAULTS)
        cm.save()
        console.print("  [green]\u2713[/green] Configuration reset to defaults")

    # ── internal helpers ───────────────────────────────────────────────────

    def _validate(self, key: str, value: Any) -> Any:
        validators: dict[str, Any] = {
            "quality": lambda v: v if v in (360, 720, 1080) else None,
            "audio_lang": lambda v: v if v in ("jpn", "eng") else None,
            "max_parallel": lambda v: max(1, min(6, int(v))),
            "hls_workers": lambda v: max(8, min(32, int(v))),
            "output_dir": lambda v: str(v),
            "keep_temp": lambda v: bool(v),
        }
        if key not in validators:
            raise KeyError(f"Unknown config key: {key}")
        result = validators[key](value)
        if result is None:
            raise ValueError(f"Invalid value for {key}: {value}")
        return result

    def _convert(self, key: str, raw: str) -> Any:
        converters: dict[str, Any] = {
            "quality": int,
            "audio_lang": str,
            "max_parallel": int,
            "hls_workers": int,
            "output_dir": str,
            "keep_temp": lambda v: v.lower() in ("true", "yes", "1", "on"),
        }
        if key not in converters:
            raise KeyError(f"Unknown config key: {key}")
        return converters[key](raw)

    @staticmethod
    def _format(value: Any) -> str:
        if isinstance(value, bool):
            return str(value).lower()
        if isinstance(value, str):
            return repr(value)
        return str(value)
