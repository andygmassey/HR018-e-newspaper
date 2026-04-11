"""
e-newspaper display watchdog.

Health signal: the OpenDisplay server writes the current wall-clock time
to images/last-poll.txt on every poll from the display. The watchdog
checks how long ago that happened — if it's too long, the display has
stopped polling and the pipeline is broken somewhere (TP-Link bridge,
display crash, Wi-Fi association, etc.).

This is the right health signal because ICMP to the display can't cross
the TP-Link bridge's NAT (the display is on the LAN side, the Mac mini
is on the WAN side), but the display polling out IS observable on the
server even through NAT.

State file schema (images/status.json):
    {
        "last_poll": "2026-04-11T20:15:03+08:00",     # from heartbeat file
        "age_seconds": 42,
        "threshold_seconds": 900,
        "healthy": true,
        "consecutive_failures": 0,
        "last_checked": "2026-04-11T20:15:45+08:00",
        "last_state_change": "..."
    }

Exit codes:
    0 — healthy
    2 — unhealthy (stale or missing heartbeat)
    1 — configuration or runtime error

Detection only, not auto-recovery. When the pipeline breaks, the only
recovery path is physical (power-cycle the TP-Link or replace the
bridge), so the watchdog's job is simply to make sure you find out fast.
"""
from __future__ import annotations

import json
import logging
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / "config.json"
STATUS_PATH = PROJECT_ROOT / "images" / "status.json"
HEARTBEAT_PATH = PROJECT_ROOT / "images" / "last-poll.txt"

# Display polls every 300s by default. After 3 missed polls (15 min) we
# consider the pipeline broken. Override in config.json.
DEFAULT_THRESHOLD = 900

logger = logging.getLogger("watchdog")


def _load_config() -> dict:
    if not CONFIG_PATH.exists():
        raise RuntimeError(f"{CONFIG_PATH} not found")
    return json.loads(CONFIG_PATH.read_text())


def _load_status() -> dict:
    if STATUS_PATH.exists():
        try:
            return json.loads(STATUS_PATH.read_text())
        except json.JSONDecodeError:
            return {}
    return {}


def _save_status(status: dict) -> None:
    STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATUS_PATH.write_text(json.dumps(status, indent=2))


def _read_heartbeat() -> datetime | None:
    """Parse the heartbeat file. Returns None if missing or unreadable."""
    if not HEARTBEAT_PATH.exists():
        return None
    try:
        text = HEARTBEAT_PATH.read_text().strip()
        return datetime.fromisoformat(text)
    except (ValueError, OSError):
        return None


def main(argv: list[str]) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    try:
        config = _load_config()
    except Exception as exc:
        logger.error("Failed to load config: %s", exc)
        return 1

    wd_cfg = config.get("watchdog") or {}
    threshold = int(wd_cfg.get("threshold_seconds", DEFAULT_THRESHOLD))

    status = _load_status()
    now = datetime.now().astimezone()
    heartbeat = _read_heartbeat()

    prev_healthy = status.get("healthy")
    prev_failures = int(status.get("consecutive_failures", 0))

    if heartbeat is None:
        healthy = False
        age_seconds = None
        last_poll_iso = None
    else:
        # Normalise aware/naive mismatch
        if heartbeat.tzinfo is None:
            heartbeat = heartbeat.astimezone()
        age_seconds = int((now - heartbeat).total_seconds())
        healthy = age_seconds <= threshold
        last_poll_iso = heartbeat.isoformat(timespec="seconds")

    new_status: dict = {
        "last_poll": last_poll_iso,
        "age_seconds": age_seconds,
        "threshold_seconds": threshold,
        "healthy": healthy,
        "last_checked": now.isoformat(timespec="seconds"),
    }

    if healthy:
        new_status["consecutive_failures"] = 0
        new_status["last_state_change"] = (
            now.isoformat(timespec="seconds")
            if prev_healthy is False
            else status.get("last_state_change", now.isoformat(timespec="seconds"))
        )
        if prev_healthy is False:
            logger.warning(
                "display RECOVERED — last poll %s (%ds ago), was failing for %d checks",
                last_poll_iso,
                age_seconds,
                prev_failures,
            )
        else:
            logger.info(
                "healthy — last poll %s (%ds ago)", last_poll_iso, age_seconds
            )
    else:
        new_status["consecutive_failures"] = prev_failures + 1
        new_status["last_state_change"] = (
            now.isoformat(timespec="seconds")
            if prev_healthy is True
            else status.get("last_state_change", now.isoformat(timespec="seconds"))
        )
        if heartbeat is None:
            reason = "no heartbeat file — server has never been polled"
        else:
            reason = f"last poll {last_poll_iso} ({age_seconds}s ago, threshold {threshold}s)"

        if prev_healthy is True or prev_healthy is None:
            logger.error("display UNHEALTHY — %s", reason)
        elif new_status["consecutive_failures"] == 3:
            logger.error(
                "display STILL UNHEALTHY after 3 consecutive checks — %s. "
                "Physical intervention likely needed (TP-Link bridge).",
                reason,
            )
        else:
            logger.warning(
                "display still unhealthy (%d consecutive failures) — %s",
                new_status["consecutive_failures"],
                reason,
            )

    _save_status(new_status)
    return 0 if healthy else 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
