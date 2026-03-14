"""Tests for HTTP fetching."""

import pytest

from jarvis_web_scraper.fetcher import is_private_host, fetch_html, _decode_response
from jarvis_web_scraper.models import FetchConfig


class TestIsPrivateHost:
    def test_localhost(self) -> None:
        assert is_private_host("localhost") is True

    def test_loopback_ip(self) -> None:
        assert is_private_host("127.0.0.1") is True

    def test_private_ip(self) -> None:
        assert is_private_host("192.168.1.1") is True
        assert is_private_host("10.0.0.1") is True

    def test_public_ip(self) -> None:
        assert is_private_host("8.8.8.8") is False

    def test_public_hostname(self) -> None:
        assert is_private_host("example.com") is False

    def test_host_with_port(self) -> None:
        assert is_private_host("localhost:8080") is True
        assert is_private_host("example.com:443") is False


class TestFetchHtmlValidation:
    @pytest.mark.asyncio
    async def test_rejects_invalid_scheme(self) -> None:
        with pytest.raises(ValueError, match="Invalid URL"):
            await fetch_html("ftp://example.com")

    @pytest.mark.asyncio
    async def test_rejects_no_host(self) -> None:
        with pytest.raises(ValueError, match="Invalid URL"):
            await fetch_html("not-a-url")

    @pytest.mark.asyncio
    async def test_rejects_private_host(self) -> None:
        config = FetchConfig(block_private_hosts=True)
        with pytest.raises(ValueError, match="private"):
            await fetch_html("http://localhost:8080/page", config)

    @pytest.mark.asyncio
    async def test_allows_private_when_disabled(self) -> None:
        config = FetchConfig(block_private_hosts=False, timeout=2.0)
        # Will fail to connect but shouldn't raise ValueError
        with pytest.raises(Exception) as exc_info:
            await fetch_html("http://127.0.0.1:19999/page", config)
        assert "private" not in str(exc_info.value).lower()


class TestDecodeResponse:
    def test_decodes_utf8(self) -> None:
        import httpx

        response = httpx.Response(
            200,
            content=b"<html><body><p>Hello world</p></body></html>",
            headers={"content-type": "text/html; charset=utf-8"},
        )
        text = _decode_response(response, "https://example.com")
        assert "Hello world" in text

    def test_decodes_without_charset(self) -> None:
        import httpx

        response = httpx.Response(
            200,
            content=b"<html><body><p>Hello world in utf8</p></body></html>",
            headers={"content-type": "text/html"},
        )
        text = _decode_response(response, "https://example.com")
        assert "Hello world" in text
