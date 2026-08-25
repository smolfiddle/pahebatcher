"""Tests for TLS context."""

from __future__ import annotations

import ssl

from pahebatcher.tls import make_ssl_ctx


class TestTLS:
    def test_minimum_version(self) -> None:
        ctx = make_ssl_ctx()
        assert ctx.minimum_version == ssl.TLSVersion.TLSv1_2

    def test_verify_mode(self) -> None:
        ctx = make_ssl_ctx()
        assert ctx.verify_mode == ssl.CERT_REQUIRED
        assert ctx.check_hostname is True

    def test_no_compression(self) -> None:
        ctx = make_ssl_ctx()
        assert ctx.options & ssl.OP_NO_COMPRESSION

    def test_ciphers_set(self) -> None:
        ctx = make_ssl_ctx()
        # get_ciphers returns list, check not empty
        assert len(ctx.get_ciphers()) > 0
