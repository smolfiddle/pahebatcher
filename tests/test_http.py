"""Tests for HttpClient."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import aiohttp

from pahebatcher.http import HttpClient


class TestGetCurlSession:
    def test_thread_local_reuse(self) -> None:
        from pahebatcher.http import _get_curl_session

        with patch("curl_cffi.requests.Session") as mock_sess:
            mock_inst = MagicMock()
            mock_sess.return_value = mock_inst
            s1 = _get_curl_session()
            s2 = _get_curl_session()
            assert s1 is s2
            mock_sess.assert_called_once()


class TestHttpClientLifecycle:
    async def test_start_close(self) -> None:
        c = HttpClient(hls_workers=8)
        await c.start()
        assert c._session is not None
        assert c.session is not None
        await c.close()
        assert c._session is None

    async def test_session_requires_start(self) -> None:
        c = HttpClient()
        with pytest.raises(RuntimeError, match="not started"):
            _ = c.session

    async def test_get_success(self) -> None:
        c = HttpClient(hls_workers=8)
        await c.start()
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.read = AsyncMock(return_value=b"hello")
        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)
        with patch.object(c.session, "get", return_value=mock_ctx):
            data = await c.get("https://example.com")
            assert data == b"hello"
        await c.close()

    async def test_get_retry_on_client_error(self) -> None:
        c = HttpClient(hls_workers=8)
        await c.start()
        # First call raises, second succeeds
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.read = AsyncMock(return_value=b"ok")
        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)

        call_count = 0

        def _fake_get(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # Return context that raises ClientError on enter
                err_ctx = MagicMock()
                err_ctx.__aenter__ = AsyncMock(side_effect=aiohttp.ClientError("fail"))
                err_ctx.__aexit__ = AsyncMock(return_value=False)
                return err_ctx
            return mock_ctx

        with patch.object(c.session, "get", side_effect=_fake_get):
            with patch("asyncio.sleep", new_callable=AsyncMock):
                data = await c.get("https://example.com")
                assert data == b"ok"
        await c.close()

    async def test_403_fallback_to_curl(self) -> None:
        c = HttpClient(hls_workers=8)
        await c.start()
        # Simulate 403
        async def _fake_curl(url, headers, timeout):
            return b"curl_data"

        err = aiohttp.ClientResponseError(
            request_info=MagicMock(), history=(), status=403, message="forbidden",
        )
        err_ctx = MagicMock()
        err_ctx.__aenter__ = AsyncMock(side_effect=err)
        err_ctx.__aexit__ = AsyncMock(return_value=False)
        with patch.object(c.session, "get", return_value=err_ctx):
            with patch("pahebatcher.http._curl_fetch", side_effect=_fake_curl) as mock_curl:
                # Need loop for executor; mock run_in_executor to call directly
                with patch.object(c._loop, "run_in_executor", new_callable=AsyncMock, return_value=b"curl_data"):
                    data = await c.get("https://example.com")
                    assert data == b"curl_data"
        await c.close()


class TestCurlFetch:
    def test_curl_fetch_success(self) -> None:
        from pahebatcher.http import _curl_fetch

        mock_r = MagicMock()
        mock_r.content = b"data"
        mock_r.raise_for_status = MagicMock()
        mock_sess = MagicMock()
        mock_sess.get.return_value = mock_r
        with patch("pahebatcher.http._get_curl_session", return_value=mock_sess):
            data = _curl_fetch("https://example.com", None, 30)
            assert data == b"data"

    def test_curl_fetch_timeout_retry(self) -> None:
        from pahebatcher.http import _curl_fetch
        import curl_cffi.requests

        mock_sess = MagicMock()
        # First call raises Timeout, second succeeds
        mock_r = MagicMock()
        mock_r.content = b"ok"
        mock_r.raise_for_status = MagicMock()
        mock_sess.get.side_effect = [
            curl_cffi.requests.exceptions.Timeout("timeout"),
            mock_r,
        ]
        with patch("pahebatcher.http._get_curl_session", return_value=mock_sess):
            with patch("time.sleep"):
                data = _curl_fetch("https://example.com", None, 30)
                assert data == b"ok"
