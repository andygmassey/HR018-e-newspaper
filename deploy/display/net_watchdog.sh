#!/system/bin/sh
# /data/local/tmp/net_watchdog.sh
#
# Keeps the display's network alive, fully self-contained. This is the
# primary self-healing daemon for the recurring "waiting for server"
# failure, and the thing that lets the display recover with NOTHING
# plugged in (no Massey, no reverse shell, no USB).
#
# Ground truth (captured over adb 2026-06-09, in the failure state):
# eth0 loses its DHCP lease / network config. dhcpcd drops, eth0 loses
# its IPv4 address and routes, and all IPv4 dies, even at root, so both
# the OpenDisplay app and the reverse shell go dark and the panel sticks
# on "waiting for server". Earlier theories (60s bridge lease, app-only
# ENETUNREACH) were wrong.
#
# Detection: the ONLY trustworthy signal is whether the display can
# actually reach Massey at the IP layer. Android 5.1 uses policy routing
# (netd installs per-network `ip rule` tables), so a populated main route
# table can still be unreachable if the framework has torn the eth0
# network down. So health = can root ping Massey.
#
# Recovery, escalating:
#   1. `netcfg eth0 dhcp` -- light: re-acquire the lease. Fixes the common
#      case where dhcpcd just died but the framework network is intact.
#   2. If that does not restore reachability within REBOOT_AFTER cycles,
#      reboot. A reboot has the Android framework rebuild eth0's network
#      from scratch (including the netd policy routing), which is the only
#      thing observed to reliably restore routing. The reboot is issued
#      locally, so it works even when every network path is dead -- that
#      is what breaks the "dead until physical unplug" dead-end that used
#      to need a 30-second DC power-cycle.
#
# Replaces tp_watchdog.sh, whose remedy was to reboot the TP-Link bridge:
# wrong target (the bridge is not the fault).

LOG=/data/local/tmp/net_watchdog.log
IFACE=eth0
TARGET=192.168.1.72
INTERVAL=45
REBOOT_AFTER=4

logln() {
    UP=$(busybox awk '{print int($1)}' /proc/uptime 2>/dev/null)
    echo "[uptime=${UP}s] $1" >> "$LOG"
}

reachable() {
    # The WiFi bridge runs 1.5-2.5s latency, so use a generous timeout
    # and try twice before declaring a cycle unreachable.
    ping -c 1 -W 6 $TARGET >/dev/null 2>&1 && return 0
    ping -c 1 -W 6 $TARGET >/dev/null 2>&1
}

logln "=== net_watchdog starting (pid $$) ==="
trap 'logln "=== net_watchdog exiting (signal) ==="; exit 0' TERM INT

down_streak=0
while true; do
    sleep $INTERVAL

    if reachable; then
        if [ $down_streak -ne 0 ]; then
            logln "reachable again, recovered"
        fi
        down_streak=0
        continue
    fi

    down_streak=$((down_streak + 1))
    logln "Massey unreachable (streak=$down_streak); re-DHCP eth0"
    netcfg $IFACE dhcp >/dev/null 2>&1
    sleep 6
    am broadcast -a android.net.conn.CONNECTIVITY_CHANGE >/dev/null 2>&1

    if reachable; then
        logln "recovered via re-DHCP"
        down_streak=0
        continue
    fi

    if [ $down_streak -ge $REBOOT_AFTER ]; then
        logln "still unreachable after $down_streak cycles; rebooting so the framework rebuilds eth0"
        sync
        reboot
    fi
done
