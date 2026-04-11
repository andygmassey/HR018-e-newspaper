# ===== POST 1: Main thread opener =====

📰 **Got a daily newspaper running on my EPD-42S!** NYT front page at full PDF quality, real grayscale, auto-updating at 07:00 every morning.

Source + every patch I had to make: https://github.com/andygmassey/HR018-e-newspaper

Pipeline:
• 07:00 HKT: Mac mini downloads today's NYT print-edition PDF from `static01.nyt.com/images/YYYY/MM/DD/nytfrontpage/scan.pdf`
• Rasterised at 200 DPI → ~2442×4685 pixels
• Auto-trim printer-plate bleed (CMYK marks, filename, price row at top)
• Fit edge-to-edge with top-anchored crop, rotate 90° for a landscape-mounted display
• Served as raw PNG via OpenDisplay WiFi protocol
• Display receives, decodes with BitmapFactory, shows via `postInvalidate(101)` for GC16 grayscale refresh

Huge thanks to Paulus Schoutsen ([balloob on GitHub](https://github.com/balloob)) for OpenDisplay + py-opendisplay — I patched the Android app fairly heavily but the protocol + server are his work. Also thanks to @onlynai for the WiFi module details and the r/eink community in general.

Writeups of every gotcha below ⬇️

# ===== POST 2: macOS firewall silent RST =====

🧱 **Gotcha 1: macOS Application Firewall silently RSTs Homebrew Python**

If the firewall is on (default) and you run py-opendisplay's server via Homebrew Python, the TCP 3-way handshake completes at kernel level, packets get counted as "ESTABLISHED" in `netstat`, but after ~3 seconds the firewall sends a RST and the connection is never delivered to user-space. The `accept()` call just sits there forever.

The debugging is maddening because:
• `netstat -anv` shows ESTABLISHED attributed to your Python PID
• `lsof -p <pid>` only shows the LISTEN socket, no ESTABLISHED
• `socketfilterfw --getappblocked <python>` returns "permitted" even when it isn't
• `tcpdump` shows a clean handshake followed by a mystery RST

The fix: explicitly register the Python.app **bundle path** (not the `python3.11` symlink) with the firewall:
```
sudo /usr/libexec/ApplicationFirewall/socketfilterfw --add \
  /opt/homebrew/Cellar/python@3.11/3.11.15/Frameworks/Python.framework/Versions/3.11/Resources/Python.app
sudo /usr/libexec/ApplicationFirewall/socketfilterfw --unblockapp \
  /opt/homebrew/Cellar/python@3.11/3.11.15/Frameworks/Python.framework/Versions/3.11/Resources/Python.app
```

`install.sh` in my repo now does this automatically. This one cost me hours.

# ===== POST 3: python-zeroconf vs mDNSResponder =====

🧱 **Gotcha 2: py-opendisplay's mDNS silently fails on macOS**

py-opendisplay uses `python-zeroconf` for mDNS advertising. On macOS, that library tries to bind UDP 5353 — which belongs to the system `mDNSResponder`. The bind fails:

```
[WARNING] zeroconf: Error with socket 7 (('0.0.0.0', 5353))): [Errno 65] No route to host
```

…but py-opendisplay catches the exception and logs "advertised" anyway, so the server *thinks* it's discoverable. It isn't. `dns-sd -B _opendisplay._tcp local` from any other machine returns nothing.

The fix: skip python-zeroconf entirely and shell out to Apple's own `/usr/bin/dns-sd`, which uses the system responder and plays nicely with every other Bonjour service on the LAN:

```
/usr/bin/dns-sd -R "OpenDisplay E-Newspaper" _opendisplay._tcp . 2446
```

My server.py spawns this as a subprocess on startup and kills it on shutdown. py-opendisplay's own mDNS is explicitly disabled.

# ===== POST 4: TP-Link WR802N and the Android DHCP quirk =====

🧱 **Gotcha 3: TP-Link WR802N Client mode + Android's stale ConnectivityManager**

Using a WR802N as a WiFi-to-Ethernet bridge to get the display on my LAN. Two fun issues:

1. **Client mode isn't a true L2 bridge by default.** Factory-fresh, the WR802N runs its own DHCP on 192.168.1.0/24 — same subnet as my home LAN — causing ARP storms for every host on the real network. Had to explicitly switch to Client mode *and* rejoin my home WiFi so it turns into a transparent bridge.

2. **Display's stock firmware doesn't auto-DHCP eth0 on boot** (confirming what others have reported re: Vol+/Vol-). Even after the TP-Link is bridged, the Android `EthernetService` gets stuck in `OBTAINING_IPADDR` forever. The cleanest fix:

```
adb shell ifconfig eth0 down
adb shell ifconfig eth0 up
```

That re-triggers Android's ethernet init, which successfully DHCPs and populates `ConnectivityManager` with real link properties. Without it, apps get `ENETUNREACH` because Android's connectivity stack has stale/empty info even though the kernel route table is fine.

# ===== POST 5: Patching the OpenDisplay APK =====

🧱 **Gotcha 4: OpenDisplay Android app is mDNS-only, so I patched the APK**

The OpenDisplay Android app (v0.1.3) discovers servers via Android `NsdManager` only — no UI or prefs for a static server IP. Fine on a flat LAN, but the TP-Link WiFi bridge doesn't reliably pass multicast, so my display never found the server.

The APK is 29KB. I decompiled with `apktool`, made a handful of smali edits, rebuilt and signed with `jarsigner` (SHA256withRSA — SHA1 is disabled in modern JDK). Five patches total:

1. **Hardcoded server fallback** in `MdnsDiscovery.start()` — fire `listener.onServerFound(new ServerInfo("E-Newspaper", "192.168.1.72", 2446))` directly, in parallel with the real mDNS lookup.
2. `READ_TIMEOUT_MS` 60s → 600s — the WiFi bridge latency averages ~1.4s per packet, 60s wasn't enough to receive a 777KB image.
3. `MAX_FRAME_SIZE` 1MB → 8MB — so the client accepts raw PNGs (see next post).
4. `BitmapFactory.decodeByteArray()` tried first in `renderImage()` before the library's 1bpp decoder.
5. `postInvalidate(101)` after `setImageBitmap()` to force GC16 grayscale refresh mode.

Happy to share the patched APK if anyone wants it — or the smali diffs if you'd rather build your own.

# ===== POST 6: The grayscale rabbit hole =====

🧱 **Gotcha 5: OpenDisplay's 1bpp protocol + a 16-level grayscale display = no grayscale**

After everything else was working, the display was showing "pure black-and-white dither" instead of the smooth grayscale newsprint look I expected from a 42" e-ink panel with GC16 support.

**Root cause:** The OpenDisplay WiFi protocol sends **1 bit per pixel**. That's all the Android client ever sees — literally pure black or pure white pixels in memory. Even if you force a GC16 refresh mode on the EPDC, there's no intermediate gray value for the waveform to render. The "grayscale" you get from Floyd–Steinberg dithering is a visual illusion that only holds at viewing distance.

**Fix, for an Avalue EPD-42S specifically** (which has the patched `invalidate(int mode)` / `postInvalidate(int mode)` framework API):

1. Server sends the **raw PNG bytes** on the wire instead of the library's 1bpp encoding.
2. Patched APK tries `BitmapFactory.decodeByteArray()` first — Android natively decodes the PNG into a grayscale `Bitmap` with 256 gray levels.
3. `imageView.setImageBitmap(bitmap)` followed by `imageView.postInvalidate(101)` — that mode code comes from decompiling Avalue's `Animation` demo app, where it's used for the "periodic GC16 ghost-cleanup" refresh.
4. The EPDC driver sees real grayscale in the framebuffer and applies the GC16 waveform.

Result: real 16-level grayscale newsprint on the display. Photos look like halftone print, text is antialiased, no visible pixel stipple at normal viewing distance. This was the biggest win of the project — everything else feels finished once the display stops shouting at you in pure 1-bit.

Full details + patched smali in the repo. Absolutely worth doing if you want anything more than a Kindle-style "text and line art" look.
