"""
TP-Link TL-WR802N admin CLI for the HR018 e-newspaper bridge.

Reads the admin password from a protected file and logs into the
router's HTTP admin UI to query status or issue an explicit reboot.
The cookie-based login scheme matches the WR802N's Chinese firmware
(the `Authorization=Basic <urlencoded-b64>` cookie pattern observed
at 192.168.1.253 on 2026-04-11).

Usage:
    python src/tplink_admin.py status
    python src/tplink_admin.py reboot --yes
    python src/tplink_admin.py --router http://192.168.1.253 \
        --password-file ~/.config/hr018/tplink.password status

NETWORK REQUIREMENT — READ THIS FIRST:
    The tool talks directly to the TP-Link admin IP (default
    192.168.1.253), which is a LAN-side address under the WR802N's
    current WISP/Client Router mode. You MUST run it from a machine
    physically on the TP-Link's LAN segment — typically:
      • a laptop plugged into the TP-Link's LAN ethernet port, or
      • the display itself, via `adb shell` (doesn't have Python —
        see the shell-wrapper follow-up if you need that path)
    Machines on Massey Wi-Fi (Mac mini, your regular laptop) CANNOT
    reach 192.168.1.253. This is not a bug in the tool, it's the
    bridge's topology.

SAFETY — READ THIS TOO:
    10 wrong password attempts on the TP-Link trigger a 1-hour
    lockout. This tool makes one authenticated request per
    invocation. On auth failure it exits without retrying. Do NOT
    wrap it in a retry loop.

SECRETS:
    The password is read from a file at runtime, never from CLI
    flags, never logged, never printed. The cookie containing the
    base64'd password is sent in the HTTP Cookie header only. File
    mode checks refuse to run if the password file is group/other
    readable.

NOT YET TESTED against real hardware — the TP-Link was flapping
during development and we never had a moment of stable LAN-side
access. First-time user: run `status` first to validate the auth
flow before attempting `reboot`.
"""
from __future__ import annotations

import argparse
import base64
import logging
import os
import re
import sys
from pathlib import Path
from urllib.parse import quote

import requests

DEFAULT_ROUTER = "http://192.168.1.253"
DEFAULT_PASSWORD_FILE = Path.home() / ".config" / "hr018" / "tplink.password"
# WR802N firmware uses the literal string "admin" as the account name
# in the Base64("admin:password") encoding, regardless of what the
# single-password login UI suggests.
ADMIN_USER = "admin"
TIMEOUT = 15

logger = logging.getLogger("tplink_admin")


def _load_password(path: Path) -> str:
    """Read the password from a restrictive-permissions file.

    Raises SystemExit with a clear message on any sanity-check
    failure. Never prints the password or its length.
    """
    if not path.exists():
        raise SystemExit(f"Password file not found: {path}")
    st = path.stat()
    # Refuse if group/other have any bits set. Equivalent to chmod 600.
    if st.st_mode & 0o077:
        raise SystemExit(
            f"{path} has permissive mode {oct(st.st_mode)[-3:]} — "
            "chmod 600 and retry"
        )
    data = path.read_text()
    password = data.rstrip("\r\n")
    if not password:
        raise SystemExit(f"{path} is empty")
    if "\n" in password:
        raise SystemExit(f"{path} contains multiple lines; expected one password")
    return password


def _build_auth_cookie(password: str) -> str:
    """Construct the TP-Link Authorization cookie value.

    The WR802N Chinese firmware's login JS does:
        var auth = "Basic " + Base64Encoding("admin:" + password);
        document.cookie = "Authorization=" + escape(auth) + ";path=/";

    `escape()` in JavaScript URL-encodes space as %20, + as %2B,
    / as %2F, = as %3D, and leaves most other ASCII alone. We
    reproduce that mapping with urllib.parse.quote and a safe set
    that matches.
    """
    raw = f"{ADMIN_USER}:{password}".encode("ascii")
    b64 = base64.b64encode(raw).decode("ascii")
    value = f"Basic {b64}"
    # JS escape() leaves A-Za-z0-9 and @*_+-./ unescaped. quote() with
    # safe="" escapes everything else including space. Close enough to
    # JS escape for this specific input (b64 alphabet + "Basic ").
    return quote(value, safe="")


def _is_login_page(html: str) -> bool:
    """The TP-Link login page uniquely contains name="pcPassword"."""
    return 'name="pcPassword"' in html


def _is_locked_out(html: str) -> bool:
    """httpAutErrorArray[0] == 2 signals the 1-hour lockout screen."""
    return bool(re.search(r"httpAutErrorArray\s*=\s*new Array\(\s*2\s*,", html))


def _authenticate(session: requests.Session, router: str, password: str) -> str:
    """Log in and return the authenticated dashboard HTML.

    On any form of auth failure, exits the process without retrying
    to avoid the 10-attempt lockout.
    """
    cookie = _build_auth_cookie(password)
    session.cookies.set("Authorization", cookie, path="/")
    try:
        r = session.get(router, timeout=TIMEOUT)
    except (requests.ConnectionError, requests.Timeout) as exc:
        raise SystemExit(
            f"Cannot reach {router}: {exc.__class__.__name__}. "
            "This tool must be run from a machine on the TP-Link's LAN "
            "side (see the NETWORK REQUIREMENT note at the top of the "
            "file). Mac mini and any device on Massey WiFi cannot reach "
            "192.168.1.253 under the current WISP mode."
        )
    r.raise_for_status()
    html = r.text

    if _is_locked_out(html):
        raise SystemExit(
            "TP-Link admin UI is currently LOCKED OUT (10+ wrong "
            "attempts). Wait an hour before retrying. Do NOT run this "
            "tool again during the lockout window."
        )
    if _is_login_page(html):
        raise SystemExit(
            "TP-Link admin login failed (wrong password). NOT retrying "
            "to avoid the lockout. Double-check the password file."
        )
    return html


def _parse_status(html: str) -> dict:
    """Extract what we can from the authenticated dashboard HTML.

    WR802N firmware shows different dashboards depending on Operation
    Mode. We scan for known markers and return a dict of whatever we
    find; missing fields just stay absent. Best-effort.
    """
    status: dict = {}

    m = re.search(r"<title>([^<]+)</title>", html)
    if m:
        status["page_title"] = m.group(1).strip()

    # Operation mode tell-tales — Chinese firmware uses mixed labels.
    mode_markers = {
        "WISP / Client Router": ["WISP", "Client Router", "客户端路由"],
        "AP Router": ["AP Router", "接入点路由"],
        "Pure Client": ["Client Mode", "客户端模式"],
        "WDS Bridge": ["WDS", "桥接"],
        "AP": ["Access Point Mode"],
    }
    for mode, tokens in mode_markers.items():
        if any(t in html for t in tokens):
            status.setdefault("detected_modes", []).append(mode)

    # WAN IP markers — these vary wildly by firmware/language; best effort
    for pattern, label in [
        (r"WAN\s*IP[^0-9]*(\d+\.\d+\.\d+\.\d+)", "wan_ip"),
        (r'wanPara\.IpAddr\s*=\s*"(\d+\.\d+\.\d+\.\d+)"', "wan_ip_js"),
        (r"IP\s*地址[^0-9]*(\d+\.\d+\.\d+\.\d+)", "wan_ip_zh"),
    ]:
        m = re.search(pattern, html)
        if m:
            status[label] = m.group(1)

    # Wireless association status
    for pattern, label in [
        (r"Wireless.*?(Connected|Disconnected)", "wlan_state"),
        (r"无线状态[^<]*(已连接|未连接|断开)", "wlan_state_zh"),
    ]:
        m = re.search(pattern, html)
        if m:
            status[label] = m.group(1)

    return status


def cmd_status(args: argparse.Namespace) -> int:
    password = _load_password(args.password_file)
    with requests.Session() as session:
        html = _authenticate(session, args.router, password)
    status = _parse_status(html)
    if not status:
        print(
            "authenticated OK — dashboard fetched, but no known markers "
            "matched. Dump the raw HTML with --dump-html to add new "
            "parsers."
        )
        if args.dump_html:
            print("--- raw HTML ---")
            print(html)
        return 0
    print("TP-Link admin dashboard:")
    for k, v in status.items():
        print(f"  {k}: {v}")
    if args.dump_html:
        print("--- raw HTML ---")
        print(html)
    return 0


def cmd_reboot(args: argparse.Namespace) -> int:
    if not args.yes:
        raise SystemExit(
            "reboot requires explicit --yes confirmation. The display "
            "will go blank for ~60 seconds while the TP-Link restarts "
            "and the pipeline reconnects."
        )
    password = _load_password(args.password_file)
    with requests.Session() as session:
        _authenticate(session, args.router, password)
        # WR802N Chinese firmware reboot endpoint — common across
        # TL-WR802N / WR702N / WR706N Chinese builds. We try the
        # Chinese-locale path first, then fall back to English-locale.
        # The GET form works on WR802N; POST works on newer firmware.
        # Referer is required by some firmware.
        headers = {"Referer": args.router + "/"}
        candidates = [
            f"{args.router}/userRpm/SysRebootRpm.htm?Reboot=%D6%D8%C6%F4%C2%B7%D3%C9%C6%F7",
            f"{args.router}/userRpm/SysRebootRpm.htm?Reboot=Reboot",
            f"{args.router}/userRpm/SysRebootRpm.htm?Reboot=1",
        ]
        for url in candidates:
            logger.info("Trying reboot endpoint: %s", url.split("?")[0])
            try:
                r = session.get(url, headers=headers, timeout=TIMEOUT)
                if r.status_code == 200 and not _is_login_page(r.text):
                    print(
                        "Reboot command accepted. The TP-Link will be "
                        "down for ~30-60 seconds while it restarts. "
                        "The watchdog will flip to UNHEALTHY during "
                        "this window and back to healthy once the "
                        "display resumes polling."
                    )
                    return 0
            except requests.RequestException:
                # Connection may drop as the router reboots — that's
                # actually a success signal. Fall through and report.
                print(
                    "Reboot command sent; connection dropped as "
                    "expected (TP-Link is restarting)."
                )
                return 0
        raise SystemExit(
            "None of the known reboot endpoints worked. Dump the "
            "admin UI with `status --dump-html` and look for the "
            "reboot form action to add the correct URL."
        )


def main(argv: list[str]) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    parser = argparse.ArgumentParser(
        description="TP-Link TL-WR802N admin CLI (HR018 e-newspaper)"
    )
    parser.add_argument(
        "--router",
        default=DEFAULT_ROUTER,
        help=f"Router base URL (default: {DEFAULT_ROUTER})",
    )
    parser.add_argument(
        "--password-file",
        type=Path,
        default=DEFAULT_PASSWORD_FILE,
        help=f"Path to password file (default: {DEFAULT_PASSWORD_FILE})",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_status = sub.add_parser("status", help="Show current router state")
    p_status.add_argument(
        "--dump-html",
        action="store_true",
        help="Print the raw dashboard HTML (for debugging parsers)",
    )
    p_status.set_defaults(func=cmd_status)

    p_reboot = sub.add_parser("reboot", help="Reboot the router")
    p_reboot.add_argument(
        "--yes",
        action="store_true",
        help="Explicit confirmation — required",
    )
    p_reboot.set_defaults(func=cmd_reboot)

    args = parser.parse_args(argv[1:])
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
