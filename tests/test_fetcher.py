"""Tests for HTTP fetching."""

import httpx
import pytest

from jarvis_web_scraper import fetcher as fetcher_mod
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

    def test_public_hostname(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import socket

        monkeypatch.setattr(
            socket,
            "getaddrinfo",
            lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))],
        )
        assert is_private_host("example.com") is False

    def test_bare_host_contract(self) -> None:
        # is_private_host now requires a BARE host (callers pass urlparse().hostname,
        # which strips the port). A port-suffixed string is not a valid host: it is
        # unresolvable -> fail-closed -> treated as private. Do NOT split on ':'
        # (that mangled IPv6, the original bug).
        assert is_private_host("localhost:8080") is True
        assert is_private_host("example.com:443") is True


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


class _RecordingClient:
    """httpx.AsyncClient stand-in for fetch_html's redirect walk.

    Records every requested URL and returns scripted responses/errors in order.
    A scripted entry that is an Exception is raised from get(); a Response is
    returned. The redirect walk validates the host BEFORE calling get(), so the
    requested URLs are exactly the hops that were actually egressed.
    """

    def __init__(self, script: list) -> None:
        self._script = list(script)
        self.urls: list[str] = []

    async def __aenter__(self) -> "_RecordingClient":
        return self

    async def __aexit__(self, *exc) -> None:
        return None

    async def get(self, url: str, headers: dict[str, str] | None = None) -> httpx.Response:
        self.urls.append(url)
        if not self._script:
            raise AssertionError(f"unexpected GET {url}")
        item = self._script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _err_403() -> httpx.Response:
    return httpx.Response(403, request=httpx.Request("GET", "http://x/"))


def _jina_ok() -> httpx.Response:
    return httpx.Response(
        200,
        headers={"content-type": "text/plain"},
        content=b"x" * 200,
        request=httpx.Request("GET", "https://r.jina.ai/"),
    )


def _install_client(monkeypatch: pytest.MonkeyPatch, client: _RecordingClient) -> None:
    """Make every httpx.AsyncClient(...) in the fetcher yield `client`, and
    keep DNS hermetic by resolving any hostname (e.g. r.jina.ai) to a public IP.
    """
    monkeypatch.setattr(fetcher_mod.httpx, "AsyncClient", lambda *a, **k: client)
    import socket

    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))],
    )


# Literal public IP for hop 1 keeps the up-front host check DNS-free.
_PUBLIC_URL = "http://93.184.216.34/page"


class TestJinaFallbackOptIn:
    def test_default_is_off(self) -> None:
        # Default-value pin: the third-party reader-proxy fallback is OFF.
        assert FetchConfig().enable_jina_fallback is False

    @pytest.mark.asyncio
    async def test_status_branch_off_no_jina(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Direct + retry both 403 -> with fallback OFF (default), the original
        # HTTPStatusError propagates and r.jina.ai is NEVER requested.
        client = _RecordingClient([_err_403(), _err_403()])
        _install_client(monkeypatch, client)
        with pytest.raises(httpx.HTTPStatusError):
            await fetch_html(_PUBLIC_URL, FetchConfig())
        assert not any(u.startswith("https://r.jina.ai/") for u in client.urls)

    @pytest.mark.asyncio
    async def test_request_error_branch_off_no_jina(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Connect/timeout error -> with fallback OFF, the error propagates and
        # r.jina.ai is NEVER requested.
        boom = httpx.ConnectError("nope", request=httpx.Request("GET", _PUBLIC_URL))
        client = _RecordingClient([boom])
        _install_client(monkeypatch, client)
        with pytest.raises(httpx.RequestError):
            await fetch_html(_PUBLIC_URL, FetchConfig())
        assert not any(u.startswith("https://r.jina.ai/") for u in client.urls)

    @pytest.mark.asyncio
    async def test_status_branch_on_uses_jina(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Direct + retry both 403 -> with fallback ON, the r.jina.ai proxy IS
        # requested for the original URL.
        client = _RecordingClient([_err_403(), _err_403(), _jina_ok()])
        _install_client(monkeypatch, client)
        text, _ = await fetch_html(_PUBLIC_URL, FetchConfig(enable_jina_fallback=True))
        assert f"https://r.jina.ai/{_PUBLIC_URL}" in client.urls

    @pytest.mark.asyncio
    async def test_request_error_branch_on_uses_jina(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Connect error on the direct hop -> with fallback ON, r.jina.ai IS
        # requested for the original URL.
        boom = httpx.ConnectError("nope", request=httpx.Request("GET", _PUBLIC_URL))
        client = _RecordingClient([boom, _jina_ok()])
        _install_client(monkeypatch, client)
        text, _ = await fetch_html(_PUBLIC_URL, FetchConfig(enable_jina_fallback=True))
        assert f"https://r.jina.ai/{_PUBLIC_URL}" in client.urls


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
