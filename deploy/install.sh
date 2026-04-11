#!/bin/sh
#
# macOS install script for HR018 — E-Newspaper backend.
#
# Run this from the project directory on the host that will run the
# OpenDisplay server (an always-on Mac is ideal — Mac mini, etc.):
#
#   git clone https://github.com/<you>/e-newspaper.git
#   cd e-newspaper
#   ./deploy/install.sh
#
# Idempotent — safe to re-run after pulling updates.

set -e

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
VENV="$PROJECT_DIR/.venv"
LAUNCH_DIR="$HOME/Library/LaunchAgents"

echo "Project: $PROJECT_DIR"

# 1. Verify Python 3.11+
if ! command -v python3.11 >/dev/null 2>&1; then
    echo "ERROR: python3.11 not found. Install with: brew install python@3.11"
    exit 1
fi
PY_VERSION=$(python3.11 --version 2>&1)
echo "Using $PY_VERSION"

# 2. Create / refresh venv
if [ ! -d "$VENV" ]; then
    echo "Creating venv at $VENV"
    python3.11 -m venv "$VENV"
fi
"$VENV/bin/pip" install --upgrade pip --quiet

# 3. Install dependencies
echo "Installing dependencies..."
"$VENV/bin/pip" install --quiet \
    requests \
    beautifulsoup4 \
    Pillow \
    "py-opendisplay @ git+https://github.com/balloob/py-opendisplay@wifi-server"

# 4. Smoke test the scraper
echo "Smoke test: scraping today's papers..."
"$VENV/bin/python" src/scraper.py
"$VENV/bin/python" src/processor.py

# 5. macOS Application Firewall allow rule
#
# If the firewall is enabled, unknown binaries silently drop incoming
# connections after the TCP handshake (they complete the 3-way handshake
# at kernel level, then RST after ~3 seconds). For Homebrew Python this
# means OpenDisplay clients connect, are accepted, then kicked without
# ever reaching user-space. Explicitly allow the Python.app bundle we're
# using so incoming connections are actually delivered to us.
#
# Requires sudo; if we don't have passwordless sudo, print a warning and
# leave the rule unchanged — the user can add it manually later.
PY_APP_BUNDLE="$("$VENV/bin/python" -c 'import sys, os, pathlib; p = pathlib.Path(os.path.realpath(sys.executable)); print(p.parents[2])')"
if [ -d "$PY_APP_BUNDLE" ]; then
    FW_CLI="/usr/libexec/ApplicationFirewall/socketfilterfw"
    if [ -x "$FW_CLI" ]; then
        echo "Registering Python with macOS Application Firewall: $PY_APP_BUNDLE"
        if sudo -n "$FW_CLI" --add "$PY_APP_BUNDLE" 2>/dev/null; then
            sudo -n "$FW_CLI" --unblockapp "$PY_APP_BUNDLE" >/dev/null 2>&1 || true
        else
            echo "  NOTE: could not add firewall rule without sudo. If OpenDisplay clients"
            echo "  connect but never receive images, run manually:"
            echo "    sudo $FW_CLI --add '$PY_APP_BUNDLE'"
            echo "    sudo $FW_CLI --unblockapp '$PY_APP_BUNDLE'"
        fi
    fi
fi

# 6. Install launchd plists (rewriting paths to match this install)
mkdir -p "$LAUNCH_DIR"
for plist in com.e-newspaper.server com.e-newspaper.daily-update; do
    src="$PROJECT_DIR/deploy/${plist}.plist"
    dst="$LAUNCH_DIR/${plist}.plist"
    echo "Installing $plist"
    sed "s|__INSTALL_DIR__|$PROJECT_DIR|g" "$src" > "$dst"

    # Reload (unload if already loaded — ignore errors)
    launchctl unload "$dst" 2>/dev/null || true
    launchctl load "$dst"
done

# 6. Final status
echo
echo "=== Installation complete ==="
echo "Server status:"
launchctl list | grep e-newspaper || true
echo
echo "The OpenDisplay server is now running on port 2446."
echo "The display will discover it via mDNS once it's on the same LAN."
echo
echo "Useful commands:"
echo "  tail -f $PROJECT_DIR/server.log"
echo "  tail -f $PROJECT_DIR/scraper.log"
echo "  launchctl start com.e-newspaper.daily-update    # force update now"
echo "  launchctl unload ~/Library/LaunchAgents/com.e-newspaper.server.plist  # stop server"
