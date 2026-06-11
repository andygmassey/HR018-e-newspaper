# On-display scripts

These shell scripts live on the EPD-42S Android filesystem, not on Massey.
They are tracked here so they survive a factory reset or fresh-flash and
can be redeployed via ADB.

## Files

| File | Path on display | Started by | Purpose |
| ---- | --------------- | ---------- | ------- |
| `install-recovery.sh` | `/system/bin/install-recovery.sh` | `init.rc` (oneshot, class main) | Boot hook. Waits for eth0 IP, then launches `supervisor.sh`. |
| `supervisor.sh` | `/data/local/tmp/supervisor.sh` | `install-recovery.sh` | Respawns `display_remote.sh` and `net_watchdog.sh` every 60s if dead. |
| `display_remote.sh` | `/data/local/tmp/display_remote.sh` | `supervisor.sh` | Reverse shell: dials Massey :9999 every 30s. Recovery channel + OTA. |
| `net_watchdog.sh` | `/data/local/tmp/net_watchdog.sh` | `supervisor.sh` | Every 30s checks it can actually reach Massey; reboots if it stays unreachable ~2 min. Self-contained network self-heal (reboot-only, nothing in the loop that can hang). |

## Why a supervisor

Android was observed running 7+ continuous days with both daemons silently
dead (`display_remote.sh` and the network watchdog both exited). `install-recovery.sh`
only fires at the actual Android boot, and the panel's power button cycles
the EPD panel without rebooting Android (Android keeps running through the
power blip). So once a daemon died there was nothing on-device to bring it
back, and the Massey-side `auto_recover.py` was useless because its only
channel is `display_remote.sh` dialing in.

The supervisor closes that gap with a single shell `while true` loop that
checks the process table every 60s and respawns dead daemons.

## The recurring network failure (root cause and the fix)

Observed 2026-06: every day or so the display stopped serving and the panel
stuck on "waiting for server".

Root cause (ground-truthed over adb in the actual failure state, 2026-06-09):
eth0 loses its DHCP lease / network config. dhcpcd drops (`init.svc.dhcpcd_eth0:
stopped`, `dhcp.eth0.reason: PREINIT`), the interface stays UP at L2 (IPv6
link-local present, packets flowing) but has NO IPv4 address and NO routes.
All IPv4 dies, even at root, so the OpenDisplay app and the reverse shell
both go dark. This is a total network loss, not an app-only `ENETUNREACH`.

Earlier theories were wrong and are debunked:
- The "60-second bridge lease" was never real. The live lease is `server
  192.168.1.1, gateway 192.168.1.1, leasetime 86400` (24h, from the upstream
  router). The bridge is a pure L2 bridge and is not the DHCP server.
- "Root networking keeps working while only apps break" was also wrong in
  this failure: root `ping` fails with `Network is unreachable` too.

Recovery has to be a **reboot**, for two reasons learned the hard way:
- A light `netcfg eth0 dhcp` is not enough. Android 5.1 uses policy routing
  (netd per-network `ip rule` tables), so repopulating the *main* route
  table does not restore reachability; a populated main table can still be
  fully unreachable. Only the framework rebuilding eth0 at boot reliably
  restores routing (a clean boot comes up with `gateway 192.168.1.253`,
  reachable).
- Worse, `netcfg eth0 dhcp` BLOCKS when the network is fully wedged. An
  earlier version of net_watchdog tried it as a first step and hung inside
  it for 14 minutes, never reaching the reboot. So the loop must contain
  nothing that can stall.

So `net_watchdog.sh` is **reboot-only**, fully on-device and self-contained:

1. Every 30s it checks whether it can actually **reach Massey** (root ping).
   Reachability is the only trustworthy signal; main-table routes can lie.
2. If it cannot reach Massey for ~2 minutes (4 consecutive failures, past a
   boot-grace window so it cannot tight-loop), it **reboots**. The reboot is
   local, so it works even when every network path is dead, and nothing in
   the loop can hang. This eliminates the old "dead until a 30-second DC
   unplug" dead-end: the display no longer needs Massey, the reverse shell,
   or a human to recover. Validated 2026-06-09 by blocking traffic to Massey
   and watching it reboot itself back to health.

`src/auto_recover.py` on Massey (heartbeat stale > 12 min -> reboot over the
reverse shell) is kept as an external backstop, but it should rarely fire now
that the display heals itself. Note its rough edge: it can reboot a display
that has only just booted (heartbeat is briefly stale right after boot), so
it is defence-in-depth, not the primary mechanism.

Superseded: `tp_watchdog.sh` (rebooted the TP-Link bridge, wrong target,
the bridge is not the fault) and the old eth0-bounce recovery recipe. The
reboot-loop-from-Massey approach is no longer the primary recovery. A
hardware swap to a GL.iNet GL-MT300N-V2 is still a
sensible long-term upgrade for the flaky, high-latency WiFi bridge, but is
no longer required to keep the display alive.

## Deployment

Requires ADB over USB (the in-band wireless ADB is blocked by the TP-Link
bridge's 3-address WiFi limitation).

```sh
# Remount /system writable (engineering build allows this)
adb shell 'mount -o remount,rw /system'

# Push /data/local/tmp scripts
adb push deploy/display/supervisor.sh     /data/local/tmp/supervisor.sh
adb push deploy/display/display_remote.sh /data/local/tmp/display_remote.sh
adb push deploy/display/net_watchdog.sh   /data/local/tmp/net_watchdog.sh
adb shell 'chmod 755 /data/local/tmp/supervisor.sh \
                     /data/local/tmp/display_remote.sh \
                     /data/local/tmp/net_watchdog.sh'

# Push the boot hook
adb push deploy/display/install-recovery.sh /system/bin/install-recovery.sh
adb shell 'chmod 750 /system/bin/install-recovery.sh'

# Restore /system read-only (will happen on next reboot regardless)
adb shell 'mount -o remount,ro /system' 2>/dev/null

# Reboot to validate the boot path
adb reboot
adb wait-for-device
adb shell 'while [ "$(getprop sys.boot_completed)" != "1" ]; do sleep 2; done'
adb shell 'busybox ps w | busybox grep -E "supervisor|display_remote|net_watchdog" | busybox grep -v grep'
```

Note: the scripts can also be deployed over the reverse shell without USB
(base64-push through `display_remote.sh`'s dial-in). USB is simplest for a
from-scratch setup; the reverse shell is the no-touch path once the display
is online (stop auto_recover on Massey first to free port 9999).

You should see all three processes within ~60s of boot.

## Logs

- `/data/local/tmp/supervisor.log` is the supervisor's own log
- `/data/local/tmp/net_watchdog.log` records every reachability check that
  failed and every reboot decision (the place to look after a stall)
- `display_remote.sh` is silent by design
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
