"""Tests for ConfigManager."""

from __future__ import annotations

import tomllib
from pathlib import Path

from pahebatcher.config_manager import ConfigManager


class TestConfigManagerDefaults:
    def test_defaults_loaded(self) -> None:
        cm = ConfigManager(Path("/tmp/nonexistent_config.toml"))
        cm.load()  # file doesn't exist, should keep defaults
        assert cm.get("quality") == 1080
        assert cm.get("audio_lang") == "jpn"
        assert cm.get("max_parallel") == 2
        assert cm.get("hls_workers") == 24


class TestConfigManagerSaveLoad:
    def test_roundtrip(self, tmp_path: Path) -> None:
        path = tmp_path / "config.toml"
        cm = ConfigManager(path)
        cm.set("quality", 720)
        cm.set("audio_lang", "eng")
        cm.save()
        assert path.exists()

        cm2 = ConfigManager(path)
        cm2.load()
        assert cm2.get("quality") == 720
        assert cm2.get("audio_lang") == "eng"
        assert cm2.get("max_parallel") == 2  # default

    def test_partial_file(self, tmp_path: Path) -> None:
        path = tmp_path / "config.toml"
        path.write_text("quality = 360\n")
        cm = ConfigManager(path)
        cm.load()
        assert cm.get("quality") == 360
        assert cm.get("audio_lang") == "jpn"  # default from DEFAULTS

    def test_invalid_key_raises(self, tmp_path: Path) -> None:
        cm = ConfigManager(tmp_path / "config.toml")
        try:
            cm.set("nonexistent_key", 123)
            assert False, "should have raised"
        except KeyError:
            pass

    def test_invalid_quality_raises(self, tmp_path: Path) -> None:
        cm = ConfigManager(tmp_path / "config.toml")
        try:
            cm.set("quality", 999)
            assert False, "should have raised"
        except ValueError:
            pass

    def test_validate_clamps_parallel(self, tmp_path: Path) -> None:
        cm = ConfigManager(tmp_path / "config.toml")
        cm.set("max_parallel", 100)
        assert cm.get("max_parallel") == 6

    def test_validate_clamps_workers(self, tmp_path: Path) -> None:
        cm = ConfigManager(tmp_path / "config.toml")
        cm.set("hls_workers", 100)
        assert cm.get("hls_workers") == 32
        cm.set("hls_workers", 2)
        assert cm.get("hls_workers") == 8

    def test_keep_temp_conversion(self, tmp_path: Path) -> None:
        cm = ConfigManager(tmp_path / "config.toml")
        cm.set("keep_temp", True)
        assert cm.get("keep_temp") is True
        cm.set("keep_temp", False)
        assert cm.get("keep_temp") is False

    def test_show_returns_all_keys(self, tmp_path: Path) -> None:
        cm = ConfigManager(tmp_path / "config.toml")
        data = cm.show()
        assert "quality" in data
        assert "audio_lang" in data
        assert "max_parallel" in data
        assert "hls_workers" in data
        assert "output_dir" in data
        assert "keep_temp" in data
