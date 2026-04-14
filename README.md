# HR018 — E-Newspaper

Daily newspaper front pages on a 42" Avalue **EPD-42S** monochrome e-ink
display, driven by an always-on Mac mini running an OpenDisplay WiFi server.

The NYT front page is fetched as a high-resolution PDF, rasterised at
200 DPI, and served via the
[OpenDisplay](https://github.com/balloob/opendisplay-android) WiFi
protocol. The display polls the server every 5 minutes and renders the
image in real 16-level grayscale on the e-ink panel.

> **Background:** the EPD-42S is a 42" Android 5.1.1 e-ink panel (2880×2160, 4:3 ratio) originally
> sold by Avalue as a digital signage product. Uses E Ink VB3300-RBA Salt driving board.
> Several people on a [Reddit thread](https://www.reddit.com/r/eink/comments/1rru7zd/) and
> the associated Discord picked them up cheap second-hand without
> instructions. This is one of the projects to come out of that community reverse-engineering effort.

## Architecture

```
Mac mini (always-on macOS)
├── launchd (hourly) → scraper.py → NYT PDF → pdftoppm 200 DPI
├── processor.py → fit/grayscale/rotate → images/current.png
├── server.py → OpenDisplay WiFi server :2446 + mDNS
├── watchdog.py → heartbeat monitor (every 5 min)
├── tplink_admin.py → bridge status/reboot via admin UI
└── tools/remote_shell.py → reverse shell listener for OTA management

TP-Link WR802N (Client mode bridge, WiFi-to-Ethernet)

EPD-42S Display (Android 5.1.1, Ethernet via bridge)
├── install-recovery.sh → DHCP retry + daemon startup at boot
├── tp_watchdog.sh → auto-reboots bridge on connectivity loss
├── display_remote.sh → reverse shell to Mac mini for OTA access
└── OpenDisplay WiFi app → polls server, renders on e-ink panel
```

## Project layout

```
config.json                 selection strategy + display orientation
src/
    scraper.py              fetch newspaper images (dispatches to per-paper scrapers)
    nyt_scraper.py          NYT high-res PDF scraper (200 DPI via pdftoppm)
    processor.py            resize/grayscale/letterbox for the display
    server.py               OpenDisplay WiFi server + heartbeat file
    watchdog.py             pipeline health check (heartbeat freshness)
    tplink_admin.py         TP-Link WR802N admin UI CLI (status / reboot)
tests/
    test_e2e.py             smoke test that pretends to be the display
tools/
    remote_shell.py         reverse shell listener for OTA display management
deploy/
    install.sh              one-shot installer (creates venv, loads launchd jobs)
    com.e-newspaper.server.plist           always-on server launchd job
    com.e-newspaper.daily-update.plist     hourly scraper+processor launchd job
    com.e-newspaper.watchdog.plist         pipeline watchdog launchd job
    DISPLAY_SETUP.md        how to set up the EPD-42S to talk to the server
images/                     gitignored output directory
```

## Quick start (local development)

You need Python 3.11+ (py-opendisplay requires it) and poppler
(`brew install poppler` for `pdftoppm`).

```bash
git clone https://github.com/andygmassey/HR018-e-newspaper.git
cd HR018-e-newspaper

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
git clone https://github.com/andygmassey/HR018-e-newspaper.git ~/projects/HR018-e-newspaper
cd ~/projects/HR018-e-newspaper
./deploy/install.sh
```

The installer:
1. Creates a Python 3.11 venv at `.venv/`
2. Installs dependencies
3. Runs a smoke scrape to confirm the pipeline works
4. Rewrites the launchd plist paths to match the install location
5. Loads launchd jobs (server always-on, scraper hourly, watchdog every 5 min)

After install:

```bash
tail -f server.log     # OpenDisplay server log
tail -f scraper.log    # scrape/process log
tail -f watchdog.log   # watchdog log
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
panel is rendered as 2160x2880). Set to `landscape` to render 2880x2160
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

**New York Times** is the primary source — fetched as the public
print-edition PDF from `static01.nyt.com` and rasterised at 200 DPI
(~2442x4685 pixels). The scraper is ET-date idempotent: it checks a
sidecar `.etdate` file and skips fetch+rasterise if today's edition
is already stored.

**frontpages.com** is the fallback for other papers — aggregates ~130
newspapers worldwide at 600x800 webp thumbnails. Adequate for secondary
papers but looks poor at 42" scale.

**Not available on frontpages.com:** the major UK national broadsheets
(Guardian, Times, Telegraph, Daily Mail, Independent). For those, the
path forward is PressReader access via a public library card or building
per-publisher high-res scrapers.

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
