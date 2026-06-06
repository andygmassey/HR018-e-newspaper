"""
Display reachability monitor (detection / alerting only).

Listens on port 9999 for the display's persistent reverse shell connection
(/data/local/tmp/display_remote.sh dials home every 30 seconds). On each
dial-in it reads images/last-poll.txt and logs whether the display's
heartbeat is fresh or stale.

It does NOT send recovery commands. Active recovery is owned by the
display's own app_watchdog.sh (see deploy/display/), which detects the
app-layer ENETUNREACH condition locally via dumpsys and escalates to a
full reboot. That on-device design is strictly better because it works
even when every remote channel is dead, which is the situation that
matters most.

History: this daemon used to send an eth0-bounce FIX and a REBOOT on stale
heartbeats. On 2026-06-06 that recipe caused an outage: repeated eth0
bounces churned the network until the reverse shell itself died, after
which nothing could recover the display remotely and it needed a physical
cold boot. Recovery was moved on-device; this is now detection only.

To take over the reverse-shell channel for manual OTA work, stop this
daemon first (it owns port 9999):
  launchctl unload ~/Library/LaunchAgents/com.e-newspaper.auto-recover.plist
"""
from __future__ import annotations

import logging
import socket
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LAST_POLL = PROJECT_ROOT / "images" / "last-poll.txt"

PORT = 9999

# Display polls every 300s; 360 gives a one-poll buffer before we call it stale.
STALE_THRESHOLD = 360

logger = logging.getLogger("auto_recover")


def heartbeat_age() -> float | None:
    """Seconds since the display last polled the server, or None if unknown."""
    if not LAST_POLL.exists():
        return None
    try:
        ts = datetime.fromisoformat(LAST_POLL.read_text().strip())
    except Exception:
        logger.exception("failed to parse %s", LAST_POLL)
        return None
    return (datetime.now(ts.tzinfo) - ts).total_seconds()


def serve_one(conn, addr) -> None:
    """Handle a single reverse-shell dial-in.

    Detection / alerting ONLY. Active recovery is now owned by the display's
    own app_watchdog.sh (see deploy/display/), which detects the app-layer
    ENETUNREACH condition via dumpsys and escalates to a full local reboot.
    Sending FIX/REBOOT from here is both redundant and was the cause of the
    2026-06-06 outage: the FIX recipe's repeated eth0 bounce churned the
    network until even the reverse shell died, leaving nothing able to
    recover it remotely.

    We keep listening so dial-ins are visible (proof the reverse shell is
    alive) and so a stale heartbeat is logged loudly for the human, but we
    do not act. To take over the channel for manual OTA work, unload this
    launch agent and run tools/remote_shell.py.
    """
    conn.settimeout(15)
    age = heartbeat_age()
    age_str = f"{age:.0f}s" if age is not None else "none"

    if age is not None and age >= STALE_THRESHOLD:
        logger.warning(
            "dial-in %s: heartbeat stale (age=%s); display-side app_watchdog "
            "should be recovering; not intervening from here", addr[0], age_str
        )
    else:
        logger.info("dial-in %s: age=%s, healthy", addr[0], age_str)


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", PORT))
    srv.listen(4)
    logger.info("auto_recover listening on port %d", PORT)

    while True:
        try:
            conn, addr = srv.accept()
        except Exception:
            logger.exception("accept failed")
            continue
        try:
            serve_one(conn, addr)
        except Exception:
            logger.exception("error handling dial-in")
        finally:
            try:
                conn.close()
            except Exception:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
