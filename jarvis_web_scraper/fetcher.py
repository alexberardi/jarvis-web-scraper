"""Async HTTP fetching with encoding handling and fallbacks.

Adapted from jarvis-recipes-server html_fetcher.py.
"""

import asyncio
import ipaddress
import logging
import re
import socket
import time
from urllib.parse import urlparse

import httpx

from jarvis_web_scraper.models import FetchConfig

logger = logging.getLogger(__name__)

# 3xx codes that carry a Location and should be followed (mirrors httpx's
# Response.has_redirect_location; deliberately excludes 300/304/305/306).
_REDIRECT_CODES = {301, 302, 303, 307, 308}
# Request headers that must not be replayed to a different origin on redirect.
_SENSITIVE_HEADERS = {"authorization", "cookie", "proxy-authorization"}


def _ip_blocked(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """True if ``ip`` is in any range unsafe for outbound scraping (SSRF guard).

    Covers loopback, private, link-local (169.254/16, fe80::/10), reserved
    (incl. the NAT64 well-known prefix), multicast, unspecified (0.0.0.0, ::),
    and — via ``not is_global`` — CGNAT 100.64/10 and other non-global ranges.
    IPv4-mapped IPv6 (``::ffff:a.b.c.d``) is evaluated as its embedded IPv4.
    """
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        ip = mapped
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
        or not ip.is_global
    )


def _pre_dns_verdict(host: str) -> bool | None:
    """Synchronous checks that need no DNS.

    Returns a definite verdict, or ``None`` if ``host`` is a name that must be
    resolved before judging.
    """
    if not host:
        return True
    try:
        return _ip_blocked(ipaddress.ip_address(host))
    except ValueError:
        pass
    if host.lower() in {"localhost", "localhost."}:
        return True
    return None


def _resolved_blocked(addrinfos: list) -> bool:
    """True if ANY resolved address is unsafe (defeats split-horizon DNS)."""
    for info in addrinfos:
        try:
            if _ip_blocked(ipaddress.ip_address(info[4][0])):
                return True
        except ValueError:
            return True
    return False


def is_private_host(host: str) -> bool:
    """True if ``host`` is, or DNS-resolves to, a private/disallowed address.

    ``host`` must be a bare hostname or IP — pass ``urlparse().hostname``, which
    strips brackets and the port. Do NOT split on ':' (it mangles IPv6). Fails
    closed: an unresolvable host is treated as private.

    This is the synchronous form (blocking ``getaddrinfo``); the fetch path uses
    the async :func:`_host_blocked` so DNS never stalls the event loop.
    """
    verdict = _pre_dns_verdict(host)
    if verdict is not None:
        return verdict
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return True
    return _resolved_blocked(infos)


async def _host_blocked(host: str) -> bool:
    """Async form of :func:`is_private_host` — resolves DNS off the event loop."""
    verdict = _pre_dns_verdict(host)
    if verdict is not None:
        return verdict
    try:
        loop = asyncio.get_running_loop()
        infos = await loop.getaddrinfo(host, None)
    except socket.gaierror:
        return True
    return _resolved_blocked(infos)


def _strip_cross_origin(
    headers: dict[str, str], current: str, nxt: str
) -> dict[str, str]:
    """Drop sensitive headers when a redirect crosses to a different origin
    (scheme/host/port), mirroring httpx's automatic stripping."""

    def origin(u: str) -> tuple[str, str | None, int | None]:
        p = urlparse(u)
        return (p.scheme, p.hostname, p.port)

    if origin(current) == origin(nxt):
        return headers
    return {k: v for k, v in headers.items() if k.lower() not in _SENSITIVE_HEADERS}


async def _fetch_following_redirects(
    client: httpx.AsyncClient,
    url: str,
    headers: dict[str, str],
    *,
    max_redirects: int,
    block: bool,
) -> httpx.Response:
    """GET ``url``, following redirects manually and re-validating every hop.

    Raises ``ValueError`` if any hop is a private/disallowed host, targets a
    non-http(s) scheme, or the chain exceeds ``max_redirects``. The client must
    be created with ``follow_redirects=False``.
    """
    current = url
    current_headers = dict(headers)
    for _ in range(max_redirects + 1):
        parsed = urlparse(current)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("Invalid redirect target")
        if block and await _host_blocked(parsed.hostname):
            raise ValueError("Redirect points to a private or disallowed host")
        response = await client.get(current, headers=current_headers)
        if response.status_code in _REDIRECT_CODES and "location" in response.headers:
            next_url = str(httpx.URL(current).join(response.headers["location"]))
            current_headers = _strip_cross_origin(current_headers, current, next_url)
            current = next_url
            continue
        return response
    raise ValueError("Too many redirects")


async def fetch_html(url: str, config: FetchConfig | None = None) -> tuple[str, int]:
    """Fetch HTML content from a URL.

    Returns:
        Tuple of (html_content, fetch_time_ms).

    Raises:
        ValueError: If URL is invalid, host is private, or content can't be decoded.
        httpx.HTTPStatusError: If the server returns an error status.
    """
    config = config or FetchConfig()
    start_ms = int(time.monotonic() * 1000)

    parsed_url = urlparse(url)
    if parsed_url.scheme not in {"http", "https"} or not parsed_url.hostname:
        raise ValueError("Invalid URL: must start with http or https")
    # DNS-resolving check up front: this also guarantees the r.jina.ai fallback
    # below never proxies a host that resolves to an internal/private target.
    if config.block_private_hosts and await _host_blocked(parsed_url.hostname):
        raise ValueError("URL points to a private or disallowed host")

    headers = {
        "User-Agent": config.user_agent,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Referer": "https://www.google.com/",
        "Connection": "keep-alive",
        **config.headers,
    }
    timeout = httpx.Timeout(config.timeout, read=config.timeout, connect=5.0)

    async def _try_fetch(
        target_url: str, extra_headers: dict[str, str] | None = None
    ) -> httpx.Response:
        # follow_redirects=False: we walk the chain ourselves so each 3xx hop's
        # host is re-validated (a single up-front check is bypassable by a 3xx).
        async with httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=False,
        ) as client:
            return await _fetch_following_redirects(
                client,
                target_url,
                headers | (extra_headers or {}),
                max_redirects=config.max_redirects,
                block=config.block_private_hosts,
            )

    try:
        response = await _try_fetch(url)
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code in {401, 403}:
            try:
                response = await _try_fetch(url, {"Accept": "*/*"})
                response.raise_for_status()
            except httpx.HTTPStatusError:
                # r.jina.ai is a third-party reader proxy — only egress to it
                # when the caller has explicitly opted in. Off (default) ->
                # fail closed by re-raising the original status error.
                if not config.enable_jina_fallback:
                    raise
                proxy_url = f"https://r.jina.ai/{url}"
                response = await _try_fetch(proxy_url, {"Accept": "text/plain"})
                response.raise_for_status()
        else:
            raise
    except (httpx.RequestError, httpx.TimeoutException):
        # Same opt-in gate for the connect/timeout fallback. Off -> re-raise.
        if not config.enable_jina_fallback:
            raise
        proxy_url = f"https://r.jina.ai/{url}"
        response = await _try_fetch(proxy_url, {"Accept": "text/plain"})
        response.raise_for_status()

    content_type = response.headers.get("content-type", "")
    if "text/html" not in content_type and "text/plain" not in content_type:
        raise ValueError(f"Unsupported content type: {content_type}")

    text = _decode_response(response, url)
    elapsed_ms = int(time.monotonic() * 1000) - start_ms
    return text, elapsed_ms


def _decode_response(response: httpx.Response, url: str) -> str:
    """Decode HTTP response content with encoding detection and fallbacks."""
    content_type = response.headers.get("content-type", "")
    content_bytes = response.content

    # Try charset from Content-Type header
    encoding = None
    if "charset=" in content_type.lower():
        try:
            encoding = content_type.split("charset=")[1].split(";")[0].strip().strip("\"'")
        except (IndexError, AttributeError):
            pass

    if not encoding:
        encoding = "utf-8"

    try:
        text = content_bytes.decode(encoding)
    except (UnicodeDecodeError, LookupError):
        try:
            text = content_bytes.decode("utf-8", errors="replace")
            # Check for meta charset declaration
            encoding_match = re.search(
                r'<meta[^>]+charset=["\']?([^"\'>\s]+)', text, re.I
            )
            if encoding_match:
                detected = encoding_match.group(1).lower()
                if detected and detected != "utf-8":
                    try:
                        text = content_bytes.decode(detected)
                    except (UnicodeDecodeError, LookupError):
                        pass
        except (UnicodeDecodeError, LookupError):
            text = response.text

    # Validate content
    if text and len(text) > 100:
        sample = text[:2000]
        has_html_tags = bool(re.search(r"<[a-z]+[^>]*>", sample, re.I))
        printable_count = sum(1 for c in sample if (32 <= ord(c) <= 126) or c.isspace())
        printable_ratio = printable_count / len(sample) if sample else 0
        control_chars = sum(1 for c in sample if ord(c) < 32 and c not in "\n\r\t")
        control_ratio = control_chars / len(sample) if sample else 0

        if has_html_tags and printable_ratio > 0.6 and control_ratio < 0.1:
            return text
        else:
            logger.warning(
                "Content validation failed for %s: tags=%s, printable=%.2f, control=%.2f",
                url, has_html_tags, printable_ratio, control_ratio,
            )

    # Final fallback
    text_fallback = response.text
    if text_fallback and len(text_fallback) > 100:
        has_html = bool(re.search(r"<[a-z]+[^>]*>", text_fallback[:2000], re.I))
        if has_html:
            return text_fallback

    if text and len(text) > 0:
        return text

    raise ValueError("Unable to decode content with valid encoding")
