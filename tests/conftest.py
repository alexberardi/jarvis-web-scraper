"""Test fixtures for jarvis-web-scraper."""

import pytest

from jarvis_web_scraper.models import FetchConfig


@pytest.fixture
def fetch_config() -> FetchConfig:
    """Default fetch config for tests."""
    return FetchConfig(timeout=5.0)


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

EMPTY_HTML = ""

NO_CONTENT_HTML = """<!DOCTYPE html>
<html>
<head><title>Empty Page</title></head>
<body>
<nav>Just nav</nav>
<script>console.log('hello');</script>
</body>
</html>"""
