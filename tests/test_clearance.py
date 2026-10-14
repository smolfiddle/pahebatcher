"""Tests for per-domain clearance store + direct fast path (solver)."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, patch

from pahebatcher.solver import Solver, _host_of, _is_challenge_body


def _solver(**kwargs: Any) -> Solver:
    return Solver("http://localhost:8191/v1", **kwargs)


def _ok_solution(cookies: list[dict[str, str]] | None = None) -> dict[str, Any]:
    return {
        "status": "ok",
        "session": "s1",
        "solution": {
            "response": '{"data": []}',
            "cookies": cookies or [],
            "userAgent": "UA",
        },
    }


def _challenge() -> str:
    return "<html><head><title>Just a moment...</title></head><body>__cf_chl</body></html>"


class TestHelpers:
    def test_host_of(self) -> None:
        assert _host_of("https://AnimePahe.PW/api?m=search&q=x") == "animepahe.pw"

    def test_is_challenge_body(self) -> None:
        assert _is_challenge_body(_challenge()) is True
        assert _is_challenge_body('{"data": [], "total": 0}') is False
        assert _is_challenge_body("") is False


class TestFastPath:
    async def test_hit_skips_browser(self) -> None:
        solver = _solver(user_cookies="cf_clearance=abc")
        with patch(
            "pahebatcher.solver._direct_fetch_sync",
            return_value=(200, '{"data": []}', []),
        ) as mock_direct, patch.object(solver, "_post", new_callable=AsyncMock) as mock_post:
            mock_post.side_effect = AssertionError("browser must not be hit")
            sol = await solver.request("https://animepahe.pw/api?m=search&q=x", cache=False)
            assert sol is not None
            assert sol["response"] == '{"data": []}'
            mock_post.assert_not_called()
        _, header, _ = mock_direct.call_args[0]
        assert "cf_clearance=abc" in header

    async def test_transport_failure_falls_back(self) -> None:
        solver = _solver(user_cookies="cf_clearance=abc")
        with (
            patch("pahebatcher.solver._direct_fetch_sync", return_value=None),
            patch.object(solver, "_post", new_callable=AsyncMock, return_value=_ok_solution()) as mock_post,
        ):
            sol = await solver.request("https://animepahe.pw/api?m=search&q=x", cache=False)
            assert sol is not None
            assert mock_post.called

    async def test_challenge_body_falls_back_and_invalidates(self) -> None:
        solver = _solver(user_cookies="cf_clearance=stale")
        with (
            patch("pahebatcher.solver._direct_fetch_sync", return_value=(403, _challenge(), [])),
            patch.object(solver, "_post", new_callable=AsyncMock, return_value=_ok_solution()) as mock_post,
        ):
            sol = await solver.request("https://animepahe.pw/api?m=search&q=x", cache=False)
            assert sol is not None
            assert mock_post.called
        # Transplant marked dead, nothing harvested (browser solution had no cookies)
        assert "animepahe.pw" in solver._clearance_dead
        assert await solver._clearance.get("animepahe.pw") is None
        # Next request goes straight to browser (no cookies left to try direct)
        with patch(
            "pahebatcher.solver._direct_fetch_sync",
            side_effect=AssertionError("no direct attempt expected"),
        ), patch.object(solver, "_post", new_callable=AsyncMock, return_value=_ok_solution()):
            sol2 = await solver.request("https://animepahe.pw/api?m=search&q=y", cache=False)
            assert sol2 is not None

    async def test_non_challenge_error_keeps_store(self) -> None:
        solver = _solver(user_cookies="cf_clearance=abc")
        await solver._note_clearance_success("animepahe.pw", [{"name": "cf_clearance", "value": "abc"}])
        with (
            patch("pahebatcher.solver._direct_fetch_sync", return_value=(429, "slow down", [])),
            patch.object(solver, "_post", new_callable=AsyncMock, return_value=_ok_solution()),
        ):
            sol = await solver.request("https://animepahe.pw/api?m=search&q=x", cache=False)
            assert sol is not None
        # Transient 429 must not invalidate or mark dead
        assert "animepahe.pw" not in solver._clearance_dead
        assert await solver._clearance.get("animepahe.pw") == [{"name": "cf_clearance", "value": "abc"}]

    async def test_solution_cache_still_works(self) -> None:
        solver = _solver()
        with patch.object(solver, "_post", new_callable=AsyncMock, return_value=_ok_solution()) as mock_post:
            first = await solver.request("https://animepahe.pw/api?m=search&q=x", cache=True)
            calls_after_first = mock_post.call_count
            assert calls_after_first > 0
            second = await solver.request("https://animepahe.pw/api?m=search&q=x", cache=True)
            assert first is not None and second is not None
            assert mock_post.call_count == calls_after_first


class TestHarvestAndPrecedence:
    async def test_harvest_on_browser_success(self) -> None:
        solver = _solver()
        with patch("pahebatcher.solver._direct_fetch_sync", return_value=None), patch.object(
            solver, "_post", new_callable=AsyncMock,
            return_value=_ok_solution([
                {"name": "cf_clearance", "value": "fresh"},
                {"name": "other", "value": "x"},
            ]),
        ):
            sol = await solver.request("https://animepahe.pw/api?m=search&q=x", cache=False)
            assert sol is not None
        # Only cf_clearance harvested, other cookies dropped
        assert await solver._clearance.get("animepahe.pw") == [{"name": "cf_clearance", "value": "fresh"}]

    async def test_transplant_wins_until_dead(self) -> None:
        solver = _solver(user_cookies="cf_clearance=user1")
        await solver._note_clearance_success("animepahe.pw", [{"name": "cf_clearance", "value": "harv"}])
        with patch(
            "pahebatcher.solver._direct_fetch_sync",
            return_value=(200, '{"data": []}', []),
        ) as mock_direct, patch.object(solver, "_post", new_callable=AsyncMock) as mock_post:
            await solver.request("https://animepahe.pw/api?m=search&q=x", cache=False)
            mock_post.assert_not_called()
        _, header, _ = mock_direct.call_args[0]
        assert "cf_clearance=user1" in header
        assert "harv" not in header

    async def test_harvested_used_when_no_transplant(self) -> None:
        solver = _solver()
        await solver._note_clearance_success("animepahe.pw", [{"name": "cf_clearance", "value": "harv"}])
        with patch(
            "pahebatcher.solver._direct_fetch_sync",
            return_value=(200, '{"data": []}', []),
        ) as mock_direct, patch.object(solver, "_post", new_callable=AsyncMock) as mock_post:
            await solver.request("https://animepahe.pw/api?m=search&q=x", cache=False)
            mock_post.assert_not_called()
        _, header, _ = mock_direct.call_args[0]
        assert "cf_clearance=harv" in header

    async def test_domain_isolation(self) -> None:
        solver = _solver()
        await solver._note_clearance_success("animepahe.pw", [{"name": "cf_clearance", "value": "pw1"}])
        with patch(
            "pahebatcher.solver._direct_fetch_sync",
            side_effect=AssertionError("must not try direct for other domain"),
        ), patch.object(solver, "_post", new_callable=AsyncMock, return_value=_ok_solution()):
            sol = await solver.request("https://animepahe.com/api?m=search&q=x", cache=False)
            assert sol is not None

    async def test_browser_body_uses_transplant_then_harvested(self) -> None:
        solver = _solver(user_cookies="cf_clearance=user1")
        bodies: list[dict[str, Any]] = []

        async def fake_post(body: dict[str, Any], timeout: int = 90) -> dict[str, Any]:
            bodies.append(body)
            return _ok_solution()

        with (
            patch("pahebatcher.solver._direct_fetch_sync", return_value=None),
            patch.object(solver, "_post", new=fake_post),
        ):
            await solver.request("https://animepahe.pw/api?m=search&q=x", cache=False)
        sent = [b for b in bodies if b.get("cmd") == "request.get"]
        assert sent and sent[0]["cookies"] == [{"name": "cf_clearance", "value": "user1"}]


class TestBrowserInvalidation:
    async def test_challenge_message_drops_harvested(self) -> None:
        solver = _solver()
        solver._session_id = "s1"
        await solver._note_clearance_success("animepahe.pw", [{"name": "cf_clearance", "value": "old"}])
        challenge = {"status": "error", "message": "Cloudflare challenge detected"}
        with patch("pahebatcher.solver._direct_fetch_sync", return_value=None), patch.object(
            solver, "_post", new_callable=AsyncMock,
            side_effect=[challenge, {"status": "ok"}, _ok_solution()],
        ):
            sol = await solver.request("https://animepahe.pw/api?m=search&q=x", cache=False)
            assert sol is not None
        assert await solver._clearance.get("animepahe.pw") is None


class TestTtl:
    async def test_expiry(self) -> None:
        solver = _solver(clearance_ttl=0.05)
        await solver._note_clearance_success("animepahe.pw", [{"name": "cf_clearance", "value": "v"}])
        assert await solver._clearance.get("animepahe.pw") == [{"name": "cf_clearance", "value": "v"}]
        await asyncio.sleep(0.1)
        with patch(
            "pahebatcher.solver._direct_fetch_sync",
            side_effect=AssertionError("expired, must not try direct"),
        ), patch.object(solver, "_post", new_callable=AsyncMock, return_value=_ok_solution()):
            sol = await solver.request("https://animepahe.pw/api?m=search&q=x", cache=False)
            assert sol is not None

    async def test_zero_disables_store_and_fast_path(self) -> None:
        solver = _solver(user_cookies="cf_clearance=u", clearance_ttl=0)
        with patch(
            "pahebatcher.solver._direct_fetch_sync",
            side_effect=AssertionError("disabled, must not try direct"),
        ):
            bodies: list[dict[str, Any]] = []

            async def fake_post(body: dict[str, Any], timeout: int = 90) -> dict[str, Any]:
                bodies.append(body)
                return _ok_solution([{"name": "cf_clearance", "value": "new"}])

            with patch.object(solver, "_post", new=fake_post):
                sol = await solver.request("https://animepahe.pw/api?m=search&q=x", cache=False)
                assert sol is not None
        # Legacy behavior: transplant still forwarded, nothing harvested
        sent = [b for b in bodies if b.get("cmd") == "request.get"]
        assert sent and sent[0]["cookies"] == [{"name": "cf_clearance", "value": "u"}]
        assert await solver._clearance.get("animepahe.pw") is None
