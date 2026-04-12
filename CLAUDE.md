# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**HR018 — E-Newspaper** displays daily newspaper front pages on a 42"
Avalue EPD-42S e-ink display (2880x2160, monochrome). The backend runs
on a Mac mini and pushes images to the display via the OpenDisplay WiFi
protocol. Fully unattended — survives display reboots, network drops,
and bridge flaps.

Companion to **HR017 — 42 E-ink** (display hardware reverse-engineering,
ADB notes, refresh-mode discovery, CoffeeETable build).

## Architecture

```
Mac mini "Massey" (192.168.1.72, always-on macOS)
├── launchd (hourly, StartInterval=3600) → scraper.py
│   └── nyt_scraper.py → static01.nyt.com PDF → pdftoppm 200 DPI
│   └── ET-date idempotency (images/raw/*.etdate sidecar)
│   └── Saves to images/raw/<slug>.png
├── processor.py
│   └── Fit to 2160×2880 portrait, grayscale, rotate 90° → 2880×2160
│   └── Updates images/current.png
├── server.py (always-on via launchd, port 2446 + mDNS)
│   └── Serves raw PNG on OpenDisplay poll
│   └── Touches images/last-poll.txt on every poll (heartbeat)
├── watchdog.py (every 5 min via launchd)
│   └── Alerts when last-poll.txt stale > 900s
├── tplink_admin.py
│   └── Cookie auth to WR802N admin UI (status / reboot)
└── tools/remote_shell.py
    └── Listens on port 9999, accepts reverse shell from display

TP-Link TL-WR802N (Client mode, pure bridge, .253)
├── LAN + WLAN share MAC/IP — no NAT
├── OUTBOUND only (3-address WiFi limitation)
│   └── Display → Massey: works
│   └── Massey → Display: BLOCKED (no inbound)
└── Radio flaps intermittently; tp_watchdog.sh auto-reboots

EPD-42S Display (DHCP from Massey via bridge)
├── /system/bin/install-recovery.sh (boot hook)
│   └── Retries DHCP 8 times
│   └── Starts tp_watchdog.sh + display_remote.sh daemons
├── /data/local/tmp/tp_watchdog.sh
│   └── Pings Mac mini every 60s, reboots bridge after 3 failures
├── /data/local/tmp/display_remote.sh
│   └── Connects OUT to Mac mini :9999 every 30s (reverse shell)
├── Patched OpenDisplay APK (BootReceiver auto-launches)
│   └── BitmapFactory → postInvalidate(101) → 16-level grayscale
└── adbd on TCP 5555 (persistent via /data/local.prop)
    └── Note: adb connect doesn't work through bridge (inbound blocked)
```

## Display Hardware

- **Model:** Avalue EPD-42S-SIDA0-01R (42" monochrome e-ink)
- **Resolution:** 2880 x 2160 (4:3)
- **OS:** Android 5.1.1 (rooted engineering build, uid=0)
- **Framebuffer:** `mxc_epdc_fb` (NXP EPDC driver)
- **Network:** Ethernet via TP-Link WR802N WiFi-to-Ethernet bridge

## Tech Stack

- **Python 3.11+** (required by py-opendisplay)
- **py-opendisplay** — OpenDisplay WiFi protocol server
- **Pillow** — image processing
- **beautifulsoup4 + requests** — newspaper scraping
- **poppler** (`pdftoppm`) — rasterising PDF front pages (NYT scraper)
- **launchd** — macOS service management

## Commands

```bash
# Activate the venv created by deploy/install.sh
source .venv/bin/activate

# List all papers available on frontpages.com today
python src/scraper.py --list

# Download today's chosen papers
python src/scraper.py

# Process today's chosen paper (per config.json) and update current.png
python src/processor.py

# Start the OpenDisplay server (manual; in production launchd handles this)
python src/server.py

# End-to-end smoke test (pretends to be the display)
python tests/test_e2e.py

# TP-Link bridge status / reboot
python src/tplink_admin.py status
python src/tplink_admin.py reboot

# Start reverse shell listener (display connects to this)
python tools/remote_shell.py

# Force a scrape+process cycle
launchctl start com.e-newspaper.daily-update
```

## Newspaper Sources

Two-tier strategy:
1. **High-res per-paper scrapers** — NYT via `nyt_scraper.py` (200 DPI
   PDF rasterisation, ~2442x4685 pixels). ET-date idempotent.
2. **frontpages.com fallback** — ~130 papers at 600x800 webp thumbnails.
   Adequate for secondary papers, poor when upscaled to 42".

## Key Constraints

- The backend must be always-on and on the same LAN for mDNS
- macOS firewall must allow Python on port 2446 (socketfilterfw gotcha)
- launchd plists must set `EnvironmentVariables.PATH` to include
  `/opt/homebrew/bin` (pdftoppm lives there)
- launchd StartCalendarInterval uses a cached timezone (observed PDT
  when system is Asia/Hong_Kong) — use StartInterval instead
- Image is portrait (2160x2880) rotated 90 to landscape (2880x2160);
  avoid `ro.sf.hwrotation` (causes skew/fracture bug on this display)

## Network Topology (important)

The TP-Link WR802N is in **Client mode (pure bridge)**, NOT NAT/WISP.
- Display gets a Massey DHCP lease directly
- **Outbound only**: display can reach Mac mini, but Mac mini CANNOT
  reach display (3-address WiFi framing limitation)
- `adb connect` from Mac mini does NOT work; use the reverse shell
- Radio flaps: bridge drops WiFi intermittently; tp_watchdog.sh on
  the display auto-reboots it via admin UI cookie auth

## Display Boot Sequence

1. Power on → init.rc runs install-recovery.sh (class main, oneshot)
2. install-recovery.sh retries DHCP 8x, starts tp_watchdog.sh +
   display_remote.sh daemons
3. adbd starts on TCP 5555 (from /data/local.prop)
4. BOOT_COMPLETED → OpenDisplay BootReceiver → MainActivity launches
5. OpenDisplay polls Mac mini server, renders newspaper

## Android Quirks

- **ConnectivityManager goes stale**: after eth0 changes, apps don't
  see the new network until a full reboot. Shell commands work fine.
- **Never run `stop adbd; start adbd`** over wireless ADB — kills the
  session. Use `adb reboot` instead.
- **busybox wget needs cookie auth** for TP-Link admin UI
  (`--header 'Cookie: Authorization=...'`), NOT Basic Auth headers.

## Security

- No passwords, WiFi credentials, or secrets in this repo
- TP-Link admin password is at `~/.config/hr018/tplink.password` on
  the Mac mini (never commit or reference its contents)
- The display is a rooted engineering build — acceptable for home LAN
  hobby use, not for commercial deployment
