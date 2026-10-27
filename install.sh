#!/bin/sh
# Omen Control Centre installer (Fedora). Run with: sudo sh install.sh
# --lighting-only: helper refuses all fan/* writes (kept across upgrades).
# --allow-fan: lift that restriction explicitly.
# Installs files only. It never writes to the driver or changes any setting.
set -eu

if [ "$(id -u)" != "0" ]; then
    echo "Run with sudo: sudo sh install.sh" >&2
    exit 1
fi

install -d /usr/share/omen-centre
install -m 0755 src/omen-centre.py /usr/share/omen-centre/omen-centre.py
install -m 0644 src/sysfs_backend.py /usr/share/omen-centre/sysfs_backend.py
install -m 0644 src/games_backend.py /usr/share/omen-centre/games_backend.py
install -m 0644 src/omenkey_backend.py /usr/share/omen-centre/omenkey_backend.py

install -d /usr/libexec/omen-centre
# stage then rename: the privileged helper lands atomically, root-owned
install -o root -g root -m 0755 helper/omenctl \
    /usr/libexec/omen-centre/.omenctl.new
mv -f /usr/libexec/omen-centre/.omenctl.new \
    /usr/libexec/omen-centre/omenctl

install -m 0644 polkit/com.mananbharti.omencentre.policy \
    /usr/share/polkit-1/actions/com.mananbharti.omencentre.policy
install -m 0644 data/com.mananbharti.OmenCentre.desktop \
    /usr/share/applications/com.mananbharti.OmenCentre.desktop

# The lighting-only marker is never removed implicitly (upgrades keep it).
# Only an explicit --allow-fan removes it.
case "${1:-}" in
    --lighting-only)
        install -d -m 0755 /etc/omen-centre
        : > /etc/omen-centre/lighting-only
        echo "Lighting-only mode: fan writes refused by the helper." ;;
    --allow-fan)
        rm -f /etc/omen-centre/lighting-only
        echo "Fan writes allowed (marker removed)." ;;
    "")
        if [ -e /etc/omen-centre/lighting-only ]; then
            echo "Kept existing lighting-only restriction (use --allow-fan to lift)."
        fi ;;
    *) echo "unknown option: $1" >&2; exit 1 ;;
esac

echo "Installed. Launch 'Omen Control Centre' from the app grid."
echo "Writes ask for your password once, then stay authorized for a few minutes."
