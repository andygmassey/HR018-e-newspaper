"""
Auto-recovery daemon for the e-ink display (heartbeat-triggered).

Listens on port 9999 for the display's persistent reverse-shell dial-in
(/data/local/tmp/display_remote.sh dials home every 30 seconds) and uses
images/last-poll.txt (the server heartbeat) as the source of truth for
whether the display is actually serving.

WHY HEARTBEAT, NOT AN ON-DEVICE CHECK
The failure mode (the "ENETUNREACH" bug, 2026-06): after a network blip the
OpenDisplay app's connect() fails with ENETUNREACH even though Android's
ConnectivityManager reports a healthy, validated default network. dumpsys on
the display therefore looks fine during the failure, so the display cannot
reliably self-detect it. The Mac mini heartbeat is the only trustworthy
signal: if last-poll.txt stops advancing, the display has stopped serving,
full stop.

RECOVERY
The only action that reliably clears the failure is: bounce eth0 (forces a
fresh ConnectivityManager network agent), then restart the OpenDisplay app
so it binds to the new agent. A reboot does NOT fix it (verified: a full
cold boot came up still broken).

CASCADE SAFETY (the hard-won part)
Doing the eth0 bounce repeatedly is harmful: it churns the network through
agent ids and can wedge eth0 entirely, killing even the reverse shell
(that is what turned the 2026-06-06 incident into a 30-hour outage). So:
  - After any recovery we wait COOLDOWN (15 min) before acting again. The
    bounce takes ~25s and a successful poll lands within one poll interval,
    so 15 min is ample to confirm success and reset.
  - After CAP (3) consecutive failed attempts we stop hammering and back off
    to BACKOFF (1 hour) between attempts, logging CRITICAL so a human looks.
  - Any fresh heartbeat resets the attempt counter.
This bounds eth0 bounces to at most one per 15 min (then one per hour),
which keeps the reverse shell alive so we never lose the channel.

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
COOLDOWN = 900             # 15 min between recovery attempts
CAP = 3                    # consecutive attempts before backing off
BACKOFF = 3600             # 1 hour between attempts once capped

# eth0 bounce + ConnectivityManager kick + app restart, backgrounded with
# nohup so it survives the reverse shell dropping when eth0 goes down.
# Mirrors tools/fix_display.py.
FIX_RECIPE = (
    "nohup sh -c '"
    "sleep 1; ifconfig eth0 down; sleep 3; ifconfig eth0 up; sleep 3; "
    "netcfg eth0 dhcp; sleep 10; "
    "am broadcast -a android.net.conn.CONNECTIVITY_CHANGE; sleep 2; "
    "am force-stop org.opendisplay.android; sleep 1; "
    "am start -n org.opendisplay.android/.MainActivity"
    "' >/dev/null 2>&1 &\n"
)

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

    level = logging.CRITICAL if attempts >= CAP else logging.WARNING
    logger.log(level, "dial-in %s: heartbeat STALE (age=%s), sending recovery "
               "(attempt %d)", addr[0], age_str, attempts + 1)
    conn.sendall(FIX_RECIPE.encode())
    state["last_recovery_at"] = now
    state["consecutive_attempts"] = attempts + 1
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
