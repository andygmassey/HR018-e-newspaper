# EPD-42S Display Setup (HR018 — E-Newspaper)

This guide gets the Avalue EPD-42S running the OpenDisplay WiFi Android
app so it polls the HR018 backend server and shows today's front page.

## Prerequisites

- Backend already deployed (run `deploy/install.sh`). The OpenDisplay
  server should be listening on port 2446 on the LAN, advertising via
  mDNS.
- Display is powered on and connected to the same LAN as the backend
  host (either via Ethernet or via a WiFi-to-Ethernet bridge such as the
  TP-Link TL-WR802N or VONETS VAP11AC).
- ADB access to the display via micro-USB.

## 1. Disable AdSign (one-off)

If you haven't already, disable the AdSign QR code app so the display
boots straight into Android instead of the kiosk registration screen:

```bash
adb shell pm disable-user com.miles22.adsign
```

## 2. Install the OpenDisplay WiFi APK

Download the latest release from
<https://github.com/balloob/opendisplay-android/releases>, then sideload
via ADB:

```bash
adb install ~/Downloads/opendisplay-android.apk
```

If you get `INSTALL_FAILED_OLDER_SDK`, the APK is built for a newer
Android version than 5.1.1. Check the releases page for an older build,
or build it yourself targeting `minSdkVersion 22`.

## 3. Launch the app

```bash
adb shell am start -n org.balloob.opendisplay/.MainActivity
```

Replace the package name if it differs — confirm with
`adb shell pm list packages | grep -i opendisplay`.

The app should:
1. Discover the backend server via mDNS (`_opendisplay._tcp`)
2. Send a display announcement (2880×2160, monochrome)
3. Receive and display today's front page
4. Re-poll every 5 minutes (configurable in `src/server.py`)

## 4. Set the app to auto-start on boot

For long-term use, you'll want OpenDisplay to launch automatically when
the display boots. Options:

- Use the Android **Default Launcher** setting and set OpenDisplay as
  the home app (Settings → Apps → Default apps → Home).
- Or set it as the home activity via ADB:

```bash
adb shell cmd package set-home-activity org.balloob.opendisplay/.MainActivity
```

## 5. Verify it's working

On the backend host:

```bash
tail -f <install-dir>/server.log
```

You should see lines like:

```
[INFO] server: Client connected: 192.168.x.y
[INFO] server: Image request from 192.168.x.y (battery=100, rssi=-50)
[INFO] server: Encoding current.png ((2160, 2880), L) for display 2880x2160 scheme=0
[INFO] server: Sending image to 192.168.x.y (777600 bytes)
```

If no client connects, check:

- Display is on the same subnet as the backend host
- mDNS is not blocked (firewall, router AP isolation)
- TCP 2446 is reachable on the backend host
- macOS firewall on the backend allows incoming connections to the
  python binary

## 6. Force a manual update

To pull a new newspaper now (e.g. for testing):

```bash
cd <install-dir>
.venv/bin/python src/scraper.py
.venv/bin/python src/processor.py
```

The display will pick it up on its next poll (within 5 minutes).

## Troubleshooting

### Display shows AdSign QR code instead of OpenDisplay
- AdSign is re-enabled. Run
  `adb shell pm disable-user com.miles22.adsign` again.
- OpenDisplay isn't set as the default home — see step 4.

### "No image" response in server logs
- The display has connected, but `images/current.png` doesn't exist.
- Run the scraper + processor manually (step 6).

### Display shows nothing or a blank screen
- Confirm OpenDisplay is actually running:
  `adb shell dumpsys window | grep -i opendisplay`
- Check the backend host's firewall isn't blocking incoming connections
- Try `--no-mdns` mode and configure the server IP manually in the
  OpenDisplay app

### Image looks wrong size or distorted
- The OpenDisplay app's announcement told the server `2880x2160`. The
  server fits the image to those dimensions in CONTAIN mode (preserving
  aspect ratio with padding). If the orientation seems wrong, edit
  `config.json` and toggle `"orientation"` between `"landscape"` and
  `"portrait"`.

### Rotation skew bug
- Forcing OS-level rotation on this display causes visual corruption in
  custom apps and WebView (see the r/eink Discord thread). `processor.py`
  already handles orientation at render time by producing the image at
  2160×2880 directly; we don't touch the OS rotation. If you see skew,
  ensure `ro.sf.hwrotation` in `/system/build.prop` is `0` (the
  default).
