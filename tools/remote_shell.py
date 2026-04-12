#!/usr/bin/env python3
"""
Remote shell to the e-ink display via reverse TCP connection.

The display runs a persistent reverse-shell daemon that connects to
this server every 30 seconds. When this script is running, the next
connection attempt gives you an interactive root shell on the display.

Works through the TP-Link bridge because the display initiates the
connection (outbound), bypassing the Client-mode inbound limitation.

Usage:
    python3 tools/remote_shell.py              # interactive shell
    echo "pm list packages" | python3 tools/remote_shell.py   # one-shot command

To push a file to the display:
    1. Start the remote shell
    2. On the display shell: cat > /data/local/tmp/file.apk
    3. On your Mac: base64 < file.apk | pbcopy
    4. Paste into the shell (or use the push helper below)

For APK installs:
    python3 tools/remote_shell.py
    Then type: pm install -r /path/to/pushed.apk
"""
import os
import select
import socket
import sys
import threading

PORT = 9999


def recv_and_print(sock):
    """Print data received from the display to stdout."""
    try:
        while True:
            data = sock.recv(4096)
            if not data:
                break
            sys.stdout.write(data.decode("utf-8", errors="replace"))
            sys.stdout.flush()
    except (OSError, ConnectionResetError):
        pass
    print("\n[display disconnected]")
    os._exit(0)


def main():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", PORT))
    srv.listen(1)
    print(f"[waiting for display on port {PORT}... "
          f"display retries every 30s]")

    conn, addr = srv.accept()
    print(f"[connected from {addr} — type commands, Ctrl-C to exit]")
    srv.close()

    # Receive thread
    t = threading.Thread(target=recv_and_print, args=(conn,), daemon=True)
    t.start()

    # Send stdin to display
    try:
        if sys.stdin.isatty():
            while True:
                cmd = input()
                conn.sendall((cmd + "\n").encode())
        else:
            # Pipe mode: send all stdin then wait for output
            data = sys.stdin.read()
            conn.sendall(data.encode())
            conn.sendall(b"exit\n")
            t.join(timeout=5)
    except (EOFError, KeyboardInterrupt):
        pass
    finally:
        conn.close()


if __name__ == "__main__":
    main()
