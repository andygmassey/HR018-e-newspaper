# On-display scripts

These shell scripts live on the EPD-42S Android filesystem, not on Massey.
They are tracked here so they survive a factory reset or fresh-flash and
can be redeployed via ADB.

## Files

| File | Path on display | Started by | Purpose |
| ---- | --------------- | ---------- | ------- |
| `install-recovery.sh` | `/system/bin/install-recovery.sh` | `init.rc` (oneshot, class main) | Boot hook. Waits for eth0 IP, then launches `supervisor.sh`. |
| `supervisor.sh` | `/data/local/tmp/supervisor.sh` | `install-recovery.sh` | Respawns `display_remote.sh` and `tp_watchdog.sh` every 60s if dead. |
| `display_remote.sh` | `/data/local/tmp/display_remote.sh` | `supervisor.sh` | Reverse shell: dials Massey :9999 every 30s for OTA recovery. |
| `tp_watchdog.sh` | `/data/local/tmp/tp_watchdog.sh` | `supervisor.sh` | Pings Massey every 60s, reboots the TP-Link bridge after 3 failures. |

## Why a supervisor

Android was observed running 7+ continuous days with both daemons silently
dead (`display_remote.sh` and `tp_watchdog.sh` both exited). `install-recovery.sh`
only fires at the actual Android boot, and the panel's power button cycles
the EPD panel without rebooting Android (Android keeps running through the
power blip). So once a daemon died there was nothing on-device to bring it
back, and the Massey-side `auto_recover.py` was useless because its only
channel is `display_remote.sh` dialing in.

The supervisor closes that gap with a single shell `while true` loop that
checks the process table every 60s and respawns dead daemons.

## Deployment

Requires ADB over USB (the in-band wireless ADB is blocked by the TP-Link
bridge's 3-address WiFi limitation).

```sh
# Remount /system writable (engineering build allows this)
adb shell 'mount -o remount,rw /system'

# Push /data/local/tmp scripts
adb push deploy/display/supervisor.sh    /data/local/tmp/supervisor.sh
adb push deploy/display/display_remote.sh /data/local/tmp/display_remote.sh
adb push deploy/display/tp_watchdog.sh    /data/local/tmp/tp_watchdog.sh
adb shell 'chmod 755 /data/local/tmp/supervisor.sh \
                     /data/local/tmp/display_remote.sh \
                     /data/local/tmp/tp_watchdog.sh'

# Push the boot hook
adb push deploy/display/install-recovery.sh /system/bin/install-recovery.sh
adb shell 'chmod 750 /system/bin/install-recovery.sh'

# Restore /system read-only (will happen on next reboot regardless)
adb shell 'mount -o remount,ro /system' 2>/dev/null

# Reboot to validate the boot path
adb reboot
adb wait-for-device
adb shell 'while [ "$(getprop sys.boot_completed)" != "1" ]; do sleep 2; done'
adb shell 'busybox ps w | busybox grep -E "supervisor|display_remote|tp_watchdog" | busybox grep -v grep'
```

You should see all three processes within ~60s of boot.

## Logs

- `/data/local/tmp/supervisor.log` is the supervisor's own log
- `display_remote.sh` and `tp_watchdog.sh` are silent by design
- `adb shell 'logcat -d -s boot_init'` shows install-recovery.sh's log lines

## Notes on this Avalue build

- Stock `ps` does not show script arguments. Use `busybox ps w` to see
  whether a `sh /data/local/tmp/foo.sh` process is alive.
- `setsid` is not installed. `nohup` works fine for daemonizing.
- `awk`, `pkill`, `head`, `tail`, `which` are not in PATH by default.
  Use the `busybox` variants (`busybox awk`, `busybox pkill`, etc).
- `/system` is normally ro. Remount with `mount -o remount,rw /system`.
  An engineering build (this one is `sabresd_7d_eink-eng`) allows it
  without verity. Stock retail builds may not.
