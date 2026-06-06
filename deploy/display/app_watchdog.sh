#!/system/bin/sh
# /data/local/tmp/app_watchdog.sh
#
# Detects APP-LAYER network loss and recovers it locally.
#
# Root failure mode (observed 2026-06-06): after a boot or a network blip,
# Android's ConnectivityManager intermittently fails to register eth0 as the
# active default network for apps. eth0 is up at the kernel level (root
# processes like ping, nc and the reverse shell all work), but apps get
# "ENETUNREACH (Network is unreachable)" and can never connect. The
# OpenDisplay app then connects-and-drops forever and the panel sticks on
# "waiting for server".
#
# Why this needs a dedicated watchdog:
#   - tp_watchdog.sh checks connectivity with a ROOT-level `nc`, which
#     bypasses the app network layer and always succeeds, so it never
#     detected this.
#   - Massey's auto_recover.py can send a fix, but its only channel is the
#     reverse shell; once eth0 churn kills that too, it goes blind and can
#     never escalate to a reboot. Recovery then required a physical cold
#     boot.
#
# The causal signal, checkable locally: ConnectivityManager's "Active
# default network". A positive network id means apps have a usable route;
# absent / -1 means apps get ENETUNREACH. This is exactly what determines
# the failure, so we key off it directly.
#
# Escalation: try the eth0-bounce + app-restart fix first (fast, ~20s);
# if the app default network is still missing after 3 consecutive checks,
# do a full Android reboot. The reboot is the key piece the old design
# lacked: it runs locally and needs no network, so it works even when
# every remote channel is dead.

LOG=/data/local/tmp/app_watchdog.log
APP=org.opendisplay.android
INTERVAL=120
MAX_FAILS=3

fails=0

log() {
    UP=$(busybox awk '{print int($1)}' /proc/uptime 2>/dev/null)
    echo "[uptime=${UP}s] $1" >> "$LOG"
}

has_app_network() {
    # Positive network id after "Active default network:" means apps have a
    # usable default route. Absent / -1 / 0 means ENETUNREACH for apps.
    dumpsys connectivity 2>/dev/null \
        | busybox grep -E "Active default network: [1-9]" >/dev/null
}

recover() {
    log "no app default network: bouncing eth0 + kicking ConnectivityManager + restarting app"
    ifconfig eth0 down
    sleep 3
    ifconfig eth0 up
    sleep 3
    netcfg eth0 dhcp
    sleep 10
    am broadcast -a android.net.conn.CONNECTIVITY_CHANGE >/dev/null 2>&1
    sleep 2
    am force-stop "$APP" >/dev/null 2>&1
    sleep 1
    am start -n "$APP/.MainActivity" >/dev/null 2>&1
}

log "=== app_watchdog starting ==="
while true; do
    sleep "$INTERVAL"

    if has_app_network; then
        if [ "$fails" -gt 0 ]; then
            log "app default network restored"
        fi
        fails=0
        continue
    fi

    fails=$((fails + 1))
    log "app default network missing (consecutive=$fails)"

    if [ "$fails" -ge "$MAX_FAILS" ]; then
        log "=== escalating to full Android reboot ==="
        reboot
        sleep 60
        continue
    fi

    recover
done
