# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**HR018 — E-Newspaper** displays daily newspaper front pages on a 42"
Avalue EPD-42S e-ink display (2880×2160, monochrome). The backend runs
on any always-on macOS host (typically a Mac mini) and pushes images to
the display via the OpenDisplay WiFi protocol.

Companion to **HR017 — 42 E-ink** (display hardware reverse-engineering,
ADB notes, refresh-mode discovery, CoffeeETable build).

## Architecture

```
Backend host (always-on macOS)
├── launchd (07:00 daily) → scraper.py
│   └── Scrapes frontpages.com for that day's newspaper images
│   └── Saves to images/raw/<slug>.webp
├── processor.py
│   └── Resizes/letterboxes to 2880×2160 (or 2160×2880 portrait), grayscale
│   └── Saves to images/processed/<slug>-<date>.png
│   └── Updates images/current.png to point at the chosen paper
└── server.py (always-on via launchd)
    └── OpenDisplay WiFi TCP server on port 2446
    └── Advertises via mDNS as _opendisplay._tcp
    └── Serves images/current.png to any polling display

EPD-42S Display (on LAN via Ethernet or WiFi bridge)
└── OpenDisplay WiFi Android app
    └── Discovers backend via mDNS, polls for new images
    └── Renders on the e-ink panel
```

## Display Hardware

- **Model:** Avalue EPD-42S-SIDA0-01R (42" monochrome e-ink)
- **Resolution:** 2880 × 2160 (4:3)
- **OS:** Android 5.1.1
- **Framebuffer:** `mxc_epdc_fb` (NXP EPDC driver)
- **Network:** Ethernet or WiFi-to-Ethernet bridge (no built-in WiFi)

## Tech Stack

- **Python 3.11+** (required by py-opendisplay)
- **py-opendisplay** — OpenDisplay WiFi protocol server
- **Pillow** — image processing
- **beautifulsoup4 + requests** — newspaper scraping
- **launchd** — macOS service management

## Commands

```bash
# Activate the venv created by deploy/install.sh
source .venv/bin/activate

# List all papers available on frontpages.com today
python src/scraper.py --list

# Download today's chosen papers (defaults defined in scraper.py)
python src/scraper.py

# Or download specific papers by slug
python src/scraper.py financial-times south-china-morning-post

# Process today's chosen paper (per config.json) and update current.png
python src/processor.py

# Process every raw image we have
python src/processor.py --all

# Start the OpenDisplay server (manual run; in production launchd handles this)
python src/server.py

# End-to-end smoke test (pretends to be the display, verifies handshake)
python tests/test_e2e.py
```

## Newspaper Source

Primary: **frontpages.com** — aggregates ~130 newspapers daily, updated
overnight UK time. Major UK broadsheets (Guardian, Times, Telegraph,
Daily Mail) are NOT on frontpages.com. The available UK-relevant titles
are Financial Times, City AM, Yorkshire Post, Morning Star, plus regional
papers and the Irish Times. International titles include the New York
Times, Washington Post, Globe and Mail, South China Morning Post, and
Japan Times.

Image resolution from frontpages.com is limited (the public `@2x.webp`
URLs are 600×800 thumbnails). On a 42" e-ink display with Floyd–Steinberg
dithering, headlines and body text remain readable, but for higher
fidelity a different source would be needed (PressReader via a library
card, or direct PDF editions).

## OpenDisplay Protocol Notes

- The display polls the server, not the other way around
- TCP port 2446, mDNS discovery on `_opendisplay._tcp`
- The server converts PNG → 1bpp monochrome with Floyd–Steinberg dithering
  via `epaper-dithering` (vendored by py-opendisplay)
- The server deduplicates by SHA-256 — sending the same image twice in a
  row returns NO_IMAGE on the second poll
- `poll_interval` default in `src/server.py` is 300s (5 min), since
  newspapers update once a day

## Key Constraints

- The backend host must be always-on and on the same LAN as the display
  for mDNS discovery to work
- macOS firewall must allow incoming connections to the python binary on
  port 2446
- Image rendering produces a portrait orientation (2160×2880) by default
  because newspapers are taller than they are wide; the EPD-42S handles
  this fine without OS-level rotation (avoid `ro.sf.hwrotation` — it
  causes a known skew/fracture bug on this display)
