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
import shutil
import signal
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

from PIL import Image

from opendisplay.wifi.imaging import image_to_1bpp
from opendisplay.wifi.protocol import DisplayAnnouncement
from opendisplay.wifi.server import OpenDisplayServer

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CURRENT_IMAGE = PROJECT_ROOT / "images" / "current.png"

# Heartbeat file: the provider touches this on every request from the
# display, writing the current ISO timestamp. The watchdog checks the
# mtime/contents to decide whether the pipeline is healthy. This is the
# right health signal because ICMP to the display can't cross the TP-Link
# bridge's NAT but the display IS polling outbound, which the server sees.
LAST_POLL_PATH = PROJECT_ROOT / "images" / "last-poll.txt"

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
    passing the display's announcement (resolution + colour scheme). The
    server itself deduplicates by SHA-256, so we don't need to cache here.

    This provider ships the raw PNG bytes on the wire (not the 1bpp dithered
    output py-opendisplay produces by default). A matching patch in the
    OpenDisplay Android client tries BitmapFactory.decodeByteArray() on the
    image_data before falling back to the library's bit-unpacking decoder.
    Sending a real PNG gives Android a grayscale Bitmap which, combined with
    a GC16 invalidate mode, lets the Avalue EPDC render actual gray levels
    instead of just stippled 1-bit output.
    """

    def __init__(self, image_path: Path) -> None:
        self.image_path = image_path

    def _touch_heartbeat(self) -> None:
        """Write current wall-clock time to LAST_POLL_PATH. Never raises
        — heartbeat failure must not break the image path."""
        try:
            LAST_POLL_PATH.parent.mkdir(parents=True, exist_ok=True)
            LAST_POLL_PATH.write_text(
                datetime.now().astimezone().isoformat(timespec="seconds")
            )
        except Exception:
            logger.exception("Failed to write heartbeat file")

    def __call__(self, announcement: Optional[DisplayAnnouncement]) -> Optional[bytes]:
        # Touch the heartbeat on every provider invocation (which equals
        # every poll from the display, whether or not we end up sending
        # a new image). This is what the watchdog reads.
        self._touch_heartbeat()

        if announcement is None:
            logger.warning("Provider called before announcement received; no image")
            return None

        if not self.image_path.exists():
            logger.warning("Image file %s does not exist", self.image_path)
            return None

        try:
            data = self.image_path.read_bytes()
            # Sanity-check PNG magic number so we don't ship garbage if the
            # file is being rewritten as we read it.
            if not data.startswith(b"\x89PNG"):
                logger.warning(
                    "%s does not look like a PNG (first bytes %r); falling "
                    "back to 1bpp encoding",
                    self.image_path,
                    data[:8],
                )
                img = Image.open(self.image_path)
                if img.mode != "RGB":
                    img = img.convert("RGB")
                data = image_to_1bpp(img, announcement.width, announcement.height)
                logger.info("Encoded 1bpp fallback: %d bytes", len(data))
                return data

            logger.info(
                "Sending raw PNG %s (%d bytes) to display %dx%d scheme=%d",
                self.image_path.name,
                len(data),
                announcement.width,
                announcement.height,
                announcement.colour_scheme,
            )
            return data
        except Exception:
            logger.exception("Failed to load image")
            return None


def _start_dns_sd(port: int) -> Optional[subprocess.Popen]:
    """
    Advertise the OpenDisplay service using macOS's native dns-sd tool.

    On macOS, python-zeroconf (used by py-opendisplay) conflicts with the
    system mDNSResponder for UDP port 5353 and fails silently. The
    workaround is to shell out to /usr/bin/dns-sd which uses the system
    responder directly and plays nicely with everything else on the LAN.

    Returns the Popen handle (so we can terminate it on shutdown), or
    None if dns-sd isn't available (e.g. on Linux).
    """
    dns_sd = shutil.which("dns-sd")
    if not dns_sd:
        logger.warning("/usr/bin/dns-sd not found; mDNS advertising skipped")
        return None

    # dns-sd -R <name> <type> <domain> <port>
    # It runs in the foreground until killed. We drop its output to DEVNULL
    # but still log that we started it.
    proc = subprocess.Popen(
        [
            dns_sd,
            "-R",
            "OpenDisplay E-Newspaper",
            "_opendisplay._tcp",
            ".",
            str(port),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    logger.info("mDNS: advertising _opendisplay._tcp port %d via dns-sd (pid %d)", port, proc.pid)
    return proc


async def run(
    port: int,
    poll_interval: int,
    mdns: bool,
    image_path: Path,
) -> None:
    provider = CurrentImageProvider(image_path)

    # We always pass mdns=False to py-opendisplay because its python-zeroconf
    # backend is broken on macOS. When the caller asked for mDNS, we spawn
    # /usr/bin/dns-sd instead.
    server = OpenDisplayServer(
        port=port,
        image_provider=provider,
        poll_interval=poll_interval,
        mdns=False,
    )

    await server.start()

    dns_sd_proc = _start_dns_sd(server.actual_port) if mdns else None

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
        if dns_sd_proc is not None:
            dns_sd_proc.terminate()
            try:
                dns_sd_proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                dns_sd_proc.kill()
        await server.stop()


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="OpenDisplay WiFi server for e-newspaper")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--poll-interval", type=int, default=DEFAULT_POLL_INTERVAL)
    parser.add_argument("--no-mdns", action="store_true", help="Disable mDNS advertising")
    parser.add_argument("--image", type=Path, default=CURRENT_IMAGE)
    args = parser.parse_args(argv[1:])

    # Explicit StreamHandler with line_buffering forced on — Python's -u
    # flag alone doesn't reliably unbuffer stderr under launchd. Reconfigure
    # stdout/stderr too so any stray print() also flushes per-line.
    try:
        sys.stdout.reconfigure(line_buffering=True)  # type: ignore[attr-defined]
        sys.stderr.reconfigure(line_buffering=True)  # type: ignore[attr-defined]
    except Exception:
        pass

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    )
    logging.basicConfig(
        level=logging.INFO,
        handlers=[handler],
        force=True,
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
