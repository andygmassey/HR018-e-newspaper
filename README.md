# HR018 — E-Newspaper

Daily newspaper front pages on a 42" Avalue **EPD-42S** monochrome e-ink
display, driven by an always-on Mac running an OpenDisplay WiFi server.

Companion to **HR017 — 42 E-ink** (display hardware reverse-engineering,
ADB notes, refresh-mode discovery, and the CoffeeETable build).

Scraper runs every morning, downloads the chosen newspaper from
[frontpages.com](https://www.frontpages.com/), processes it for the e-ink
panel, and serves it via the
[OpenDisplay](https://github.com/balloob/opendisplay-android) WiFi
protocol. The display polls the server every 5 minutes and shows the
latest image.

> Background: the EPD-42S is a 42" Android-based e-ink panel originally
> sold by Avalue as a digital signage product. Several people on a
> [Reddit thread](https://www.reddit.com/r/eink/comments/1rru7zd/) and
> the associated Discord picked them up cheap second-hand without
> instructions. This is one of the projects to come out of that.

## Architecture

```
Backend host (always-on macOS)
├── launchd → scraper.py → frontpages.com → images/raw/<slug>.webp
├── launchd → processor.py → fits/dithers → images/current.png
└── launchd → server.py → OpenDisplay WiFi server :2446 + mDNS

EPD-42S Display (LAN: Ethernet, or WiFi-to-Ethernet bridge)
└── OpenDisplay WiFi Android app
    └── discovers backend via mDNS, polls every 5 min, displays image
```

## Project layout

```
config.json                 selection strategy + display orientation
src/
    scraper.py              fetch newspaper images from frontpages.com
    processor.py            resize/grayscale/letterbox for the display
    server.py               OpenDisplay WiFi server
tests/
    test_e2e.py             smoke test that pretends to be the display
deploy/
    install.sh              one-shot installer (creates venv, loads launchd jobs)
    com.e-newspaper.server.plist           always-on server launchd job
    com.e-newspaper.daily-update.plist     daily scraper+processor launchd job
    DISPLAY_SETUP.md        how to set up the EPD-42S to talk to the server
images/                     gitignored output directory
```

## Quick start (local development)

You need Python 3.11+ (py-opendisplay requires it).

```bash
git clone https://github.com/<you>/e-newspaper.git
cd e-newspaper

python3.11 -m venv .venv
.venv/bin/pip install requests beautifulsoup4 Pillow \
    "py-opendisplay @ git+https://github.com/balloob/py-opendisplay@wifi-server"

# Pull today's papers
.venv/bin/python src/scraper.py

# Process today's chosen paper (per config.json) → images/current.png
.venv/bin/python src/processor.py

# Run the server in the foreground
.venv/bin/python src/server.py

# In another terminal: smoke test (pretends to be the display)
.venv/bin/python tests/test_e2e.py
```

## Deploying to an always-on Mac

```bash
git clone https://github.com/<you>/e-newspaper.git ~/projects/e-newspaper
cd ~/projects/e-newspaper
./deploy/install.sh
```

The installer:
1. Creates a Python 3.11 venv at `.venv/`
2. Installs dependencies
3. Runs a smoke scrape to confirm the pipeline works
4. Rewrites the launchd plist paths to match the install location
5. Loads both launchd jobs (`com.e-newspaper.server` always-on, and
   `com.e-newspaper.daily-update` at 07:00 daily)

After install:

```bash
tail -f server.log     # OpenDisplay server log
tail -f scraper.log    # daily scrape/process log
launchctl start com.e-newspaper.daily-update    # force a refresh now
```

## Configuring which paper to show

Edit `config.json`:

| `selection` | Behaviour |
|---|---|
| `weekday`  | Picks a different paper for each day of the week (`weekday_map`) |
| `rotate`   | Cycles through `rotation` list, one per day |
| `fixed`    | Always shows `fixed` |

`orientation` is `portrait` by default (newspapers are tall, the e-ink
panel is rendered as 2160×2880). Set to `landscape` to render 2880×2160
instead.

To list all the papers available on frontpages.com today:

```bash
.venv/bin/python src/scraper.py --list
```

## Setting up the EPD-42S display

See [`deploy/DISPLAY_SETUP.md`](deploy/DISPLAY_SETUP.md) for the full
flow: disabling the AdSign boot app via ADB, sideloading the OpenDisplay
WiFi APK, and setting it as the home activity so it survives reboots.

Once the display is on the same LAN as the backend, it will discover the
server via mDNS and start polling automatically.

## Image source notes

frontpages.com aggregates ~130 newspapers worldwide and updates them
daily. Image resolution is limited (the public `@2x.webp` URLs are
600×800 thumbnails) but on a 42" e-ink panel with Floyd–Steinberg
dithering, headlines and body text remain readable.

**Not available on frontpages.com:** the major UK national broadsheets
(Guardian, Times, Telegraph, Daily Mail, Independent). Available
UK-relevant titles include:

- Financial Times ✓
- South China Morning Post ✓
- City AM, Yorkshire Post, Morning Star
- Various regional papers (Manchester Evening News, Liverpool Echo, etc.)
- The Irish Times

For higher fidelity or for the missing UK titles you'd need a different
source — PressReader (free with many public library cards) or direct
PDF editions from the publishers. PRs welcome.

## Credits

- The [OpenDisplay](https://github.com/balloob/opendisplay-android)
  Android app and
  [py-opendisplay](https://github.com/balloob/py-opendisplay) library by
  Paulus Schoutsen
- frontpages.com for the daily aggregated images
- The r/eink community and the EPD-42 Discord for collectively reverse
  engineering this hardware
- Inspired in part by Max Braun's
  ["Paper" project](https://onezero.medium.com/the-morning-paper-revisited-35b407822494)

## Licence

MIT — see [LICENSE](LICENSE).
