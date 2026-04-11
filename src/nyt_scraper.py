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

    final_path = IMAGES_RAW / "the-new-york-times.webp"
    rendered.rename(final_path)
    pdf_path.unlink(missing_ok=True)
    logger.info("Saved %s", final_path)
    return final_path


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
