"""Content extraction from HTML using trafilatura with BeautifulSoup fallback."""

import logging
import re

from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)


def extract_content(html: str, url: str, max_chars: int = 8000) -> tuple[str | None, str]:
    """Extract main text content from HTML.

    Args:
        html: Raw HTML string.
        url: Source URL (for logging).
        max_chars: Maximum characters to return.

    Returns:
        Tuple of (title, text_content).
    """
    title = _extract_title(html)

    # Try trafilatura first (best quality extraction)
    text = _extract_with_trafilatura(html)
    if text and len(text.strip()) > 50:
        return title, text[:max_chars]

    # Fallback to BeautifulSoup
    logger.debug("Trafilatura extraction insufficient for %s, trying BeautifulSoup", url)
    text = _extract_with_beautifulsoup(html)
    if text and len(text.strip()) > 50:
        return title, text[:max_chars]

    # Last resort: return whatever we got
    result = text or ""
    return title, result[:max_chars]


def _extract_title(html: str) -> str | None:
    """Extract page title from HTML."""
    try:
        soup = BeautifulSoup(html[:5000], "lxml")
        if soup.title and soup.title.string:
            return soup.title.string.strip()
    except Exception:
        pass
    return None


def _extract_with_trafilatura(html: str) -> str | None:
    """Extract content using trafilatura."""
    try:
        import trafilatura

        result = trafilatura.extract(
            html,
            include_comments=False,
            include_tables=True,
            no_fallback=False,
            favor_precision=False,
            favor_recall=True,
        )
        return result
    except Exception as e:
        logger.debug("Trafilatura extraction failed: %s", e)
        return None


def _extract_with_beautifulsoup(html: str) -> str | None:
    """Extract content using BeautifulSoup (fallback)."""
    try:
        soup = BeautifulSoup(html, "lxml")

        # Remove non-content elements
        for tag in soup.find_all(
            ["script", "style", "nav", "header", "footer", "aside", "noscript", "iframe"]
        ):
            tag.decompose()

        # Try article or main content first
        main_content = (
            soup.find("article")
            or soup.find("main")
            or soup.find(attrs={"role": "main"})
            or soup.find(id=re.compile(r"content|article|post|entry", re.I))
            or soup.find(class_=re.compile(r"content|article|post|entry", re.I))
        )

        target = main_content or soup.body or soup
        paragraphs = target.find_all(["p", "li", "h1", "h2", "h3", "h4", "td", "blockquote"])

        texts: list[str] = []
        for p in paragraphs:
            text = p.get_text(separator=" ", strip=True)
            if len(text) > 20:
                texts.append(text)

        if texts:
            return "\n\n".join(texts)

        # Absolute fallback: all visible text
        all_text = target.get_text(separator="\n", strip=True)
        lines = [line.strip() for line in all_text.splitlines() if len(line.strip()) > 10]
        return "\n".join(lines) if lines else None
    except Exception as e:
        logger.debug("BeautifulSoup extraction failed: %s", e)
        return None
