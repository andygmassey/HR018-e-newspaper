"""
Process raw newspaper images for the e-ink display.

Reads images from images/raw/, scales/letterboxes to 2880x2160 (the display
native resolution), converts to grayscale, and writes the result to
images/processed/<slug>.png. Also updates images/current.png to point at
the chosen paper for the day.

The selection strategy is configurable:
    - "fixed": always use a specific paper
    - "rotate": cycle through the configured papers, one per day
    - "weekday": map weekdays to specific papers (e.g. SCMP on weekdays, FT on weekends)

Configuration lives in config.json at the project root.
"""
from __future__ import annotations

import json
import logging
import shutil
import sys
from datetime import date, datetime
from pathlib import Path

from PIL import Image

PROJECT_ROOT = Path(__file__).resolve().parent.parent
IMAGES_RAW = PROJECT_ROOT / "images" / "raw"
IMAGES_PROCESSED = PROJECT_ROOT / "images" / "processed"
CURRENT_IMAGE = PROJECT_ROOT / "images" / "current.png"
CONFIG_PATH = PROJECT_ROOT / "config.json"

# Display resolution (Avalue EPD-42S, landscape native)
DISPLAY_W = 2880
DISPLAY_H = 2160

# Display orientation: "landscape" (2880x2160) or "portrait" (2160x2880).
# Newspapers are tall, so portrait gives a much better fit. The render
# pipeline produces an image at the chosen orientation; if the display is
# physically mounted landscape, leave as "portrait" and the OpenDisplay
# Android app or display rotation will handle it.
DEFAULT_ORIENTATION = "portrait"

logger = logging.getLogger("processor")


def load_config() -> dict:
    if CONFIG_PATH.exists():
        return json.loads(CONFIG_PATH.read_text())
    # Sensible defaults
    return {
        "selection": "weekday",
        "weekday_map": {
            # Monday=0 .. Sunday=6
            "0": "south-china-morning-post",
            "1": "south-china-morning-post",
            "2": "south-china-morning-post",
            "3": "south-china-morning-post",
            "4": "financial-times",  # Friday
            "5": "the-new-york-times",  # Saturday
            "6": "financial-times",  # Sunday (FT Weekend)
        },
        "rotation": [
            "south-china-morning-post",
            "financial-times",
            "the-new-york-times",
            "the-washington-post",
        ],
        "fixed": "financial-times",
        "orientation": DEFAULT_ORIENTATION,
        "fit_mode": "contain",  # "contain" (letterbox) or "cover" (crop edges)
    }


def choose_paper(config: dict, today: date | None = None) -> str:
    """Pick which paper to display today."""
    today = today or date.today()
    strategy = config.get("selection", "fixed")

    if strategy == "fixed":
        return config["fixed"]
    if strategy == "weekday":
        return config["weekday_map"][str(today.weekday())]
    if strategy == "rotate":
        rotation = config["rotation"]
        idx = today.toordinal() % len(rotation)
        return rotation[idx]

    raise ValueError(f"Unknown selection strategy: {strategy!r}")


def process_image(
    src_path: Path,
    out_path: Path,
    orientation: str = DEFAULT_ORIENTATION,
    fit_mode: str = "contain",
    rotation: int = 0,
) -> Image.Image:
    """
    Resize and dither a newspaper image for the e-ink display.

    Args:
        src_path: Source webp/png/jpg.
        out_path: Where to write the processed PNG.
        orientation: "landscape" (2880x2160) or "portrait" (2160x2880).
                     This is the orientation at which the newspaper content
                     is composed — usually "portrait" since newspapers are
                     taller than wide.
        fit_mode: "contain" letterboxes (preserves entire page);
                  "cover" crops to fill (loses some edges).
        rotation: Final clockwise rotation in degrees (0, 90, 180, 270)
                  applied after fitting. Use this when the physical display
                  is mounted in a different orientation than the content.
                  For example, render portrait content at 2160x2880 then
                  rotate 90 to get a 2880x2160 landscape image that reads
                  correctly on a physically landscape-mounted display held
                  sideways.

    Returns the final PIL Image.
    """
    target_w, target_h = (
        (DISPLAY_W, DISPLAY_H) if orientation == "landscape" else (DISPLAY_H, DISPLAY_W)
    )

    img = Image.open(src_path)
    logger.info("Loaded %s: %s, mode=%s", src_path.name, img.size, img.mode)

    # Convert to grayscale up front so resize works on luminance only
    img_gray = img.convert("L")

    src_w, src_h = img_gray.size
    src_aspect = src_w / src_h
    target_aspect = target_w / target_h

    if fit_mode == "contain":
        # Scale to fit entirely within the target, with white background
        if src_aspect > target_aspect:
            new_w = target_w
            new_h = round(target_w / src_aspect)
        else:
            new_h = target_h
            new_w = round(target_h * src_aspect)

        scaled = img_gray.resize((new_w, new_h), Image.LANCZOS)
        # White background — feels more like real newsprint than black bars
        canvas = Image.new("L", (target_w, target_h), 255)
        canvas.paste(scaled, ((target_w - new_w) // 2, (target_h - new_h) // 2))
    elif fit_mode in ("cover", "cover_top"):
        # Scale so the image fills the target completely, cropping overflow.
        if src_aspect > target_aspect:
            new_h = target_h
            new_w = round(target_h * src_aspect)
        else:
            new_w = target_w
            new_h = round(target_w / src_aspect)

        scaled = img_gray.resize((new_w, new_h), Image.LANCZOS)
        # "cover" is a center crop; "cover_top" anchors the top and crops the
        # bottom (useful for newspapers — preserves the masthead and lead
        # headlines, trims the bottom columns).
        if fit_mode == "cover_top":
            left = (new_w - target_w) // 2
            top = 0
        else:
            left = (new_w - target_w) // 2
            top = (new_h - target_h) // 2
        canvas = scaled.crop((left, top, left + target_w, top + target_h))
    else:
        raise ValueError(f"Unknown fit_mode: {fit_mode!r}")

    # Optional post-rotation for displays that are physically mounted in a
    # different orientation than the content is composed at. PIL's rotate
    # argument is counter-clockwise, so we negate to get clockwise.
    if rotation not in (0, 90, 180, 270):
        raise ValueError(f"rotation must be 0/90/180/270, got {rotation}")
    if rotation:
        canvas = canvas.rotate(-rotation, expand=True)

    # Save grayscale PNG. The OpenDisplay server (or display itself) will
    # apply the final 1-bit dither using its own waveform-aware logic.
    out_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out_path, format="PNG", optimize=True)
    logger.info("Wrote %s (%dx%d rotation=%d)", out_path, canvas.width, canvas.height, rotation)
    return canvas


def process_today(config: dict | None = None) -> Path:
    """Process today's chosen paper and update images/current.png."""
    config = config or load_config()
    slug = choose_paper(config)
    logger.info("Today's paper: %s", slug)

    src = IMAGES_RAW / f"{slug}.webp"
    if not src.exists():
        raise FileNotFoundError(
            f"Raw image for '{slug}' not found at {src}. Run scraper.py first."
        )

    out = IMAGES_PROCESSED / f"{slug}-{date.today().isoformat()}.png"
    process_image(
        src,
        out,
        orientation=config.get("orientation", DEFAULT_ORIENTATION),
        fit_mode=config.get("fit_mode", "contain"),
        rotation=int(config.get("rotation", 0)),
    )

    # Update current.png as a copy (not symlink — Android client may not handle symlinks)
    shutil.copy2(out, CURRENT_IMAGE)
    logger.info("Updated %s", CURRENT_IMAGE)
    return CURRENT_IMAGE


def main(argv: list[str]) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    config = load_config()

    # CLI:
    #   processor.py             - process today's chosen paper
    #   processor.py <slug>      - process a specific paper
    #   processor.py --all       - process every raw image we have
    if len(argv) >= 2 and argv[1] == "--all":
        for raw in sorted(IMAGES_RAW.glob("*.webp")):
            slug = raw.stem
            out = IMAGES_PROCESSED / f"{slug}-{date.today().isoformat()}.png"
            process_image(
                raw,
                out,
                orientation=config.get("orientation", DEFAULT_ORIENTATION),
                fit_mode=config.get("fit_mode", "contain"),
                rotation=int(config.get("rotation", 0)),
            )
        return 0

    if len(argv) >= 2:
        slug = argv[1]
        src = IMAGES_RAW / f"{slug}.webp"
        if not src.exists():
            logger.error("No raw image for slug '%s'", slug)
            return 1
        out = IMAGES_PROCESSED / f"{slug}-{date.today().isoformat()}.png"
        process_image(
            src,
            out,
            orientation=config.get("orientation", DEFAULT_ORIENTATION),
            fit_mode=config.get("fit_mode", "contain"),
            rotation=int(config.get("rotation", 0)),
        )
        shutil.copy2(out, CURRENT_IMAGE)
        return 0

    process_today(config)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
