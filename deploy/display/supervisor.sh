#!/system/bin/sh
# /data/local/tmp/supervisor.sh
#
# Watchdog for the on-device daemons (display_remote.sh and tp_watchdog.sh).
# Started by /system/bin/install-recovery.sh on boot. Runs forever; every
# 60 seconds checks whether each daemon is in the process table and
# restarts any that have died. Logs to /data/local/tmp/supervisor.log.
#
# Why this exists: Android has been observed running for 7+ continuous
# days with both daemons silently dead. install-recovery.sh only fires
# at boot, so once a daemon exits there's nothing on-device that brings
# it back. The Massey-side auto_recover daemon depends on
# display_remote.sh dialing in, so without this supervisor, a single
# daemon crash takes the entire recovery loop offline until physical
# reboot of the OS (not just panel power-cycle).
#
# Stock Android `ps` does not include script arguments, so we use
# `busybox ps w` to match the script name in the cmdline.

LOG=/data/local/tmp/supervisor.log
DAEMONS="display_remote.sh tp_watchdog.sh app_watchdog.sh"
INTERVAL=60

log() {
    UP=$(busybox awk '{print int($1)}' /proc/uptime 2>/dev/null)
    echo "[uptime=${UP}s pid=$$] $1" >> "$LOG"
}

# Returns 0 if a process matching $1 is running, 1 otherwise.
# Excludes supervisor itself and the grep process.
is_running() {
    busybox ps w 2>/dev/null \
        | busybox grep -F "$1" \
        | busybox grep -v "supervisor" \
        | busybox grep -v "grep" \
        > /dev/null
}

start_if_missing() {
    name=$1
    script="/data/local/tmp/$name"
    if is_running "$name"; then
        return
    fi
    if [ ! -x "$script" ]; then
        log "WARN: $script not executable, skipping"
        return
    fi
    log "starting $name"
    nohup sh "$script" >/dev/null 2>&1 &
}

log "=== supervisor starting ==="
trap 'log "=== supervisor exiting (signal trapped) ==="; exit 0' TERM INT

while true; do
    for d in $DAEMONS; do
        start_if_missing "$d"
    done
    sleep "$INTERVAL"
done
