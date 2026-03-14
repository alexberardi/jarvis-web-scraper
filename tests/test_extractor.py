"""Tests for content extraction."""

from jarvis_web_scraper.extractor import extract_content, _extract_title

import pytest

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

MINIMAL_HTML = """<html><body><p>Short</p></body></html>"""

NO_CONTENT_HTML = """<!DOCTYPE html>
<html>
<head><title>Empty Page</title></head>
<body>
<nav>Just nav</nav>
<script>console.log('hello');</script>
</body>
</html>"""


class TestExtractTitle:
    def test_extracts_title(self) -> None:
        title = _extract_title(SAMPLE_HTML)
        assert title == "Test Page"

    def test_no_title(self) -> None:
        title = _extract_title("<html><body>No title</body></html>")
        assert title is None

    def test_empty_html(self) -> None:
        title = _extract_title("")
        assert title is None


class TestExtractContent:
    def test_extracts_article_content(self) -> None:
        title, text = extract_content(SAMPLE_HTML, "https://example.com")
        assert title == "Test Page"
        assert "first paragraph" in text
        assert "second paragraph" in text

    def test_strips_nav_and_footer(self) -> None:
        _, text = extract_content(SAMPLE_HTML, "https://example.com")
        assert "Navigation links" not in text
        assert "Footer content" not in text

    def test_respects_max_chars(self) -> None:
        _, text = extract_content(SAMPLE_HTML, "https://example.com", max_chars=50)
        assert len(text) <= 50

    def test_handles_minimal_html(self) -> None:
        title, text = extract_content(MINIMAL_HTML, "https://example.com")
        assert title is None
        # Should return something even if minimal
        assert isinstance(text, str)

    def test_handles_no_content(self) -> None:
        title, text = extract_content(NO_CONTENT_HTML, "https://example.com")
        assert title == "Empty Page"
        assert isinstance(text, str)

    def test_strips_scripts(self) -> None:
        _, text = extract_content(NO_CONTENT_HTML, "https://example.com")
        assert "console.log" not in text
