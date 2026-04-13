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


def _find_frontpage_tweet_ids(sess: requests.Session) -> list[str]:
    """Find recent FT front page tweet IDs via Nitter RSS.

    Returns multiple candidates (newest first) so the caller can
    skip tweets that contain videos instead of photos.
    """
    for base in NITTER_INSTANCES:
        url = f"{base}/FT/rss"
        try:
            r = sess.get(url, timeout=15)
            if r.status_code != 200:
                continue
            items = re.findall(
                r'<item>.*?<link>(.*?)</link>.*?<description>(.*?)</description>.*?</item>',
                r.text, re.DOTALL
            )
            # Front page tweets first, then any tweet as fallback
            frontpage_ids = []
            other_ids = []
            for link, desc in items:
                m = re.search(r'/status/(\d+)', link)
                if not m:
                    continue
                if 'front page' in desc.lower():
                    frontpage_ids.append(m.group(1))
                else:
                    other_ids.append(m.group(1))
            if frontpage_ids or other_ids:
                return frontpage_ids + other_ids
        except Exception:
            logger.debug("Nitter instance %s failed", base)
            continue
    return []


def _get_image_url(sess: requests.Session, tweet_id: str) -> str | None:
    """Get the full-res PHOTO URL from a tweet via fxtwitter API.

    Only returns static image URLs (pbs.twimg.com). Rejects videos
    (video.twimg.com) — the @FT account sometimes posts video content
    that is NOT the front page.
    """
    url = FXTWITTER_API.format(tweet_id=tweet_id)
    try:
        r = sess.get(url, timeout=15)
        if r.status_code != 200:
            return None
        data = r.json()
        tweet = data.get("tweet", {})
        media = tweet.get("media", {})
        # Use photos specifically, NOT all (which includes videos)
        photos = media.get("photos", [])
        for photo in photos:
            img_url = photo.get("url", "")
            # Only accept pbs.twimg.com (static images), reject video.twimg.com
            if "pbs.twimg.com" not in img_url:
                continue
            # Request original resolution
            if "name=" not in img_url:
                img_url += "?name=orig"
            else:
                img_url = re.sub(r'name=\w+', 'name=orig', img_url)
            return img_url
    except Exception:
        logger.debug("fxtwitter API failed for tweet %s", tweet_id)
    return None


def download_ft() -> Path:
    """Download today's FT front page from Twitter."""
    sess = _new_session()

    logger.info("Searching for latest FT front page tweets...")
    tweet_ids = _find_frontpage_tweet_ids(sess)
    if not tweet_ids:
        raise RuntimeError("Could not find FT front page tweets via Nitter RSS")

    # Try each candidate until we find one with a photo (not a video)
    img_url = None
    for tweet_id in tweet_ids:
        logger.info("Trying tweet %s...", tweet_id)
        img_url = _get_image_url(sess, tweet_id)
        if img_url:
            break
        logger.info("Tweet %s has no photo, trying next", tweet_id)

    if not img_url:
        raise RuntimeError(f"No tweets with photos found among {len(tweet_ids)} candidates")

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
