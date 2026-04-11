# ===== POST 1: The big announcement =====

**📰 Got the e-newspaper working on my EPD-42S!**

Daily newspaper front page auto-updating every morning, driven by a Mac mini + the TP-Link WR802N as a WiFi-to-Ethernet bridge to the display. Today it's showing the New York Times. Looks fantastic dithered on the e-ink.

Source + all my fixes: https://github.com/andygmassey/HR018-e-newspaper

Pipeline:
• Cron at 07:00 HKT scrapes frontpages.com for ~130 daily newspapers
• Picks today's paper per a weekday schedule (FT on Fridays, SCMP midweek, etc.)
• Processes to 2880×2160 with Floyd–Steinberg dither
• Serves via OpenDisplay WiFi protocol to the display

The backend uses @balloob's brilliant py-opendisplay (thanks!), plus a sideloaded + patched OpenDisplay Android APK with a hardcoded server IP since mDNS across a WiFi bridge is unreliable.

It took way longer than it should have — there were 5 or 6 gotchas along the way. I'll post them as replies in this thread in case anyone hits the same walls.

# ===== POST 2: macOS firewall gotcha =====

**🧱 Gotcha #1: macOS Application Firewall silently RSTs Homebrew Python**

This one cost me HOURS. If you run the OpenDisplay server on a Mac with the firewall enabled, and you're using Homebrew Python (not system Python), **incoming TCP connections complete the 3-way handshake and then get RST'd after ~3 seconds by the firewall, without the connection ever reaching your Python process**.

The debugging is diabolical because:
• `netstat -anv | grep 2446` shows ESTABLISHED, attributed to your Python PID
• `lsof -p <pid>` only shows the LISTEN socket
• Python's `accept()` sits there forever
• `pfctl -s rules` is empty
• `socketfilterfw --getappblocked <python>` returns "permitted" even when it's being blocked

The fix: register the Python.app bundle (not just `python3.11`) with the firewall:
```
sudo /usr/libexec/ApplicationFirewall/socketfilterfw --add \
  /opt/homebrew/Cellar/python@3.11/3.11.15/Frameworks/Python.framework/Versions/3.11/Resources/Python.app
sudo /usr/libexec/ApplicationFirewall/socketfilterfw --unblockapp \
  /opt/homebrew/Cellar/python@3.11/3.11.15/Frameworks/Python.framework/Versions/3.11/Resources/Python.app
```

Packet capture showed:
```
SYN → SYN-ACK → ACK → FIN → ACK → (3 seconds of nothing) → RST
```

The RST isn't from Python; it's from the firewall kicking a connection it never liked. My install.sh now does this automatically.

# ===== POST 3: python-zeroconf vs mDNSResponder =====

**🧱 Gotcha #2: py-opendisplay's mDNS doesn't advertise on macOS**

py-opendisplay uses python-zeroconf for mDNS advertising. On macOS, that library tries to bind UDP 5353 — which is owned by the system mDNSResponder. The bind silently fails with ENORUTE:

```
[WARNING] zeroconf: Error with socket 7 (('0.0.0.0', 5353))): [Errno 65] No route to host
```

…but py-opendisplay logs "mDNS: advertised" anyway, because the exception is swallowed. The service is never registered, so OpenDisplay clients can't discover it.

**Fix:** shell out to `/usr/bin/dns-sd` (which uses the system responder properly) instead of python-zeroconf. I patched my server.py to spawn it as a subprocess:

```
/usr/bin/dns-sd -R "OpenDisplay E-Newspaper" _opendisplay._tcp . 2446
```

After that, `dns-sd -B _opendisplay._tcp local` finds it immediately, and clients discover it across the LAN. Added this as a workaround in https://github.com/andygmassey/HR018-e-newspaper/blob/main/src/server.py — the server always uses dns-sd on macOS regardless of the mdns flag.

# ===== POST 4: TP-Link WR802N Client mode =====

**🧱 Gotcha #3: TP-Link WR802N "Client mode" and the display's DHCP quirk**

Set up my WR802N as a WiFi-to-Ethernet bridge. Two things bit me:

**1. Client mode isn't a true L2 bridge by default.** Initially the TP-Link ran its own DHCP on 192.168.1.0/24 — the SAME subnet as my home LAN — causing ARP to fail for every host on Massey. Had to switch explicitly to Client mode (not "Router" or "Hotspot"). Once joined to Massey, the display got a real DHCP lease from my home router and the TP-Link became a transparent bridge.

**2. The display's stock firmware doesn't auto-DHCP eth0 on boot** unless you hold Vol+ / Vol- (as others have reported). But there's a cleaner ADB fix if you can connect once:

```
adb shell ifconfig eth0 down
adb shell ifconfig eth0 up
```

That re-triggers Android's EthernetService which then actually requests DHCP and populates Android's ConnectivityManager with real link properties. Without this, apps get ENETUNREACH because ConnectivityManager has stale/empty info even though the kernel route table is fine.

Also: on a slow WiFi bridge (mine's 100-1400ms latency), you need to bump the socket read timeout in the OpenDisplay client — 60s isn't enough to transfer a 777KB image.

# ===== POST 5: Patching the OpenDisplay APK =====

**🧱 Gotcha #4: OpenDisplay Android app is mDNS-only, no manual server config**

The app (v0.1.3) uses Android NsdManager for discovery — no UI or prefs for a static server IP. That's fine when your display is on the same L2 segment as the server, but mine sits behind a WiFi bridge and multicast just doesn't traverse reliably.

So I patched the APK. The app's only 29KB — trivial to decompile with apktool, edit the smali, rebuild, and sign. My patch injects a hardcoded `ServerInfo("E-Newspaper", "192.168.1.72", 2446)` into `MdnsDiscovery.start()` right after the NSD discovery call, then fires `listener.onServerFound(info)` directly. The existing mDNS discovery keeps running alongside as a fallback.

I also bumped `READ_TIMEOUT_MS` from 60000 → 600000 (60s → 10min) for the slow WiFi bridge, since 777KB images over a lossy link can take a while.

Minimal patch location: `smali/org/opendisplay/android/MdnsDiscovery.smali` at the end of `start()`:

```
new-instance v0, Lorg/opendisplay/android/MdnsDiscovery$ServerInfo;
const-string v1, "E-Newspaper (hardcoded)"
const-string v2, "192.168.1.72"
const/16 v3, 0x98e
invoke-direct {v0, v1, v2, v3}, ...ServerInfo-><init>(...)
iget-object v1, p0, ...->listener:...
invoke-interface {v1, v0}, ...Listener->onServerFound(...)
```

Build with `apktool b`, sign with jarsigner + SHA256withRSA (SHA1 is deprecated in modern JDK), install with `adb install -r`.

Everything's in the repo. Happy to share the patched APK directly if anyone wants it rather than building their own. Huge thanks to @balloob for OpenDisplay itself and @onlynai for figuring out the WiFi card situation earlier!
