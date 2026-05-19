"""
Scrape The Guardian front page via BBC News's daily newspaper roundup.

BBC News publishes individual photos of UK newspaper front pages in daily
articles linked from https://www.bbc.co.uk/news/blogs/the_papers.

Each image is hosted on the BBC's ichef CDN at URLs like:
    https://ichef.bbci.co.uk/ace/standard/1680/cpsprodpb/<hash>/live/<uuid>.jpg

The size segment can be bumped to 2560 for ~2560×3100 images — bigger than
the EPD-42S's 2880×2160 target, so we downscale rather than upscale.

Discovery flow:
1. Fetch the BBC papers overview page
2. Find the most recent article link
3. Inside that article, find the <img> whose alt mentions "front page of the Guardian"
4. Replace the size parameter with 2560 and download

Note: this is yesterday's print edition (BBC publishes the roundup each
evening). For a morning newspaper display this is fine.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

import requests
from bs4 import BeautifulSoup

PROJECT_ROOT = Path(__file__).resolve().parent.parent
IMAGES_RAW = PROJECT_ROOT / "images" / "raw"

BBC_PAPERS_URL = "https://www.bbc.co.uk/news/blogs/the_papers"

ICHEF_RE = re.compile(
    r"https://ichef\.bbci\.co\.uk/ace/standard/\d+/"
    r"(cpsprodpb/[a-f0-9]+/live/[a-f0-9-]+\.(?:jpg|png))"
)
ARTICLE_RE = re.compile(r"/news/articles/[a-z0-9]+")
GUARDIAN_ALT_RE = re.compile(r"front page of (?:the )?guardian", re.IGNORECASE)

# Maximum resolution the ichef CDN will serve (empirically tested).
ICHEF_MAX_SIZE = 2560

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

logger = logging.getLogger("bbc_guardian_scraper")


def _new_session() -> requests.Session:
    sess = requests.Session()
    sess.trust_env = False
    sess.headers.update({"User-Agent": USER_AGENT})
    return sess


def _find_latest_article_url(html: str) -> str | None:
    """Find the most recent papers article link from the overview page."""
    soup = BeautifulSoup(html, "html.parser")
    for a in soup.find_all("a", href=ARTICLE_RE):
        text = a.get_text(strip=True)
        if text:  # skip empty/icon links
            return f"https://www.bbc.co.uk{a['href']}"
    return None


def _find_guardian_image_url(html: str) -> str | None:
    """
    Parse a BBC papers article and find the standalone Guardian front page.

    Individual articles contain separate <img> tags for each newspaper,
    with alt text like: '... reads the headline on the front page of the Guardian.'
    These are high-resolution photos (~1680×2050+) of the printed front page.
    """
    soup = BeautifulSoup(html, "html.parser")
    for img in soup.find_all("img"):
        alt = img.get("alt") or ""
        if not GUARDIAN_ALT_RE.search(alt):
            continue

        for attr in ("src", "srcset"):
            val = img.get(attr, "")
            m = ICHEF_RE.search(val)
            if m:
                path = m.group(1)
                return f"https://ichef.bbci.co.uk/ace/standard/{ICHEF_MAX_SIZE}/{path}"
    return None


def download_guardian() -> Path:
    """Download the latest Guardian front page via BBC News."""
    sess = _new_session()
    IMAGES_RAW.mkdir(parents=True, exist_ok=True)

    # Step 1: find the latest papers article
    logger.info("Fetching BBC papers overview")
    r = sess.get(BBC_PAPERS_URL, timeout=30)
    r.raise_for_status()

    article_url = _find_latest_article_url(r.text)
    if not article_url:
        raise RuntimeError("Could not find any article link on the BBC papers page")
    logger.info("Latest papers article: %s", article_url)

    # Step 2: find the Guardian image in that article
    r = sess.get(article_url, timeout=30)
    r.raise_for_status()

    image_url = _find_guardian_image_url(r.text)
    if not image_url:
        raise RuntimeError(
            f"Could not find a Guardian front page image in {article_url}. "
            "The article may not include the Guardian today, or the page "
            "structure may have changed."
        )

    # Step 3: download at max resolution
    logger.info("Downloading Guardian front page from %s", image_url)
    r = sess.get(image_url, timeout=30)
    r.raise_for_status()

    if len(r.content) < 50_000:
        raise RuntimeError(
            f"BBC returned suspiciously small image ({len(r.content)} bytes)"
        )

    out_path = IMAGES_RAW / "the-guardian.webp"
    out_path.write_bytes(r.content)
    logger.info("Saved %s (%d bytes)", out_path, len(r.content))
    return out_path
