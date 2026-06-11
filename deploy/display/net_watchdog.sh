#!/system/bin/sh
# /data/local/tmp/net_watchdog.sh
#
# On-device network self-heal for the recurring "waiting for server"
# failure. Reboot-only by design: nothing in its loop can hang, so it
# always reaches the recovery step. This is what lets the display recover
# with NOTHING plugged in (no Massey, no reverse shell, no USB).
#
# Ground truth (adb, 2026-06-09): eth0 loses its DHCP lease / network
# config. dhcpcd drops, the interface stays UP at L2 but has no IPv4
# address and no routes, so all IPv4 dies (even root): the OpenDisplay app
# and the reverse shell both go dark and the panel sticks on "waiting for
# server". (Not the app-only ENETUNREACH or the 60s bridge lease earlier
# docs claimed; the live lease is 24h from the router 192.168.1.1.)
#
# Why reboot-only (learned the hard way, 2026-06-09): an earlier version
# tried `netcfg eth0 dhcp` as a light recovery first. That was a mistake:
#   - `netcfg eth0 dhcp` BLOCKS when the network is fully wedged, which
#     stalls this loop before it can ever escalate to a reboot. That left
#     the display dead despite the watchdog "running".
#   - Even when it returns, it only repopulates the main route table;
#     Android 5.1 uses policy routing (netd per-network `ip rule` tables),
#     so the routes are not actually used and the display stays
#     unreachable.
# The only thing observed to reliably restore eth0 (including the netd
# policy routing) is the framework rebuilding it at boot. So: detect by
# real reachability, and reboot. Simple and unstallable.
#
# Detection: can we actually reach Massey (root ping). Reachability is the
# only trustworthy signal; main-table routes can lie.
# Recovery: after REBOOT_AFTER consecutive unreachable checks (past a boot
# grace window so it cannot tight-loop), reboot.
#
# Replaces tp_watchdog.sh (which rebooted the TP-Link bridge: wrong target).

LOG=/data/local/tmp/net_watchdog.log
TARGET=192.168.1.72
INTERVAL=30
# Be PATIENT. The WiFi bridge flaps: it can be unreachable for several
# minutes and then recover on its own (the OpenDisplay app reconnects by
# itself, no reboot needed). Rebooting on a flap is pointless (the bridge
# re-associates either way) and disruptive, and a short threshold caused a
# reboot loop every ~4-5 min (observed 2026-06-09). Only reboot for a
# genuine long dead-end like the original hours-long stall. The reboot is
# the LAST resort; Massey's auto_recover (heartbeat) is a parallel backstop.
REBOOT_AFTER=20     # ~10 min of CONTINUOUS unreachability before a reboot
GRACE_UPTIME=240    # don't count failures in the first 4 min after a boot

logln() {
    UP=$(busybox awk '{print int($1)}' /proc/uptime 2>/dev/null)
    echo "[uptime=${UP}s] $1" >> "$LOG"
}

uptime_secs() {
    busybox awk '{print int($1)}' /proc/uptime 2>/dev/null
}

reachable() {
    # The WiFi bridge runs 1.5-2.5s latency; generous timeout, two tries.
    ping -c 1 -W 6 $TARGET >/dev/null 2>&1 && return 0
    ping -c 1 -W 6 $TARGET >/dev/null 2>&1
}

logln "=== net_watchdog starting (pid $$, reboot-only) ==="
trap 'logln "=== net_watchdog exiting (signal) ==="; exit 0' TERM INT

down_streak=0
while true; do
    sleep $INTERVAL

    if reachable; then
        if [ $down_streak -ne 0 ]; then
            logln "reachable again, recovered (was streak=$down_streak)"
        fi
        down_streak=0
        continue
    fi

    UP=$(uptime_secs)
    if [ -z "$UP" ]; then UP=0; fi
    if [ "$UP" -lt "$GRACE_UPTIME" ]; then
        logln "unreachable but within boot grace (uptime ${UP}s); not counting"
        continue
    fi

    down_streak=$((down_streak + 1))
    logln "Massey unreachable (streak=$down_streak, uptime ${UP}s)"

    if [ $down_streak -ge $REBOOT_AFTER ]; then
        logln "unreachable $down_streak cycles; rebooting so the framework rebuilds eth0"
        sync
        reboot
    fi
done
