"""Web scraper client — main entrypoint."""

import asyncio
import logging
import time

from jarvis_web_scraper.extractor import extract_content
from jarvis_web_scraper.fetcher import fetch_html
from jarvis_web_scraper.models import FetchConfig, ScrapedPage

logger = logging.getLogger(__name__)


class WebScraper:
    """Async web scraper with content extraction."""

    def __init__(self, config: FetchConfig | None = None) -> None:
        self._config = config or FetchConfig()

    async def fetch_and_extract(self, url: str, max_chars: int = 8000) -> ScrapedPage:
        """Fetch a URL and extract its main text content.

        Args:
            url: The URL to scrape.
            max_chars: Maximum characters of extracted text to return.

        Returns:
            ScrapedPage with extracted content or error details.
        """
        start_ms = int(time.monotonic() * 1000)
        try:
            html, fetch_time_ms = await fetch_html(url, self._config)
            title, text = extract_content(html, url, max_chars=max_chars)
            elapsed_ms = int(time.monotonic() * 1000) - start_ms

            return ScrapedPage(
                url=url,
                title=title,
                text_content=text,
                word_count=len(text.split()) if text else 0,
                fetch_time_ms=elapsed_ms,
            )
        except Exception as e:
            elapsed_ms = int(time.monotonic() * 1000) - start_ms
            logger.warning("Failed to scrape %s: %s", url, e)
            return ScrapedPage(
                url=url,
                title=None,
                text_content="",
                word_count=0,
                fetch_time_ms=elapsed_ms,
                error=str(e),
            )

    async def batch_fetch(
        self,
        urls: list[str],
        max_concurrent: int = 3,
        max_chars: int = 8000,
    ) -> list[ScrapedPage]:
        """Fetch and extract multiple URLs concurrently.

        Args:
            urls: List of URLs to scrape.
            max_concurrent: Maximum concurrent requests.
            max_chars: Maximum characters per page.

        Returns:
            List of ScrapedPage results (same order as input URLs).
        """
        semaphore = asyncio.Semaphore(max_concurrent)

        async def _limited_fetch(url: str) -> ScrapedPage:
            async with semaphore:
                return await self.fetch_and_extract(url, max_chars=max_chars)

        tasks = [_limited_fetch(url) for url in urls]
        return list(await asyncio.gather(*tasks))
