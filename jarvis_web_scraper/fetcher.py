"""Async HTTP fetching with encoding handling and fallbacks.

Adapted from jarvis-recipes-server html_fetcher.py.
"""

import ipaddress
import logging
import re
import time
from urllib.parse import urlparse

import httpx

from jarvis_web_scraper.models import FetchConfig

logger = logging.getLogger(__name__)


def is_private_host(host: str) -> bool:
    """Check if a host is private/localhost."""
    hostname = host.split(":")[0]
    try:
        ip = ipaddress.ip_address(hostname)
        return ip.is_private or ip.is_loopback
    except ValueError:
        return hostname.lower() in {"localhost"}


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
    if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
        raise ValueError("Invalid URL: must start with http or https")
    if config.block_private_hosts and is_private_host(parsed_url.hostname or ""):
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
        merged = headers | (extra_headers or {})
        async with httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
            max_redirects=config.max_redirects,
            headers=merged,
        ) as client:
            return await client.get(target_url)

    try:
        response = await _try_fetch(url)
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code in {401, 403}:
            try:
                response = await _try_fetch(url, {"Accept": "*/*"})
                response.raise_for_status()
            except httpx.HTTPStatusError:
                proxy_url = f"https://r.jina.ai/{url}"
                response = await _try_fetch(proxy_url, {"Accept": "text/plain"})
                response.raise_for_status()
        else:
            raise
    except (httpx.RequestError, httpx.TimeoutException):
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
