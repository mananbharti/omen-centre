# First hardware test: one static keyboard colour

Scope: keyboard colour only. No fan changes, no driver or firmware changes,
no animation. Run each command on its own line. Nothing here is applied
automatically.

## 1. Current state (read on the real laptop, 14:13)

zone00 #ff0000, zone01-03 #ffffff, brightness 100, animation_mode static,
animation_speed 1, brightness_lock 0. (`all` also shows #ff0000 but it is a
write-style attribute and is NOT used for restore.) Do not `cat` the state
file: it is binary.

## 2. Back up (byte-preserving, nothing printed)

    mkdir -p ~/omen-backup
    grep -r . /sys/devices/platform/omen-rgb-keyboard/rgb_zones > ~/omen-backup/rgb_zones.txt 2>&1
    sudo cp -p /var/lib/omen-rgb-keyboard/state ~/omen-backup/state
    sudo chown $USER ~/omen-backup/state
    ls -l ~/omen-backup

## 3. Install (files only, lighting-only helper)

    cd omen-centre
    sudo sh install.sh --lighting-only

Installs: app, helper `/usr/libexec/omen-centre/omenctl`, one polkit action,
one desktop entry, and the marker `/etc/omen-centre/lighting-only` that makes
the helper refuse every `fan/*` key. It writes no setting.

## 4. Test (one Apply)

Launch the app normally (not read-only, not with sudo). Lighting page:
change only the first zone (Z0, now red) to blue 0000FF. Leave Z1-Z3,
brightness, effect and speed alone. Press Apply once. A normal polkit password dialog
appears. Report what the keyboard did.

## 5. Restore

In the app set Z0 back to red FF0000 and Apply. Or by hand:

    echo FF0000 | sudo tee /sys/devices/platform/omen-rgb-keyboard/rgb_zones/zone00

Check afterwards:

    grep -r . /sys/devices/platform/omen-rgb-keyboard/rgb_zones/zone0*

Expected zone00 #ff0000, zone01-03 #ffffff. The backup copy of the state file
is a fallback only; do not edit that file by hand.

## Remove

    sudo rm -rf /usr/share/omen-centre /usr/libexec/omen-centre /etc/omen-centre
    sudo rm -f /usr/share/polkit-1/actions/com.mananbharti.omencentre.policy /usr/share/applications/com.mananbharti.OmenCentre.desktop
