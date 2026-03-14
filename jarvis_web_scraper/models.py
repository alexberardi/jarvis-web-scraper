"""Data models for web scraper."""

from dataclasses import dataclass, field


@dataclass
class FetchConfig:
    """Configuration for HTTP fetching."""

    timeout: float = 15.0
    user_agent: str = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
    max_redirects: int = 5
    block_private_hosts: bool = True
    headers: dict[str, str] = field(default_factory=dict)


@dataclass
class ScrapedPage:
    """Result of fetching and extracting content from a URL."""

    url: str
    title: str | None
    text_content: str
    word_count: int
    fetch_time_ms: int
    error: str | None = None

    @property
    def ok(self) -> bool:
        """Whether the page was successfully fetched and extracted."""
        return self.error is None
