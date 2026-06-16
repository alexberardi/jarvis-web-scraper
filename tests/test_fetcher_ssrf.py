"""SSRF-hardening tests for the fetcher.

Covers the three gaps the hardening closes:
  1. Redirect bypass — every 3xx hop is re-validated, not just the initial URL.
  2. No DNS resolution — hostnames are resolved and judged by their addresses.
  3. IPv6 parsing + range coverage — ``::1``, IPv4-mapped, link-local, reserved
     (NAT64), CGNAT, unspecified are all blocked.

Tests are hermetic: literal IPs need no DNS, and the few name-resolution cases
monkeypatch ``socket.getaddrinfo`` (which the async path reaches via the event
loop's default executor-backed ``getaddrinfo``).
"""

import socket

import httpx
import pytest

from jarvis_web_scraper.fetcher import (
    _fetch_following_redirects,
    _host_blocked,
    _ip_blocked,
    is_private_host,
)

import ipaddress


def _gai(ip: str) -> list:
    """A getaddrinfo-shaped result for a single address."""
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0))]


class _StubClient:
    """Minimal httpx.AsyncClient stand-in for the redirect walk.

    Returns scripted responses in order and records (url, headers) per GET.
    The walk validates the host BEFORE calling get(), so a blocked hop never
    consumes a scripted response.
    """

    def __init__(self, responses: list[httpx.Response]) -> None:
        self._responses = list(responses)
        self.calls: list[tuple[str, dict[str, str]]] = []

    async def get(self, url: str, headers: dict[str, str] | None = None) -> httpx.Response:
        self.calls.append((url, dict(headers or {})))
        if not self._responses:
            raise AssertionError(f"unexpected GET {url}")
        return self._responses.pop(0)


def _redirect(location: str, status: int = 302) -> httpx.Response:
    return httpx.Response(status, headers={"location": location})


def _ok() -> httpx.Response:
    return httpx.Response(200, headers={"content-type": "text/html"}, content=b"<html></html>")


# --------------------------------------------------------------------------- #
# Blocklist coverage (is_private_host / _ip_blocked) — literal IPs, no DNS.
# --------------------------------------------------------------------------- #

BLOCKED_LITERALS = [
    "127.0.0.1",            # loopback
    "10.0.0.1",             # private
    "192.168.1.1",          # private
    "169.254.169.254",      # link-local / cloud metadata
    "::1",                  # IPv6 loopback (the original split(':') bypass)
    "::ffff:127.0.0.1",     # IPv4-mapped loopback (is_private, NOT is_loopback)
    "fe80::1",              # IPv6 link-local
    "64:ff9b::7f00:1",      # NAT64 well-known prefix (is_reserved canary)
    "100.64.1.2",           # CGNAT 100.64/10 (only `not is_global` catches it)
    "0.0.0.0",              # unspecified
    "::",                   # IPv6 unspecified
]

ALLOWED_LITERALS = [
    "8.8.8.8",
    "1.1.1.1",
    "2606:4700:4700::1111",  # Cloudflare DNS (global unicast)
]


@pytest.mark.parametrize("host", BLOCKED_LITERALS)
def test_blocked_ip_literals(host: str) -> None:
    assert is_private_host(host) is True


@pytest.mark.parametrize("host", ALLOWED_LITERALS)
def test_allowed_ip_literals(host: str) -> None:
    assert is_private_host(host) is False


def test_ipv6_loopback_no_longer_bypasses() -> None:
    # Regression: the old `host.split(":")[0]` mangled "::1" -> "" and let it pass.
    assert is_private_host("::1") is True
    assert _ip_blocked(ipaddress.ip_address("::1")) is True


def test_ipv4_mapped_caught_via_is_private_not_is_loopback() -> None:
    # Guards against a future "simplify _ip_blocked to is_loopback" regression.
    mapped = ipaddress.ip_address("::ffff:127.0.0.1")
    assert mapped.is_loopback is False  # would slip past an is_loopback-only check
    assert _ip_blocked(mapped) is True


def test_nat64_reserved_canary() -> None:
    # The only blocked literal that relies on the is_reserved term.
    assert _ip_blocked(ipaddress.ip_address("64:ff9b::7f00:1")) is True


def test_cgnat_blocked_by_not_is_global() -> None:
    # 100.64/10 has every special-use flag False; only `not is_global` catches it.
    assert is_private_host("100.64.1.2") is True


@pytest.mark.parametrize("host", ["localhost", "localhost.", "LOCALHOST"])
def test_localhost_variants_blocked(host: str) -> None:
    assert is_private_host(host) is True


def test_empty_host_blocked() -> None:
    assert is_private_host("") is True


# --------------------------------------------------------------------------- #
# DNS resolution (gap 2) — hostnames judged by their resolved addresses.
# --------------------------------------------------------------------------- #


def test_hostname_resolving_private_is_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: _gai("127.0.0.1"))
    assert is_private_host("evil.example") is True


def test_hostname_resolving_public_is_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: _gai("93.184.216.34"))
    assert is_private_host("good.example") is False


def test_decimal_ip_normalized_and_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    # http://2130706433/ == 127.0.0.1; not a literal IP, blocked via getaddrinfo.
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: _gai("127.0.0.1"))
    assert is_private_host("2130706433") is True


def test_unresolvable_host_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*a, **k):
        raise socket.gaierror("nope")

    monkeypatch.setattr(socket, "getaddrinfo", _boom)
    assert is_private_host("does-not-resolve.invalid") is True


def test_split_horizon_blocks_if_any_address_private(monkeypatch: pytest.MonkeyPatch) -> None:
    # One public + one private A record -> blocked (ANY bad address blocks).
    infos = _gai("93.184.216.34") + _gai("127.0.0.1")
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: infos)
    assert is_private_host("rebind.example") is True


async def test_host_blocked_async_matches_sync(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: _gai("127.0.0.1"))
    assert await _host_blocked("internal.example") is True
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: _gai("93.184.216.34"))
    assert await _host_blocked("public.example") is False
    assert await _host_blocked("169.254.169.254") is True  # literal, no DNS


# --------------------------------------------------------------------------- #
# Redirect walk (gap 1) — every hop re-validated.
# --------------------------------------------------------------------------- #

_START = "http://93.184.216.34/"  # literal public IP: no DNS needed for hop 1


async def _walk(client: _StubClient, url: str = _START, *, max_redirects: int = 5, block: bool = True):
    return await _fetch_following_redirects(
        client, url, {"User-Agent": "x"}, max_redirects=max_redirects, block=block
    )


async def test_redirect_to_loopback_literal_rejected() -> None:
    client = _StubClient([_redirect("http://127.0.0.1/")])
    with pytest.raises(ValueError, match="private or disallowed"):
        await _walk(client)
    assert len(client.calls) == 1  # private hop never fetched


async def test_redirect_to_metadata_rejected() -> None:
    client = _StubClient([_redirect("http://169.254.169.254/latest/meta-data/")])
    with pytest.raises(ValueError, match="private or disallowed"):
        await _walk(client)


async def test_redirect_to_ipv6_loopback_rejected() -> None:
    client = _StubClient([_redirect("http://[::1]/")])
    with pytest.raises(ValueError, match="private or disallowed"):
        await _walk(client)


async def test_redirect_to_resolving_private_hostname_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda host, *a, **k: _gai("127.0.0.1") if "evil" in host else _gai("93.184.216.34"),
    )
    client = _StubClient([_redirect("http://evil.example/")])
    with pytest.raises(ValueError, match="private or disallowed"):
        await _walk(client)


async def test_redirect_file_scheme_rejected() -> None:
    client = _StubClient([_redirect("file:///etc/passwd")])
    with pytest.raises(ValueError, match="Invalid redirect target"):
        await _walk(client)


async def test_redirect_gopher_scheme_rejected() -> None:
    client = _StubClient([_redirect("gopher://127.0.0.1:6379/")])
    with pytest.raises(ValueError, match="Invalid redirect target"):
        await _walk(client)


async def test_protocol_relative_redirect_to_metadata_rejected() -> None:
    # "//169.254.169.254/" inherits the current scheme via URL.join -> http://...
    client = _StubClient([_redirect("//169.254.169.254/")])
    with pytest.raises(ValueError, match="private or disallowed"):
        await _walk(client)


async def test_relative_redirect_on_public_host_followed() -> None:
    client = _StubClient([_redirect("/page2"), _ok()])
    resp = await _walk(client)
    assert resp.status_code == 200
    assert [c[0] for c in client.calls] == [_START, "http://93.184.216.34/page2"]


async def test_normal_public_chain_succeeds() -> None:
    client = _StubClient([_redirect("http://1.1.1.1/"), _ok()])
    resp = await _walk(client)
    assert resp.status_code == 200
    assert len(client.calls) == 2


async def test_max_redirects_boundary_allows_exactly_n() -> None:
    client = _StubClient([_redirect("/1"), _redirect("/2"), _ok()])
    resp = await _walk(client, max_redirects=2)
    assert resp.status_code == 200


async def test_max_redirects_boundary_rejects_n_plus_one() -> None:
    client = _StubClient([_redirect("/1"), _redirect("/2"), _redirect("/3")])
    with pytest.raises(ValueError, match="Too many redirects"):
        await _walk(client, max_redirects=2)


async def test_non_redirect_3xx_with_location_is_not_followed() -> None:
    # 304/300 carry no follow semantics even with a Location header.
    client = _StubClient([_redirect("http://127.0.0.1/", status=304)])
    resp = await _walk(client)
    assert resp.status_code == 304  # returned as-is, private target never fetched
    assert len(client.calls) == 1


async def test_block_false_follows_private_redirect() -> None:
    # Explicit opt-out (block_private_hosts=False) must still bypass the gate.
    client = _StubClient([_redirect("http://127.0.0.1/"), _ok()])
    resp = await _walk(client, block=False)
    assert resp.status_code == 200
    assert len(client.calls) == 2


async def test_cross_origin_redirect_strips_sensitive_headers() -> None:
    client = _StubClient([_redirect("http://1.1.1.1/"), _ok()])
    await _fetch_following_redirects(
        client,
        _START,
        {"User-Agent": "x", "Authorization": "Bearer secret", "Cookie": "sid=1"},
        max_redirects=5,
        block=True,
    )
    first_hop_headers = client.calls[0][1]
    second_hop_headers = client.calls[1][1]
    assert first_hop_headers.get("Authorization") == "Bearer secret"
    assert "Authorization" not in second_hop_headers
    assert "Cookie" not in second_hop_headers


async def test_same_origin_redirect_keeps_headers() -> None:
    client = _StubClient([_redirect("/page2"), _ok()])
    await _fetch_following_redirects(
        client,
        _START,
        {"User-Agent": "x", "Authorization": "Bearer secret"},
        max_redirects=5,
        block=True,
    )
    assert client.calls[1][1].get("Authorization") == "Bearer secret"
