# jarvis-web-scraper

A pip-installable web scraping and content extraction library for Jarvis
microservices. It fetches a URL and returns clean, readable text using a
trafilatura-first extraction pipeline with a BeautifulSoup fallback, plus
built-in SSRF hardening (private-host blocking with DNS resolution and
per-redirect-hop revalidation).

## Install

```bash
pip install -e .

# with dev/test extras
pip install -e ".[dev]"
```

Requires Python 3.10+.

## Usage

```python
import asyncio
from jarvis_web_scraper import WebScraper, FetchConfig

async def main():
    scraper = WebScraper()

    # Fetch and extract a single page
    page = await scraper.fetch_and_extract("https://example.com")
    print(page.title, page.word_count, page.ok)
    print(page.text_content)

    # Fetch several pages concurrently
    pages = await scraper.batch_fetch(
        ["https://a.com", "https://b.com", "https://c.com"],
        max_concurrent=3,
        max_chars=8000,
    )

    # Custom configuration
    scraper = WebScraper(config=FetchConfig(timeout=30.0))

asyncio.run(main())
```

`fetch_and_extract` returns a `ScrapedPage` with `url`, `title`,
`text_content`, `word_count`, `fetch_time_ms`, `error`, and the convenience
property `ok` (true when `error is None`).

## How it works

1. Fetch the URL with browser-like headers (httpx, async).
2. On 401/403, retry with a relaxed `Accept` header.
3. On persistent block or timeout, fall back to the `r.jina.ai` reader proxy.
4. Extract main content with trafilatura; fall back to BeautifulSoup when the
   trafilatura result is too thin.

Private/internal hosts are blocked by default (`FetchConfig(block_private_hosts=True)`).
Hostnames are resolved via DNS and every redirect hop is re-validated, so a
public name that resolves to a private address is still rejected.

## Testing

```bash
pytest -v
pytest --cov=jarvis_web_scraper --cov-report=term-missing
```

## License

Apache License, Version 2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
