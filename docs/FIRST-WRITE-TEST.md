# First hardware test: one static keyboard colour

Scope: keyboard colour only. No fan changes, no driver or firmware changes,
no animation. Run each command on its own line. Nothing here is applied
automatically.

## 1. Read current state (read-only, changes nothing)

    ls /sys/devices/platform/omen-rgb-keyboard
    ls /sys/devices/platform/omen-rgb-keyboard/rgb_zones
    grep -r . /sys/devices/platform/omen-rgb-keyboard/rgb_zones 2>&1
    cat /var/lib/omen-rgb-keyboard/state

Send the output back. Test values are chosen only after this.

## 2. Back up current state

    mkdir -p ~/omen-backup
    grep -r . /sys/devices/platform/omen-rgb-keyboard/rgb_zones > ~/omen-backup/rgb_zones.txt 2>&1
    sudo cp /var/lib/omen-rgb-keyboard/state ~/omen-backup/state

## 3. Install (files only, lighting-only helper)

    cd omen-centre
    sudo sh install.sh --lighting-only

Installs: app, helper `/usr/libexec/omen-centre/omenctl`, one polkit action,
one desktop entry, and the marker `/etc/omen-centre/lighting-only` that makes
the helper refuse every `fan/*` key. It writes no setting.

## 4. Test (one Apply)

Launch the app normally (not read-only, not with sudo). Lighting page, pick
one colour for all zones, press Apply once. A normal polkit password dialog
appears. Report what the keyboard did.

## 5. Restore

In the app, set the old colours from `~/omen-backup/rgb_zones.txt` and Apply,
or by hand, per zone (replace RRGGBB with the backed-up value):

    echo RRGGBB | sudo tee /sys/devices/platform/omen-rgb-keyboard/rgb_zones/zone00

Repeat for zone01..zone03. The driver also saves state itself, so the backup
copy of the state file is the fallback.

## Remove

    sudo rm -rf /usr/share/omen-centre /usr/libexec/omen-centre /etc/omen-centre
    sudo rm -f /usr/share/polkit-1/actions/com.mananbharti.omencentre.policy /usr/share/applications/com.mananbharti.OmenCentre.desktop
