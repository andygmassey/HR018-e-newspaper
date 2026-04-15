"""
Scrape newspaper front page images from frontpages.com.

Saves images to images/raw/ named by paper slug. Updates manifest.json
with metadata (paper name, source URL, fetched timestamp, original date).
"""
from __future__ import annotations

import json
import logging
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import requests
from bs4 import BeautifulSoup

PROJECT_ROOT = Path(__file__).resolve().parent.parent
IMAGES_RAW = PROJECT_ROOT / "images" / "raw"
MANIFEST = PROJECT_ROOT / "images" / "manifest.json"

FRONTPAGES_HOMEPAGE = "https://www.frontpages.com/"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# Image path pattern: /t/YYYY/MM/DD/<slug>-<random>.webp
THUMB_PATH_RE = re.compile(r"^/t/(\d{4})/(\d{2})/(\d{2})/([a-z][a-z0-9-]+?)-([a-z0-9]{6,})\.webp$")

logger = logging.getLogger("scraper")


def _new_session() -> requests.Session:
    """Build a requests session that ignores any system proxy."""
    sess = requests.Session()
    sess.trust_env = False
    sess.headers.update({"User-Agent": USER_AGENT})
    return sess


def fetch_homepage(sess: requests.Session) -> str:
    r = sess.get(FRONTPAGES_HOMEPAGE, timeout=20)
    r.raise_for_status()
    return r.text


def parse_papers(html: str) -> dict[str, dict]:
    """
    Parse the homepage and return a mapping of paper slug -> metadata:
        {
            "slug": "financial-times",
            "section": "UK Newspapers",
            "thumb_url": "https://www.frontpages.com/t/.../<slug>-...webp",
            "highres_url": "https://www.frontpages.com/t/.../<slug>-...@2x.webp",
            "edition_date": "2026-04-10",  # YYYY-MM-DD from URL path
        }
    """
    soup = BeautifulSoup(html, "html.parser")
    papers: dict[str, dict] = {}
    current_section = "Unknown"

    for el in soup.find_all(["h1", "h2", "h3", "img"]):
        if el.name in ("h1", "h2", "h3"):
            text = el.get_text(strip=True)
            if text:
                current_section = text
            continue

        # img element
        src = el.get("src", "")
        m = THUMB_PATH_RE.match(src)
        if not m:
            continue
        year, month, day, slug, _ = m.groups()

        # Build absolute URLs
        thumb_url = f"https://www.frontpages.com{src}"
        highres_url = thumb_url.replace(".webp", "@2x.webp")

        # The same slug may appear in multiple sections (e.g. usa-today-sports
        # appears under both Sports and US Newspapers). Keep the first occurrence.
        if slug in papers:
            continue

        papers[slug] = {
            "slug": slug,
            "section": current_section,
            "thumb_url": thumb_url,
            "highres_url": highres_url,
            "edition_date": f"{year}-{month}-{day}",
        }

    return papers


def download_paper(sess: requests.Session, paper: dict, dest_dir: Path) -> Path:
    """Download the highest-resolution available image for a paper."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    url = paper["highres_url"]
    r = sess.get(url, timeout=30)
    r.raise_for_status()

    # Save as <slug>.webp (overwrite previous day's file)
    out_path = dest_dir / f"{paper['slug']}.webp"
    out_path.write_bytes(r.content)
    return out_path


def scrape(slugs: Iterable[str] | None = None) -> dict:
    """
    Scrape frontpages.com and download requested papers.

    Args:
        slugs: List of paper slugs to download. If None, returns the parsed
               manifest without downloading anything.

    Returns:
        Manifest dict with all parsed papers and download status.
    """
    sess = _new_session()
    logger.info("Fetching frontpages.com homepage")
    html = fetch_homepage(sess)
    papers = parse_papers(html)
    logger.info("Found %d papers across all sections", len(papers))

    manifest = {
        "scraped_at": datetime.now(timezone.utc).isoformat(),
        "source": "frontpages.com",
        "papers": papers,
        "downloaded": {},
    }

    if slugs is None:
        return manifest

    target_slugs = list(slugs)
    for slug in target_slugs:
        if slug not in papers:
            logger.warning("Paper '%s' not found on frontpages.com today", slug)
            manifest["downloaded"][slug] = {"status": "not_found"}
            continue
        try:
            path = download_paper(sess, papers[slug], IMAGES_RAW)
            size = path.stat().st_size
            logger.info("Downloaded %s (%d bytes) -> %s", slug, size, path)
            manifest["downloaded"][slug] = {
                "status": "ok",
                "path": str(path.relative_to(PROJECT_ROOT)),
                "bytes": size,
                "edition_date": papers[slug]["edition_date"],
            }
        except Exception as exc:
            logger.exception("Failed to download %s", slug)
            manifest["downloaded"][slug] = {"status": "error", "error": str(exc)}

    return manifest


def save_manifest(manifest: dict) -> None:
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(manifest, indent=2))


# Paper-slug → high-resolution scraper module override.
#
# For these papers we have a direct PDF/high-res source that produces
# dramatically sharper output than frontpages.com's 600×800 thumbnails.
# The main() function will try these first and fall back to the generic
# frontpages.com scraper if they fail.
HIRES_SCRAPERS = {
    "the-new-york-times": ("nyt_scraper", "download_nyt"),
    "south-china-morning-post": ("pressreader_scraper", "download_pressreader"),
    "the-washington-post": ("wp_scraper", "download_wp"),
    "financial-times": ("ft_scraper", "download_ft"),
    "los-angeles-times": ("lat_scraper", "download_lat"),
}


def _run_hires(slug: str) -> bool:
    """Attempt to fetch a high-res version of `slug`. Returns True on success."""
    entry = HIRES_SCRAPERS.get(slug)
    if not entry:
        return False
    module_name, func_name = entry
    try:
        import importlib
        mod = importlib.import_module(module_name)
        func = getattr(mod, func_name)
        # PressReader scraper needs the slug to look up the CID
        if module_name == "pressreader_scraper":
            func(slug)
        else:
            func()
        return True
    except Exception:
        logger.exception("High-res scraper %s for %s failed — falling back", module_name, slug)
        return False


def main(argv: list[str]) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    # CLI: scraper.py [slug ...]  - download specified papers
    #      scraper.py --list      - just print the available papers
    if len(argv) >= 2 and argv[1] == "--list":
        manifest = scrape(slugs=None)
        for slug, info in sorted(manifest["papers"].items(), key=lambda x: (x[1]["section"], x[0])):
            print(f"{info['section']:<30} {slug:<40} {info['edition_date']}")
        return 0

    # Default set if no slugs specified — override on the CLI to taste
    default_slugs = [
        "financial-times",
        "south-china-morning-post",
        "the-guardian",
        "los-angeles-times",
        "the-new-york-times",
        "the-washington-post",
        "the-globe-and-mail",
        "the-irish-times",
    ]
    slugs = argv[1:] if len(argv) > 1 else default_slugs

    # Try high-res scrapers first; collect the slugs that fall through to
    # the generic frontpages.com pipeline.
    hires_done: set[str] = set()
    for slug in slugs:
        if slug in HIRES_SCRAPERS:
            logger.info("Using high-res scraper for %s", slug)
            if _run_hires(slug):
                hires_done.add(slug)

    remaining = [s for s in slugs if s not in hires_done]
    if remaining:
        manifest = scrape(slugs=remaining)
    else:
        manifest = {"downloaded": {}, "papers": {}}
    for slug in hires_done:
        manifest["downloaded"][slug] = {"status": "ok", "source": "hires"}
    save_manifest(manifest)

    ok = sum(1 for d in manifest["downloaded"].values() if d.get("status") == "ok")
    total = len(manifest["downloaded"])
    logger.info("Done: %d/%d papers downloaded successfully", ok, total)
    return 0 if ok > 0 else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
