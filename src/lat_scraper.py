"""
Scrape the LA Times front page from Freedom Forum's CDN.

Same pattern as wp_scraper.py:
    https://cdn.freedomforum.org/dfp/pdf{DAY}/CA_LAT.pdf

Rasterises to ~2175×4489 at 200 DPI.
Requires poppler's pdftoppm in PATH.
"""
from __future__ import annotations

import logging
import shutil
import subprocess
from datetime import date
from pathlib import Path

import requests
from PIL import Image

PROJECT_ROOT = Path(__file__).resolve().parent.parent
IMAGES_RAW = PROJECT_ROOT / "images" / "raw"

URL_TEMPLATE = "https://cdn.freedomforum.org/dfp/pdf{day}/CA_LAT.pdf"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

logger = logging.getLogger("lat_scraper")


def download_lat(target_day: date | None = None, dpi: int = 200) -> Path:
    """Download today's LA Times front page PDF and rasterise it."""
    if not shutil.which("pdftoppm"):
        raise RuntimeError("pdftoppm not found. Install poppler.")

    today = target_day or date.today()
    url = URL_TEMPLATE.format(day=today.day)

    logger.info("Fetching LA Times PDF: %s", url)
    r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
    r.raise_for_status()

    IMAGES_RAW.mkdir(parents=True, exist_ok=True)
    pdf_path = IMAGES_RAW / "_lat.pdf"
    pdf_path.write_bytes(r.content)

    out_prefix = IMAGES_RAW / "_lat_render"
    subprocess.run(
        ["pdftoppm", "-jpeg", "-r", str(dpi), "-f", "1", "-l", "1",
         str(pdf_path), str(out_prefix)],
        check=True, capture_output=True,
    )

    rendered = out_prefix.parent / f"{out_prefix.name}-1.jpg"
    if not rendered.exists():
        raise RuntimeError(f"pdftoppm did not produce {rendered}")

    final_path = IMAGES_RAW / "los-angeles-times.webp"
    img = Image.open(rendered)
    img.save(final_path, format="JPEG", quality=92)
    rendered.unlink(missing_ok=True)
    pdf_path.unlink(missing_ok=True)
    logger.info("Saved %s (%s)", final_path, img.size)
    return final_path
