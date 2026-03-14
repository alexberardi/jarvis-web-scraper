"""Tests for WebScraper client."""

from unittest.mock import AsyncMock, patch

import pytest

from jarvis_web_scraper import WebScraper, ScrapedPage, FetchConfig

SAMPLE_HTML = """<!DOCTYPE html>
<html>
<head><title>Test Page</title></head>
<body>
<nav>Navigation links here</nav>
<article>
    <h1>Main Article Title</h1>
    <p>This is the first paragraph of the article with enough content to be meaningful.
    It contains several sentences that describe the topic in detail.</p>
    <p>This is the second paragraph with additional information about the topic.
    More details are provided here for comprehensive coverage.</p>
</article>
<footer>Footer content here</footer>
</body>
</html>"""


class TestWebScraper:
    @pytest.mark.asyncio
    async def test_fetch_and_extract_success(self) -> None:
        scraper = WebScraper()
        with patch(
            "jarvis_web_scraper.client.fetch_html",
            new_callable=AsyncMock,
            return_value=(SAMPLE_HTML, 100),
        ):
            page = await scraper.fetch_and_extract("https://example.com")

        assert page.ok
        assert page.url == "https://example.com"
        assert page.title == "Test Page"
        assert page.word_count > 0
        assert page.error is None
        assert "first paragraph" in page.text_content

    @pytest.mark.asyncio
    async def test_fetch_and_extract_error(self) -> None:
        scraper = WebScraper()
        with patch(
            "jarvis_web_scraper.client.fetch_html",
            new_callable=AsyncMock,
            side_effect=ValueError("Connection failed"),
        ):
            page = await scraper.fetch_and_extract("https://example.com")

        assert not page.ok
        assert page.error == "Connection failed"
        assert page.text_content == ""
        assert page.word_count == 0

    @pytest.mark.asyncio
    async def test_batch_fetch(self) -> None:
        scraper = WebScraper()
        urls = ["https://a.com", "https://b.com", "https://c.com"]

        with patch(
            "jarvis_web_scraper.client.fetch_html",
            new_callable=AsyncMock,
            return_value=(SAMPLE_HTML, 50),
        ):
            pages = await scraper.batch_fetch(urls, max_concurrent=2)

        assert len(pages) == 3
        assert all(p.ok for p in pages)

    @pytest.mark.asyncio
    async def test_batch_fetch_partial_failure(self) -> None:
        scraper = WebScraper()
        call_count = 0

        async def _mock_fetch(url, config=None):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise ValueError("Timeout")
            return (SAMPLE_HTML, 50)

        with patch("jarvis_web_scraper.client.fetch_html", side_effect=_mock_fetch):
            pages = await scraper.batch_fetch(["https://a.com", "https://b.com"])

        assert len(pages) == 2
        assert pages[0].ok
        assert not pages[1].ok

    @pytest.mark.asyncio
    async def test_respects_max_chars(self) -> None:
        scraper = WebScraper()
        with patch(
            "jarvis_web_scraper.client.fetch_html",
            new_callable=AsyncMock,
            return_value=(SAMPLE_HTML, 50),
        ):
            page = await scraper.fetch_and_extract("https://example.com", max_chars=100)

        assert len(page.text_content) <= 100

    def test_custom_config(self) -> None:
        config = FetchConfig(timeout=30.0, block_private_hosts=False)
        scraper = WebScraper(config=config)
        assert scraper._config.timeout == 30.0
        assert scraper._config.block_private_hosts is False
