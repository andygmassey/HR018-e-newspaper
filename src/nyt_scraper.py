"""
Scrape The New York Times front page from their public PDF archive.

nytimes.com publishes each day's print front page at:
    https://static01.nyt.com/images/YYYY/MM/DD/nytfrontpage/scan.pdf

This is the highest quality source we have — ~2MB, rasterisable at any DPI.
At 200 DPI the page comes out to roughly 2442 x 4685 pixels, which is plenty
of headroom for a 2880 x 2160 e-ink display.

Requires poppler's pdftoppm in PATH (install with `brew install poppler` on
macOS). We shell out rather than pulling in a Python PDF rendering library.
"""
from __future__ import annotations

import logging
import shutil
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import requests
from PIL import Image

PROJECT_ROOT = Path(__file__).resolve().parent.parent
IMAGES_RAW = PROJECT_ROOT / "images" / "raw"

URL_TEMPLATE = "https://static01.nyt.com/images/{year}/{month}/{day}/nytfrontpage/scan.pdf"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
DEFAULT_DPI = 200

logger = logging.getLogger("nyt_scraper")


def _new_session() -> requests.Session:
    sess = requests.Session()
    sess.trust_env = False
    sess.headers.update({"User-Agent": USER_AGENT})
    return sess


def _pdf_url(day: date) -> str:
    return URL_TEMPLATE.format(
        year=f"{day.year:04d}",
        month=f"{day.month:02d}",
        day=f"{day.day:02d}",
    )


def download_nyt(target_day: date | None = None, dpi: int = DEFAULT_DPI) -> Path:
    """
    Download today's NYT front page PDF, rasterise it, and save as webp.

    If the requested day's PDF isn't available yet (NYT hasn't published the
    print edition), fall back to the previous day.

    Returns the path to the saved raw image.
    """
    if not shutil.which("pdftoppm"):
        raise RuntimeError(
            "pdftoppm not found. Install poppler: `brew install poppler` on macOS "
            "or `apt install poppler-utils` on Linux."
        )

    today = target_day or date.today()
    sess = _new_session()

    # Try today first, then yesterday — NYT's next-day PDF often isn't
    # published until early-morning US Eastern time, which is evening HKT.
    for candidate in (today, today - timedelta(days=1)):
        url = _pdf_url(candidate)
        logger.info("Trying NYT PDF: %s", url)
        r = sess.get(url, timeout=30)
        if r.status_code == 200 and r.headers.get("content-type", "").startswith("application/pdf"):
            break
        logger.info("  %s → %s", r.status_code, r.headers.get("content-type", ""))
    else:
        raise RuntimeError("No NYT PDF available for today or yesterday")

    # Save the PDF to a temp file; pdftoppm can't read from stdin easily
    pdf_path = IMAGES_RAW / "_nyt.pdf"
    IMAGES_RAW.mkdir(parents=True, exist_ok=True)
    pdf_path.write_bytes(r.content)
    logger.info("Downloaded NYT PDF (%d bytes) for %s", len(r.content), candidate)

    # Rasterise page 1 of the PDF. pdftoppm appends -N to the output prefix
    # where N is the page number, so we call it into a temp prefix and then
    # rename the single output file.
    out_prefix = IMAGES_RAW / "_nyt_render"
    subprocess.run(
        [
            "pdftoppm",
            "-jpeg",
            "-r",
            str(dpi),
            "-f",
            "1",
            "-l",
            "1",
            str(pdf_path),
            str(out_prefix),
        ],
        check=True,
        capture_output=True,
    )

    # pdftoppm emits `_nyt_render-1.jpg`
    rendered = out_prefix.parent / f"{out_prefix.name}-1.jpg"
    if not rendered.exists():
        raise RuntimeError(f"pdftoppm did not produce {rendered}")

    # Auto-trim the print-plate bleed: NYT's PDF includes a top row of
    # CMYK registration marks, filename, and price info, plus some
    # whitespace at the bottom. We find the actual content bounds by
    # scanning ink density row-by-row.
    final_path = IMAGES_RAW / "the-new-york-times.webp"
    trimmed = _trim_print_bleed(rendered)
    trimmed.save(final_path, format="JPEG", quality=92)
    rendered.unlink(missing_ok=True)
    pdf_path.unlink(missing_ok=True)
    logger.info("Saved %s (%s)", final_path, trimmed.size)
    return final_path


def _trim_print_bleed(img_path: Path) -> Image.Image:
    """
    Crop the print-plate bleed areas off a NYT scan.

    The PDF includes, above the newspaper proper:
      - CMYK colour registration marks
      - A filename line like `Nxxx,2026-04-11,A,001,Bs-4C,E1_+`
      - A price indicator
    and below the last article some whitespace.

    We walk the rows from top and bottom looking for dense content blocks
    (where at least 10% of the row is ink-like), with the threshold low
    enough to catch the masthead but high enough to skip the sparse print
    marks. A small margin above the masthead preserves the "All the News
    That's Fit to Print" banner.
    """
    img = Image.open(img_path)
    gray = img.convert("L")
    # Row ink density — fraction of pixels darker than "near-white".
    # Use numpy if available, fall back to a pure-Python implementation so
    # the scraper still runs on minimal installs.
    try:
        import numpy as np
        arr = np.array(gray)
        h, w = arr.shape
        row_density = (arr < 200).sum(axis=1) / w
    except ImportError:
        px = gray.load()
        w, h = gray.size
        row_density = [
            sum(1 for x in range(w) if px[x, y] < 200) / w for y in range(h)
        ]

    # The NYT PDF has a printer-mark band at y~80-110 (CMYK, filename, price)
    # with 6-9% ink density, followed by ~100 rows of whitespace, then the
    # masthead at y=220+ with 24-44% density. A 20% threshold cleanly skips
    # the printer marks and lands on the first real masthead row.
    DENSE_THRESHOLD = 0.20

    # Find first dense row from the top
    top = 0
    for y in range(h):
        if row_density[y] >= DENSE_THRESHOLD:
            top = y
            break

    # Find last dense row from the bottom.
    bottom = h
    for y in range(h - 1, -1, -1):
        if row_density[y] >= DENSE_THRESHOLD:
            bottom = y + 1
            break

    # Generous margin above the first dense row to preserve the "All the News
    # That's Fit to Print" banner that sits above the masthead proper. Below,
    # a small margin is enough.
    top_margin = max(30, h // 100)
    bottom_margin = max(8, h // 500)
    top = max(0, top - top_margin)
    bottom = min(h, bottom + bottom_margin)

    logger.info("Trimming NYT print bleed: y=%d..%d of %d", top, bottom, h)
    return img.crop((0, top, img.width, bottom))


def main(argv: list[str]) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    try:
        download_nyt()
        return 0
    except Exception:
        logger.exception("Failed to download NYT front page")
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
