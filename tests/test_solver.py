"""Tests for FlareSolverr solver."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import aiohttp

from pahebatcher.solver import Solver


class TestParseCookieString:
    def test_empty(self) -> None:
        assert Solver._parse_cookie_string("") == []

    def test_single(self) -> None:
        result = Solver._parse_cookie_string("a=1")
        assert result == [{"name": "a", "value": "1"}]

    def test_multiple(self) -> None:
        result = Solver._parse_cookie_string("a=1; b=2; c=3")
        assert len(result) == 3
        assert result[0]["name"] == "a"
        assert result[1]["value"] == "2"

    def test_with_spaces_and_equals_in_value(self) -> None:
        result = Solver._parse_cookie_string("cf_clearance=abc123; session=xyz=123")
        assert result[0] == {"name": "cf_clearance", "value": "abc123"}
        assert result[1]["value"] == "xyz=123"

    def test_trailing_semicolon(self) -> None:
        result = Solver._parse_cookie_string("a=1;")
        assert len(result) == 1


class TestSolverLifecycle:
    async def test_start_and_close(self) -> None:
        s = Solver("http://localhost:8191/v1")
        await s.start()
        assert s._http_session is not None
        await s.close()
        assert s._http_session is None

    async def test_session_property_requires_start(self) -> None:
        s = Solver("http://localhost:8191/v1")
        with pytest.raises(RuntimeError, match="not started"):
            _ = s._session  # type: ignore

    async def test_ensure_session_creates(self) -> None:
        s = Solver("http://localhost:8191/v1")
        await s.start()
        mock_resp = {"status": "ok", "session": "test-sid"}
        with patch.object(s, "_post", new_callable=AsyncMock, return_value=mock_resp):
            await s._ensure_session()
            assert s._session_id == "test-sid"
        await s.close()

    async def test_destroy_session(self) -> None:
        s = Solver("http://localhost:8191/v1")
        await s.start()
        s._session_id = "sid123"
        with patch.object(s, "_post", new_callable=AsyncMock, return_value=None) as mock_post:
            await s.destroy_session()
            assert s._session_id is None
            mock_post.assert_called_once()
        await s.close()


class TestSolverRequest:
    async def test_ping_success(self) -> None:
        s = Solver("http://localhost:8191/v1")
        await s.start()
        mock_resp = MagicMock()
        mock_resp.json = AsyncMock(return_value={"status": "ok"})
        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)
        with patch.object(s._session, "get", return_value=mock_ctx):
            result = await s.ping()
            assert result is True
        await s.close()

    async def test_ping_failure(self) -> None:
        s = Solver("http://localhost:8191/v1")
        await s.start()
        with patch.object(s._session, "get", side_effect=Exception("fail")):
            result = await s.ping()
            assert result is False
        await s.close()

    async def test_request_cache_hit(self) -> None:
        s = Solver("http://localhost:8191/v1")
        await s.start()
        await s._solver_cache.set("https://example.com", {"response": "cached"})
        result = await s.request("https://example.com", cache=True)
        assert result == {"response": "cached"}
        await s.close()

    async def test_fetch_json_pre_tag(self) -> None:
        s = Solver("http://localhost:8191/v1")
        await s.start()
        html = '<html><pre>{"key": "value"}</pre></html>'
        with patch.object(s, "request", new_callable=AsyncMock, return_value={"response": html}):
            result = await s.fetch_json("https://example.com")
            assert result == {"key": "value"}
        await s.close()

    async def test_fetch_json_stripped_html(self) -> None:
        s = Solver("http://localhost:8191/v1")
        await s.start()
        html = '{"direct": "json"}'
        with patch.object(s, "request", new_callable=AsyncMock, return_value={"response": html}):
            result = await s.fetch_json("https://example.com")
            assert result == {"direct": "json"}
        await s.close()

    async def test_fetch_html(self) -> None:
        s = Solver("http://localhost:8191/v1")
        await s.start()
        with patch.object(
            s, "request", new_callable=AsyncMock, return_value={"response": "<h1>Title</h1>", "cookies": []},
        ):
            result = await s.fetch_html("https://example.com")
            assert result is not None
            html, cookies = result
            assert "<h1>Title</h1>" in html
        await s.close()

    async def test_fetch_html_none(self) -> None:
        s = Solver("http://localhost:8191/v1")
        await s.start()
        with patch.object(s, "request", new_callable=AsyncMock, return_value=None):
            result = await s.fetch_html("https://example.com")
            assert result is None
        await s.close()
