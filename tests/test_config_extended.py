"""Tests for config constants and manager edge cases."""

from __future__ import annotations

import json
from pathlib import Path

from pahebatcher import config
from pahebatcher.config_manager import ConfigManager


class TestConfigConstants:
    def test_retry_constants(self) -> None:
        assert config.RETRY_ATTEMPTS == 5
        assert config.RETRY_BASE_DELAY == 0.5
        assert config.REQUEST_DELAY == 0.4

    def test_version_matches_package(self) -> None:
        from pahebatcher import __version__
        assert config.VERSION == __version__


class TestConfigManagerValidation:
    def test_invalid_quality_raises(self, tmp_path: Path) -> None:
        cm = ConfigManager(tmp_path / "cfg.toml")
        try:
            cm.set("quality", 999)
            assert False, "should raise"
        except ValueError:
            pass

    def test_clamping_max_parallel(self, tmp_path: Path) -> None:
        cm = ConfigManager(tmp_path / "cfg.toml")
        cm.set("max_parallel", 100)
        assert cm.get("max_parallel") == 6
        cm.set("max_parallel", -5)
        assert cm.get("max_parallel") == 1

    def test_clamping_hls_workers(self, tmp_path: Path) -> None:
        cm = ConfigManager(tmp_path / "cfg.toml")
        cm.set("hls_workers", 100)
        assert cm.get("hls_workers") == 32
        cm.set("hls_workers", 0)
        assert cm.get("hls_workers") == 8

    def test_unknown_key_raises(self, tmp_path: Path) -> None:
        cm = ConfigManager(tmp_path / "cfg.toml")
        try:
            cm.set("unknown", 123)
            assert False
        except KeyError:
            pass

    def test_load_corrupted_toml(self, tmp_path: Path) -> None:
        cfg = tmp_path / "cfg.toml"
        cfg.write_text("not toml [[[")
        cm = ConfigManager(cfg)
        try:
            cm.load()
            assert False, "should raise"
        except Exception:
            pass  # expected

    def test_save_and_load_roundtrip(self, tmp_path: Path) -> None:
        cfg = tmp_path / "cfg.toml"
        cm = ConfigManager(cfg)
        cm.set("quality", 720)
        cm.set("audio_lang", "eng")
        cm.save()
        cm2 = ConfigManager(cfg)
        cm2.load()
        assert cm2.get("quality") == 720
        assert cm2.get("audio_lang") == "eng"
        assert cm2.get("resolve_ahead") == 999  # default preserved
