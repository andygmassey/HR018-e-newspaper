"""
Scrape The Washington Post front page from Freedom Forum's CDN.

Freedom Forum (formerly Newseum) hosts daily front page PDFs:
    https://cdn.freedomforum.org/dfp/pdf{DAY}/DC_WP.pdf

where {DAY} is the day of the month without leading zero.
The PDF rasterises to ~2689×4748 at 200 DPI — excellent quality.

Requires poppler's pdftoppm in PATH.
"""
from __future__ import annotations

import logging
import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path

import requests

PROJECT_ROOT = Path(__file__).resolve().parent.parent
IMAGES_RAW = PROJECT_ROOT / "images" / "raw"

URL_TEMPLATE = "https://cdn.freedomforum.org/dfp/pdf{day}/DC_WP.pdf"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
DEFAULT_DPI = 200

logger = logging.getLogger("wp_scraper")


def download_wp(target_day: date | None = None, dpi: int = DEFAULT_DPI) -> Path:
    """Download today's Washington Post front page PDF and rasterise it."""
    if not shutil.which("pdftoppm"):
        raise RuntimeError("pdftoppm not found. Install poppler.")

    today = target_day or date.today()
    url = URL_TEMPLATE.format(day=today.day)

    logger.info("Fetching Washington Post PDF: %s", url)
    r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
    r.raise_for_status()

    if not r.headers.get("content-type", "").startswith("application/pdf"):
        raise RuntimeError(f"Expected PDF, got {r.headers.get('content-type')}")

    IMAGES_RAW.mkdir(parents=True, exist_ok=True)
    pdf_path = IMAGES_RAW / "_wp.pdf"
    pdf_path.write_bytes(r.content)
    logger.info("Downloaded WP PDF (%d bytes)", len(r.content))

    out_prefix = IMAGES_RAW / "_wp_render"
    subprocess.run(
        ["pdftoppm", "-jpeg", "-r", str(dpi), "-f", "1", "-l", "1",
         str(pdf_path), str(out_prefix)],
        check=True, capture_output=True,
    )

    rendered = out_prefix.parent / f"{out_prefix.name}-1.jpg"
    if not rendered.exists():
        raise RuntimeError(f"pdftoppm did not produce {rendered}")

    final_path = IMAGES_RAW / "the-washington-post.webp"
    from PIL import Image
    img = Image.open(rendered)
    img.save(final_path, format="JPEG", quality=92)
    rendered.unlink(missing_ok=True)
    pdf_path.unlink(missing_ok=True)
    logger.info("Saved %s (%s)", final_path, img.size)
    return final_path
