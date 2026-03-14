# jarvis-web-scraper

Pip-installable web scraping and content extraction library for jarvis microservices. Extracted from `jarvis-recipes-server`'s battle-tested `html_fetcher.py`, with a trafilatura + BeautifulSoup content extraction layer.

## Quick Reference

```bash
# Setup
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"

# Test
.venv/bin/pytest -v
.venv/bin/pytest --cov=jarvis_web_scraper --cov-report=term-missing
```

## Usage

```python
from jarvis_web_scraper import WebScraper, FetchConfig, ScrapedPage

# Single page
scraper = WebScraper()
page = await scraper.fetch_and_extract("https://example.com")
print(page.title, page.word_count, page.ok)

# Batch (concurrent with semaphore)
pages = await scraper.batch_fetch(
    ["https://a.com", "https://b.com", "https://c.com"],
    max_concurrent=3,
    max_chars=8000,
)

# Custom config
scraper = WebScraper(config=FetchConfig(
    timeout=30.0,
    block_private_hosts=False,
))
```

## Architecture

```
jarvis_web_scraper/
├── __init__.py       # Public API: WebScraper, ScrapedPage, FetchConfig
├── client.py         # WebScraper class (main entrypoint)
├── fetcher.py        # Async HTTP fetching (httpx, encoding detection, proxy fallback)
├── extractor.py      # Content extraction (trafilatura → BeautifulSoup fallback)
└── models.py         # ScrapedPage, FetchConfig dataclasses
```

### Extraction Pipeline

```
URL → fetch_html() → raw HTML
  → trafilatura.extract() → text (if sufficient)
  → BeautifulSoup fallback → text (article/main/content selectors)
  → ScrapedPage(title, text_content, word_count, fetch_time_ms)
```

### Fetch Resilience

1. Direct GET with browser-like headers
2. On 401/403: retry with `Accept: */*`
3. On 401/403 retry fail: proxy through `r.jina.ai`
4. On timeout/connection error: proxy through `r.jina.ai`
5. Encoding detection: Content-Type charset → meta charset → UTF-8 fallback
6. Content validation: HTML tag detection, printable ratio, control char ratio

## Models

**`ScrapedPage`** — Result of fetching + extracting:
- `url: str` — Source URL
- `title: str | None` — Page title
- `text_content: str` — Extracted text
- `word_count: int` — Word count
- `fetch_time_ms: int` — Total time
- `error: str | None` — Error message if failed
- `ok: bool` — Property: `error is None`

**`FetchConfig`** — Fetch configuration:
- `timeout: float` — Request timeout (default: 15s)
- `user_agent: str` — Browser user agent
- `max_redirects: int` — Max redirects (default: 5)
- `block_private_hosts: bool` — Block localhost/private IPs (default: True)
- `headers: dict` — Extra headers

## Dependencies

**Runtime:**
- `httpx` — Async HTTP client
- `trafilatura` — Main content extraction engine
- `beautifulsoup4` + `lxml` — Fallback extraction

**Dev:**
- `pytest`, `pytest-asyncio`, `pytest-cov`

## Used By

- `jarvis-command-center` — Deep research tool (batch scrape search results for LLM summarization)
- `jarvis-recipes-server` — Potential future migration from inline html_fetcher.py

## Testing

```bash
.venv/bin/pytest -v --tb=short
```

27 tests covering:
- URL validation and private host blocking
- Encoding detection and fallback
- Content extraction (article, nav/footer stripping, scripts)
- Batch fetch with partial failures
- Max chars truncation
- Custom config

## CI

- **GitHub Actions**: `.github/workflows/test.yml` — pytest on push/PR to main, 70% coverage threshold
- **No Docker build** — this is a library, not a service
