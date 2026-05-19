"""
Auto-recovery daemon for the e-ink display.

Listens on port 9999 for the display's persistent reverse shell connection
(/data/local/tmp/display_remote.sh dials home every 30 seconds). On each
dial-in, checks images/last-poll.txt:

  - Fresh heartbeat (< 6 min): close the connection, no action.
  - Stale heartbeat (>= 6 min): send the recovery recipe (eth0 bounce +
    OpenDisplay app force-stop + restart) and log it.
  - Very stale (> 30 min) with recent fix attempts already made: escalate
    to a full reboot of the display.

This is the missing automatic-recovery half of the existing watchdog.
src/watchdog.py only detects and alerts; this one actually fixes.

Cooldowns prevent hammering during real outages, and a state file at
images/auto-recover-state.json tracks recent action timestamps so the
daemon survives restarts.

Conflicts: only one process can bind port 9999. To use tools/remote_shell.py
or tools/fix_display.py interactively, stop this daemon first
(`launchctl unload ~/Library/LaunchAgents/com.e-newspaper.auto-recover.plist`).
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

# Thresholds in seconds.
# Display polls every 300s; 360 gives us a one-poll buffer before we react.
STALE_THRESHOLD = 360
FIX_COOLDOWN = 300
# Escalate to reboot once a soft-fix budget has been exhausted at this age.
REBOOT_STALE_THRESHOLD = 1800
REBOOT_COOLDOWN = 1800

# Mirrors tools/fix_display.py's proven recipe. Backgrounded with nohup so
# the script keeps running after the reverse shell closes.
FIX_RECIPE = (
    "nohup sh -c '"
    "sleep 1; ifconfig eth0 down; sleep 3; ifconfig eth0 up; sleep 3; "
    "netcfg eth0 dhcp; sleep 10; "
    "am broadcast -a android.net.conn.CONNECTIVITY_CHANGE; sleep 2; "
    "am force-stop org.opendisplay.android; sleep 1; "
    "am start -n org.opendisplay.android/.MainActivity"
    "' >/dev/null 2>&1 &\n"
)

REBOOT_RECIPE = "nohup sh -c 'sleep 1; reboot' >/dev/null 2>&1 &\n"

logger = logging.getLogger("auto_recover")


def load_state() -> dict:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text())
        except Exception:
            logger.exception("state file corrupt, starting fresh")
    return {"last_fix_at": None, "last_reboot_at": None}


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


def decide_action(state: dict, now: float) -> str | None:
    """Return 'fix', 'reboot', or None based on heartbeat age + cooldowns."""
    age = heartbeat_age()
    if age is None or age < STALE_THRESHOLD:
        return None

    if age > REBOOT_STALE_THRESHOLD:
        last_reboot = state.get("last_reboot_at") or 0
        if now - last_reboot > REBOOT_COOLDOWN:
            return "reboot"
        # In reboot cooldown – fall through to fix if that's ready
    last_fix = state.get("last_fix_at") or 0
    if now - last_fix > FIX_COOLDOWN:
        return "fix"
    return None


def serve_one(conn, addr, state) -> None:
    """Handle a single reverse-shell dial-in."""
    conn.settimeout(15)
    age = heartbeat_age()
    age_str = f"{age:.0f}s" if age is not None else "none"
    now = time.time()
    action = decide_action(state, now)

    if action is None:
        logger.info("dial-in %s: age=%s, no action", addr[0], age_str)
        return

    if action == "fix":
        logger.warning("dial-in %s: age=%s, sending FIX", addr[0], age_str)
        conn.sendall(FIX_RECIPE.encode())
        state["last_fix_at"] = now
        save_state(state)
    elif action == "reboot":
        logger.warning("dial-in %s: age=%s, sending REBOOT", addr[0], age_str)
        conn.sendall(REBOOT_RECIPE.encode())
        state["last_reboot_at"] = now
        save_state(state)


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
            serve_one(conn, addr, state)
        except Exception:
            logger.exception("error handling dial-in")
        finally:
            try:
                conn.close()
            except Exception:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
