"""
Auto-recovery daemon for the e-ink display (heartbeat-triggered).

Listens on port 9999 for the display's persistent reverse-shell dial-in
(/data/local/tmp/display_remote.sh dials home every 30 seconds) and uses
images/last-poll.txt (the server heartbeat) as the source of truth for
whether the display is actually serving.

WHY HEARTBEAT, NOT AN ON-DEVICE CHECK
The display cannot reliably self-detect the failure: during it, the
display's own `dumpsys connectivity` can report a healthy validated network
while the app still cannot connect, and `tp_watchdog.sh`'s root-level `nc`
also passes (root networking works while only apps are broken). The Mac
mini heartbeat is the only trustworthy signal: if last-poll.txt stops
advancing, the display has stopped serving.

RECOVERY: REBOOT, NOTHING CLEVER
The failure has shown up in two forms: a valid Ethernet network agent the
app cannot bind (ENETUNREACH), and no Ethernet network agent at all
(`Active default network: none`). A full reboot recreates the network
stack from scratch and has reliably cleared both in practice. The eth0
bounce that earlier versions used was a mistake: it fixes only the first
form, can CREATE the second by churning network agents, and fights
tp_watchdog.sh (which also touches eth0 / the bridge). So recovery here is
simply: reboot the display.

CASCADE SAFETY
A reboot is safe to repeat (unlike eth0 bouncing, which wedged the network
and caused the 2026-06-06 30-hour outage). Still, we wait COOLDOWN (10 min)
between reboots so a display that boots into a bad state is not rebooted in
a tight loop, and after CAP reboots we back off to BACKOFF (1 hour) and log
CRITICAL so a human looks. Any fresh heartbeat resets the counter.

State (attempt count + last action time) is persisted to
images/auto-recover-state.json so restarts do not reset the backoff.

Conflicts: only one process can bind port 9999. To use tools/remote_shell.py
or tools/fix_display.py interactively, stop this daemon first
(launchctl unload ~/Library/LaunchAgents/com.e-newspaper.auto-recover.plist).
"""
from __future__ import annotations

import json
import logging
import socket
import time
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LAST_POLL = PROJECT_ROOT / "images" / "last-poll.txt"
STATE_PATH = PROJECT_ROOT / "images" / "auto-recover-state.json"

PORT = 9999

# All seconds.
STALE_THRESHOLD = 720      # 12 min: ~2 missed 5-min polls + buffer
COOLDOWN = 600             # 10 min between reboots
CAP = 4                    # reboots before backing off
BACKOFF = 3600             # 1 hour between reboots once capped

# Recovery: reboot the display. Backgrounded with nohup so it fires after
# the reverse shell closes.
REBOOT_RECIPE = "nohup sh -c 'sleep 1; reboot' >/dev/null 2>&1 &\n"

logger = logging.getLogger("auto_recover")


def load_state() -> dict:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text())
        except Exception:
            logger.exception("state file corrupt, starting fresh")
    return {"last_recovery_at": 0, "consecutive_attempts": 0}


def save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2))


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


def serve_one(conn, addr, state) -> bool:
    """Handle one dial-in. Returns True if state changed (caller saves)."""
    conn.settimeout(15)
    age = heartbeat_age()
    age_str = f"{age:.0f}s" if age is not None else "none"
    now = time.time()

    if age is None or age < STALE_THRESHOLD:
        # Healthy. Reset the attempt counter if we'd been failing.
        if state.get("consecutive_attempts", 0) > 0:
            logger.info("dial-in %s: heartbeat fresh (age=%s), recovered, "
                        "resetting attempts", addr[0], age_str)
            state["consecutive_attempts"] = 0
            return True
        logger.info("dial-in %s: age=%s, healthy", addr[0], age_str)
        return False

    # Stale. Decide whether we are allowed to act yet.
    attempts = state.get("consecutive_attempts", 0)
    since_last = now - state.get("last_recovery_at", 0)
    wait = BACKOFF if attempts >= CAP else COOLDOWN

    if since_last < wait:
        logger.warning("dial-in %s: heartbeat STALE (age=%s), attempt %d, "
                       "in cooldown (%.0fs/%ds)", addr[0], age_str,
                       attempts, since_last, wait)
        return False

    next_attempt = attempts + 1
    level = logging.CRITICAL if attempts >= CAP else logging.WARNING
    logger.log(level, "dial-in %s: heartbeat STALE (age=%s), rebooting display "
               "(attempt %d)", addr[0], age_str, next_attempt)
    conn.sendall(REBOOT_RECIPE.encode())
    state["last_recovery_at"] = now
    state["consecutive_attempts"] = next_attempt
    return True


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
            state = load_state()
            if serve_one(conn, addr, state):
                save_state(state)
        except Exception:
            logger.exception("error handling dial-in")
        finally:
            try:
                conn.close()
            except Exception:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
