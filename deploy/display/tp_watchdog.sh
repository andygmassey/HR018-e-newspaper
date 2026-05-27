#!/system/bin/sh
# TP-Link bridge watchdog: monitors connectivity via TCP,
# reboots the TP-Link via admin UI when WiFi drops, and kicks
# Android ConnectivityManager after recovery.

COOKIE_FILE="/data/local/tmp/tp_cookie.txt"
TP="http://192.168.1.253"
TARGET="192.168.1.72"
TARGET_PORT=2446
INTERVAL=60
FAIL_THRESHOLD=3
REBOOT_WAIT=90

consecutive_failures=0
was_down=0

while true; do
    sleep $INTERVAL

    /system/xbin/nc -z -w 5 $TARGET $TARGET_PORT 2>/dev/null
    if [ $? -eq 0 ]; then
        if [ $was_down -eq 1 ]; then
            # Just recovered: kick ConnectivityManager so apps see the network
            am broadcast -a android.net.conn.CONNECTIVITY_CHANGE >/dev/null 2>&1
            log -p i -t tp_watchdog "connectivity restored, broadcast CONNECTIVITY_CHANGE" 2>/dev/null
            was_down=0
        fi
        consecutive_failures=0
        continue
    fi

    was_down=1
    consecutive_failures=$((consecutive_failures + 1))

    if [ $consecutive_failures -ge $FAIL_THRESHOLD ]; then
        log -p w -t tp_watchdog "rebooting TP-Link ($consecutive_failures failures)" 2>/dev/null
        ifconfig eth0 192.168.1.100 netmask 255.255.255.0 up 2>/dev/null
        COOKIE=$(cat $COOKIE_FILE 2>/dev/null)
        if [ -n "$COOKIE" ]; then
            busybox wget -q -O /dev/null \
                --header "Cookie: Authorization=$COOKIE" \
                --header "Referer: $TP/userRpm/SysRebootRpm.htm" \
                "$TP/userRpm/SysRebootRpm.htm?Reboot=%D6%D8%C6%F4%C2%B7%D3%C9%C6%F7" 2>/dev/null
        fi
        sleep $REBOOT_WAIT
        netcfg eth0 dhcp 2>/dev/null
        sleep 8
        # Kick ConnectivityManager after DHCP
        am broadcast -a android.net.conn.CONNECTIVITY_CHANGE >/dev/null 2>&1
        consecutive_failures=0
    fi
done
