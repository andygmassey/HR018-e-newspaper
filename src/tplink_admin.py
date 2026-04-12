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


def _parse_js_array(html: str, name: str) -> list:
    """Extract a `var <name> = new Array(...);` declaration from the page.

    WR802N's admin pages stuff all the useful state into top-of-HTML JS
    arrays (statusPara, lanPara, wlanPara, wanPara, etc). Values are a
    mix of bare integers and double-quoted strings, comma separated,
    with a trailing ", 0, 0 );" sentinel. Returns a python list of
    strings and ints, without the trailing sentinel.
    """
    # Non-greedy to stop at the first closing paren
    m = re.search(
        rf"var\s+{re.escape(name)}\s*=\s*new\s+Array\s*\((.*?)\)\s*;",
        html,
        re.DOTALL,
    )
    if not m:
        return []
    body = m.group(1)
    # Tokenise: "quoted strings" or bare tokens separated by commas
    tokens = re.findall(r'"([^"]*)"|([^,\s]+)', body)
    values: list = []
    for q, b in tokens:
        if q or not b:
            values.append(q)
        else:
            try:
                values.append(int(b))
            except ValueError:
                values.append(b)
    # Drop the trailing 0, 0 sentinel the firmware always appends
    while len(values) >= 2 and values[-1] == 0 and values[-2] == 0:
        values.pop()
        values.pop()
        # Break after one pair so we don't eat legitimate trailing zeros
        break
    return values


def _parse_status(html: str) -> dict:
    """Extract structured state from StatusRpm.htm's JS arrays.

    The WR802N's Chinese firmware 1.0.9 (verified 2026-04-12) exposes:

        statusPara = [int, int, int, int, int, "firmware", "hardware", ...]
        lanPara    = ["MAC", "IP", "mask"]
        wlanPara   = [up, "SSID", ..., "MAC", "IP", ..., channel, ...]
        wanPara    = [linkMode, "ip", "mask", ...]   (all empty in pure
                                                      bridge mode where
                                                      no WAN is configured)
    """
    status: dict = {}

    m = re.search(r"<title>([^<]+)</title>", html)
    if m:
        status["page_title"] = m.group(1).strip()

    statusPara = _parse_js_array(html, "statusPara")
    if len(statusPara) >= 7:
        fw = str(statusPara[5]).strip()
        hw = str(statusPara[6]).strip()
        if fw:
            status["firmware"] = fw
        if hw:
            status["hardware"] = hw

    lanPara = _parse_js_array(html, "lanPara")
    if len(lanPara) >= 3:
        status["lan_mac"] = lanPara[0]
        status["lan_ip"] = lanPara[1]
        status["lan_mask"] = lanPara[2]

    wlanPara = _parse_js_array(html, "wlanPara")
    if len(wlanPara) >= 6:
        # [0]=up? [1]=SSID [4]=MAC [5]=IP
        status["wlan_up"] = bool(wlanPara[0])
        status["wlan_ssid"] = wlanPara[1]
        status["wlan_mac"] = wlanPara[4]
        status["wlan_ip"] = wlanPara[5]

    wanPara = _parse_js_array(html, "wanPara")
    # In pure bridge mode wanPara is all zeros/empty strings and
    # conveys nothing useful; only report it if something's in it.
    if wanPara and any(str(v).strip() for v in wanPara if v != 0):
        status["wan_raw"] = wanPara

    # Operation mode inference:
    # - wanPara empty → pure bridge (LAN and WLAN share MAC/IP)
    # - wanPara populated → routed/WISP mode with separate WAN
    if lanPara and wlanPara and len(lanPara) >= 3 and len(wlanPara) >= 6:
        if lanPara[0] == wlanPara[4] and lanPara[1] == wlanPara[5]:
            status["mode_inferred"] = "bridge (LAN and WLAN share MAC/IP)"
        else:
            status["mode_inferred"] = "routed (LAN and WLAN differ)"

    return status


def _fetch_status_page(session: requests.Session, router: str) -> str:
    """Fetch the actual status page. The root URL returns a frameset
    shell; the useful content lives at /userRpm/StatusRpm.htm."""
    r = session.get(
        router + "/userRpm/StatusRpm.htm",
        headers={"Referer": router + "/"},
        timeout=TIMEOUT,
    )
    r.raise_for_status()
    return r.text


def cmd_status(args: argparse.Namespace) -> int:
    password = _load_password(args.password_file)
    with requests.Session() as session:
        _authenticate(session, args.router, password)
        html = _fetch_status_page(session, args.router)
    status = _parse_status(html)
    if not status:
        print(
            "authenticated OK — fetched StatusRpm.htm, but no known "
            "markers matched. Dump with --dump-html to inspect."
        )
    else:
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
        # WR802N Chinese firmware 1.0.9 verified 2026-04-12 at
        # SysRebootRpm.htm. The form is method="get" action="SysRebootRpm.htm"
        # with one submit input name="Reboot" whose value is the gb2312-
        # encoded Chinese button label "重启路由器" (Reboot Router).
        # URL-encoded = %D6%D8%C6%F4%C2%B7%D3%C9%C6%F7.
        url = (
            f"{args.router}/userRpm/SysRebootRpm.htm"
            f"?Reboot=%D6%D8%C6%F4%C2%B7%D3%C9%C6%F7"
        )
        logger.info("POSTing reboot to /userRpm/SysRebootRpm.htm")
        try:
            r = session.get(
                url,
                headers={"Referer": f"{args.router}/userRpm/SysRebootRpm.htm"},
                timeout=TIMEOUT,
            )
        except requests.RequestException:
            # Connection dropping mid-request as the TP-Link begins
            # rebooting is actually a success signal.
            print(
                "Reboot command sent; connection dropped as the "
                "TP-Link begins its restart."
            )
            return 0
        if r.status_code == 200 and not _is_login_page(r.text):
            print(
                "Reboot command accepted. The TP-Link will be "
                "down for ~30-60 seconds. The watchdog will flip "
                "to UNHEALTHY during this window and back to "
                "healthy once the display resumes polling."
            )
            return 0
        raise SystemExit(
            f"Reboot endpoint returned HTTP {r.status_code} and "
            "unexpected content. Dump with --dump-html to debug."
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
