"""Web scraping and content extraction for jarvis microservices.

Usage:
    from jarvis_web_scraper import WebScraper

    scraper = WebScraper()
    page = await scraper.fetch_and_extract("https://example.com")
    pages = await scraper.batch_fetch(["https://a.com", "https://b.com"])
"""

from jarvis_web_scraper.client import WebScraper
from jarvis_web_scraper.models import FetchConfig, ScrapedPage

__all__ = [
    "WebScraper",
    "ScrapedPage",
    "FetchConfig",
]
__version__ = "0.1.0"
