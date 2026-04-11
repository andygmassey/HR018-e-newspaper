"""
OpenDisplay WiFi server for the e-newspaper display.

Always-on TCP server (runs via launchd) that serves images/current.png to
any OpenDisplay client that connects. The display polls this server every
poll_interval seconds; when current.png changes (because the daily
scraper+processor pipeline ran), the next poll receives the new image.

Usage:
    python -m src.server                # default port 2446, mDNS on
    python -m src.server --port 2446    # explicit port
    python -m src.server --no-mdns      # disable Bonjour advertising
    python -m src.server --once         # serve a single image and exit
                                          (useful for testing)
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys
from pathlib import Path
from typing import Optional

from PIL import Image

from opendisplay.wifi.imaging import image_to_1bpp
from opendisplay.wifi.protocol import DisplayAnnouncement
from opendisplay.wifi.server import OpenDisplayServer

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CURRENT_IMAGE = PROJECT_ROOT / "images" / "current.png"

# Display polls this often (seconds). Newspaper updates daily, so a generous
# interval is fine — the display only needs to check periodically. 5 minutes
# is a reasonable balance between responsiveness and network chatter.
DEFAULT_POLL_INTERVAL = 300  # 5 minutes
DEFAULT_PORT = 2446

logger = logging.getLogger("server")


class CurrentImageProvider:
    """
    Image provider callable that loads images/current.png on each request.

    py-opendisplay's server calls the provider every time the display polls,
    passing the display's announcement (resolution + colour scheme). We use
    the announcement to size the image correctly. The server itself
    deduplicates by SHA-256, so we don't need to cache here.
    """

    def __init__(self, image_path: Path) -> None:
        self.image_path = image_path

    def __call__(self, announcement: Optional[DisplayAnnouncement]) -> Optional[bytes]:
        if announcement is None:
            logger.warning("Provider called before announcement received; no image")
            return None

        if not self.image_path.exists():
            logger.warning("Image file %s does not exist", self.image_path)
            return None

        try:
            img = Image.open(self.image_path)
            logger.info(
                "Encoding %s (%s, %s) for display %dx%d scheme=%d",
                self.image_path.name,
                img.size,
                img.mode,
                announcement.width,
                announcement.height,
                announcement.colour_scheme,
            )
            # py-opendisplay's fit_image pads with an RGB tuple, so the input
            # must be in RGB mode (or a mode that can accept tuple fill).
            if img.mode != "RGB":
                img = img.convert("RGB")
            data = image_to_1bpp(img, announcement.width, announcement.height)
            logger.info("Encoded image: %d bytes", len(data))
            return data
        except Exception:
            logger.exception("Failed to load/encode image")
            return None


async def run(
    port: int,
    poll_interval: int,
    mdns: bool,
    image_path: Path,
) -> None:
    provider = CurrentImageProvider(image_path)

    server = OpenDisplayServer(
        port=port,
        image_provider=provider,
        poll_interval=poll_interval,
        mdns=mdns,
    )

    await server.start()
    logger.info(
        "OpenDisplay server running on port %d, poll_interval=%ds, mdns=%s, image=%s",
        server.actual_port,
        poll_interval,
        mdns,
        image_path,
    )

    # Wait forever, until SIGTERM/SIGINT
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            # Windows doesn't support add_signal_handler; not relevant here.
            pass

    try:
        await stop_event.wait()
    finally:
        logger.info("Shutting down")
        await server.stop()


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="OpenDisplay WiFi server for e-newspaper")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--poll-interval", type=int, default=DEFAULT_POLL_INTERVAL)
    parser.add_argument("--no-mdns", action="store_true", help="Disable mDNS advertising")
    parser.add_argument("--image", type=Path, default=CURRENT_IMAGE)
    args = parser.parse_args(argv[1:])

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    asyncio.run(
        run(
            port=args.port,
            poll_interval=args.poll_interval,
            mdns=not args.no_mdns,
            image_path=args.image,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
