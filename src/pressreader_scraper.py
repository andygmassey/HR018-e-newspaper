"""
Scrape newspaper front pages from PressReader's public CDN.

PressReader serves front page images at up to 2000px width without
authentication for some papers. The URL pattern is:
    https://i.prcdn.co/img?cid=<CID>&page=1&width=2000

Known CIDs:
    6150  South China Morning Post
    1020  The Guardian (UK)

Returns PNG images at ~2000×3000+ pixels — excellent for the e-ink display.
"""
from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import requests
from PIL import Image

PROJECT_ROOT = Path(__file__).resolve().parent.parent
IMAGES_RAW = PROJECT_ROOT / "images" / "raw"

# Full-page takeover ads (e.g. HSBC on SCMP) have a much higher percentage
# of dark pixels than a real newspaper front page (which is mostly white
# paper with black text). Threshold: a normal front page is ~25-40% dark;
# a full-page ad is typically >55%.
AD_DARK_THRESHOLD = 0.55

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

PAPERS = {
    "south-china-morning-post": 6150,
    "the-guardian": 1022,
}

logger = logging.getLogger("pressreader_scraper")


def download_pressreader(slug: str) -> Path:
    """Download the front page for a PressReader-hosted paper."""
    cid = PAPERS.get(slug)
    if cid is None:
        raise ValueError(f"No PressReader CID for {slug}")

    IMAGES_RAW.mkdir(parents=True, exist_ok=True)
    url = f"https://i.prcdn.co/img?cid={cid}&page=1&width=2000"

    logger.info("Fetching %s from PressReader (cid=%d)", slug, cid)
    r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
    r.raise_for_status()

    if len(r.content) < 10000:
        raise RuntimeError(f"PressReader returned suspiciously small image ({len(r.content)} bytes)")

    # Detect full-page takeover ads (e.g. HSBC ads on SCMP).
    # These have a much higher dark-pixel ratio than real newspaper pages.
    out_path = IMAGES_RAW / f"{slug}.webp"
    out_path.write_bytes(r.content)

    img = Image.open(out_path).convert("L")
    dark_ratio = (np.array(img) < 128).mean()
    logger.info("Saved %s (%d bytes, dark_ratio=%.2f)", out_path, len(r.content), dark_ratio)

    if dark_ratio > AD_DARK_THRESHOLD:
        logger.warning(
            "%s front page looks like a full-page ad (dark_ratio=%.2f > %.2f) — rejecting",
            slug, dark_ratio, AD_DARK_THRESHOLD,
        )
        raise RuntimeError(
            f"{slug} front page appears to be a full-page takeover ad "
            f"(dark_ratio={dark_ratio:.2f}). Falling back."
        )

    return out_path
