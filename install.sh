#!/bin/sh
# Omen Control Centre installer (Fedora). Run with: sudo sh install.sh
# --lighting-only: helper refuses all fan/* writes (kept across upgrades).
# --allow-fan: lift that restriction explicitly.
# Installs files only. It never writes to the driver or changes any setting.
# DESTDIR=/some/dir installs into a staging tree without root (for testing).
set -eu

D="${DESTDIR:-}"
if [ -z "$D" ] && [ "$(id -u)" != "0" ]; then
    echo "Run with sudo: sudo sh install.sh" >&2
    exit 1
fi
OWN=""
if [ -z "$D" ]; then OWN="-o root -g root"; fi

install -d "$D/usr/share/omen-centre"
# every module the app imports lives in src/; install them all
for f in src/*.py; do
    mode=0644
    [ "$f" = "src/omen-centre.py" ] && mode=0755
    install -m "$mode" "$f" "$D/usr/share/omen-centre/$(basename "$f")"
done

install -d "$D/usr/libexec/omen-centre"
# stage then rename: the privileged helper lands atomically, root-owned
# shellcheck disable=SC2086
install $OWN -m 0755 helper/omenctl "$D/usr/libexec/omen-centre/.omenctl.new"
mv -f "$D/usr/libexec/omen-centre/.omenctl.new" \
    "$D/usr/libexec/omen-centre/omenctl"

install -d "$D/usr/share/polkit-1/actions" "$D/usr/share/applications"
install -m 0644 polkit/com.mananbharti.omencentre.policy \
    "$D/usr/share/polkit-1/actions/com.mananbharti.omencentre.policy"
install -m 0644 data/com.mananbharti.OmenCentre.desktop \
    "$D/usr/share/applications/com.mananbharti.OmenCentre.desktop"

# The lighting-only marker is never removed implicitly (upgrades keep it).
# Only an explicit --allow-fan removes it.
case "${1:-}" in
    --lighting-only)
        install -d -m 0755 "$D/etc/omen-centre"
        : > "$D/etc/omen-centre/lighting-only"
        echo "Lighting-only mode: fan writes refused by the helper." ;;
    --allow-fan)
        rm -f "$D/etc/omen-centre/lighting-only"
        echo "Fan writes allowed (marker removed)." ;;
    "")
        if [ -e "$D/etc/omen-centre/lighting-only" ]; then
            echo "Kept existing lighting-only restriction (use --allow-fan to lift)."
        fi ;;
    *) echo "unknown option: $1" >&2; exit 1 ;;
esac

echo "Installed. Launch 'Omen Control Centre' from the app grid."
echo "Writes ask for your password once, then stay authorized for a few minutes."
