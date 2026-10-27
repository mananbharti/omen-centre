#!/bin/sh
# Omen Control Centre installer (Fedora). Run with: sudo sh install.sh
set -eu

if [ "$(id -u)" != "0" ]; then
    echo "Run with sudo: sudo sh install.sh" >&2
    exit 1
fi

install -d /usr/share/omen-centre
install -m 0755 src/omen-centre.py /usr/share/omen-centre/omen-centre.py
install -m 0644 src/sysfs_backend.py /usr/share/omen-centre/sysfs_backend.py

install -d /usr/libexec/omen-centre
install -o root -g root -m 0755 helper/omenctl /usr/libexec/omen-centre/omenctl

install -m 0644 polkit/com.mananbharti.omencentre.policy \
    /usr/share/polkit-1/actions/com.mananbharti.omencentre.policy
install -m 0644 data/com.mananbharti.OmenCentre.desktop \
    /usr/share/applications/com.mananbharti.OmenCentre.desktop

echo "Installed. Launch 'Omen Control Centre' from the app grid."
echo "Writes ask for your password once, then stay authorized for a few minutes."
