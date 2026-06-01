"""Tests for SessionManager."""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import patch

from pahebatcher.sessions import SessionManager


class TestSessionManager:
    def test_empty_cache(self, tmp_path: Path) -> None:
        sessions = SessionManager.get_sessions(tmp_path / "nonexistent")
        assert sessions == []

    def test_get_sessions(self, tmp_path: Path) -> None:
        cache = tmp_path / "cache"
        session_dir = cache / "Test_Anime_session-1"
        session_dir.mkdir(parents=True)
        ep_dir = session_dir / "Ep_001_JPN"
        ep_dir.mkdir(parents=True)
        (ep_dir / "000000.ts").write_bytes(b"data")

        meta = {"title": "Test Anime", "url": "https://example.com", "updated": time.time()}
        (session_dir / "session.json").write_text(json.dumps(meta))

        sessions = SessionManager.get_sessions(cache)
        assert len(sessions) == 1
        assert sessions[0]["title"] == "Test Anime"
        assert sessions[0]["ep_count"] == 1
        assert sessions[0]["seg_count"] == 1
        assert sessions[0]["url"] == "https://example.com"

    def test_get_sessions_no_metadata(self, tmp_path: Path) -> None:
        cache = tmp_path / "cache"
        (cache / "Some_Dir").mkdir(parents=True)
        sessions = SessionManager.get_sessions(cache)
        assert sessions == []

    def test_get_sessions_invalid_json(self, tmp_path: Path) -> None:
        cache = tmp_path / "cache"
        session_dir = cache / "Bad_Session"
        session_dir.mkdir(parents=True)
        (session_dir / "session.json").write_text("not json")
        sessions = SessionManager.get_sessions(cache)
        assert sessions == []
