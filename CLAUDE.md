# jarvis-web-scraper

Pip-installable web scraping and content extraction library for jarvis microservices.

## Usage

```python
from jarvis_web_scraper import WebScraper, FetchConfig

scraper = WebScraper()
page = await scraper.fetch_and_extract("https://example.com")
pages = await scraper.batch_fetch(["https://a.com", "https://b.com"])
```

## Architecture

- `client.py` — `WebScraper` class (main entrypoint)
- `fetcher.py` — Async HTTP fetching (adapted from recipes html_fetcher.py)
- `extractor.py` — Content extraction (trafilatura + BeautifulSoup fallback)
- `models.py` — `ScrapedPage`, `FetchConfig` dataclasses

## Testing

```bash
pip install -e ".[dev]"
pytest
```

## Dependencies

- `httpx` — Async HTTP client
- `trafilatura` — Main content extraction
- `beautifulsoup4` + `lxml` — Fallback extraction
