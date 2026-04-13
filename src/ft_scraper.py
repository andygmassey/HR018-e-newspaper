"""
Scrape the Financial Times front page from @FT on Twitter/X.

The @FT account posts daily front page images at ~2537×4096 with text
like "Just published: front page of the Financial Times". The images
on pbs.twimg.com are public and require no auth.

Discovery flow:
1. Fetch @FT's RSS via a Nitter instance (or fxtwitter)
2. Find the latest "front page" tweet
3. Extract the image URL via fxtwitter API
4. Download the full-res image (?name=orig)
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

import requests

PROJECT_ROOT = Path(__file__).resolve().parent.parent
IMAGES_RAW = PROJECT_ROOT / "images" / "raw"

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# Nitter instances for RSS (try multiple in case one is down)
NITTER_INSTANCES = [
    "https://nitter.net",
    "https://nitter.privacydev.net",
    "https://nitter.poast.org",
]

FXTWITTER_API = "https://api.fxtwitter.com/FT/status/{tweet_id}"

logger = logging.getLogger("ft_scraper")


def _new_session() -> requests.Session:
    sess = requests.Session()
    sess.trust_env = False
    sess.headers.update({"User-Agent": USER_AGENT})
    return sess


def _find_frontpage_tweet_id(sess: requests.Session) -> str | None:
    """Find the latest FT front page tweet ID via Nitter RSS."""
    for base in NITTER_INSTANCES:
        url = f"{base}/FT/rss"
        try:
            r = sess.get(url, timeout=15)
            if r.status_code != 200:
                continue
            # Look for tweets containing "front page" — extract the tweet URL
            # Nitter RSS has <link> elements like https://nitter.net/FT/status/1234567890
            matches = re.findall(r'/FT/status/(\d+)', r.text)
            # Also get the tweet text to filter for front page posts
            items = re.findall(
                r'<item>.*?<link>(.*?)</link>.*?<description>(.*?)</description>.*?</item>',
                r.text, re.DOTALL
            )
            for link, desc in items:
                if 'front page' in desc.lower():
                    m = re.search(r'/status/(\d+)', link)
                    if m:
                        return m.group(1)
            # If no "front page" match, try the first tweet with an image
            if matches:
                return matches[0]
        except Exception:
            logger.debug("Nitter instance %s failed", base)
            continue
    return None


def _get_image_url(sess: requests.Session, tweet_id: str) -> str | None:
    """Get the full-res image URL from a tweet via fxtwitter API."""
    url = FXTWITTER_API.format(tweet_id=tweet_id)
    try:
        r = sess.get(url, timeout=15)
        if r.status_code != 200:
            return None
        data = r.json()
        # Navigate: tweet.media.all[0].url or tweet.media.photos[0].url
        tweet = data.get("tweet", {})
        media = tweet.get("media", {})
        photos = media.get("photos", media.get("all", []))
        if photos and len(photos) > 0:
            img_url = photos[0].get("url", "")
            # Ensure we get the original resolution
            if "pbs.twimg.com" in img_url and "name=" not in img_url:
                img_url += "?name=orig"
            elif "name=" in img_url:
                img_url = re.sub(r'name=\w+', 'name=orig', img_url)
            return img_url
    except Exception:
        logger.debug("fxtwitter API failed for tweet %s", tweet_id)
    return None


def download_ft() -> Path:
    """Download today's FT front page from Twitter."""
    sess = _new_session()

    logger.info("Searching for latest FT front page tweet...")
    tweet_id = _find_frontpage_tweet_id(sess)
    if not tweet_id:
        raise RuntimeError("Could not find FT front page tweet via Nitter RSS")

    logger.info("Found tweet %s, fetching image URL via fxtwitter...", tweet_id)
    img_url = _get_image_url(sess, tweet_id)
    if not img_url:
        raise RuntimeError(f"Could not extract image URL from tweet {tweet_id}")

    logger.info("Downloading FT front page: %s", img_url[:80])
    r = sess.get(img_url, timeout=30)
    r.raise_for_status()

    if len(r.content) < 50000:
        raise RuntimeError(f"FT image suspiciously small ({len(r.content)} bytes)")

    IMAGES_RAW.mkdir(parents=True, exist_ok=True)
    final_path = IMAGES_RAW / "financial-times.webp"
    final_path.write_bytes(r.content)
    logger.info("Saved %s (%d bytes)", final_path, len(r.content))
    return final_path
