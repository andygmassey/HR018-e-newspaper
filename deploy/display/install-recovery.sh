#!/system/bin/sh
# Boot-time setup for Avalue EPD-42S.
# Runs via init's flash_recovery service (class main, oneshot).

log -p i -t boot_init "starting" 2>/dev/null

# 1. Wait for eth0 to have an IP. The kernel/Android handle DHCP at
#    boot; we just poll the property. Bounded at 60s so we never
#    block the supervisor start indefinitely.
ATTEMPT=0
while [ $ATTEMPT -lt 6 ]; do
    ATTEMPT=$((ATTEMPT + 1))
    IP=$(getprop dhcp.eth0.ipaddress)
    if [ -n "$IP" ]; then
        log -p i -t boot_init "eth0 has $IP (after ${ATTEMPT}0s)" 2>/dev/null
        break
    fi
    sleep 10
done

# 2. Start the daemon supervisor. The supervisor respawns
#    display_remote.sh and tp_watchdog.sh if they die, so we don't
#    need to start them directly here.
nohup sh /data/local/tmp/supervisor.sh >/dev/null 2>&1 &

log -p i -t boot_init "supervisor started" 2>/dev/null
