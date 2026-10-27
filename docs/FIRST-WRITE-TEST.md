# First hardware test: one static keyboard colour

Scope: keyboard colour only. No fan changes, no driver or firmware changes,
no animation. Run each command on its own line. Nothing here is applied
automatically.

## 1. Current state (read on the real laptop, 14:13)

zone00 #ff0000, zone01-03 #ffffff, brightness 100, animation_mode static,
animation_speed 1, brightness_lock 0. (`all` also shows #ff0000 but it is a
write-style attribute and is NOT used for restore.) Do not `cat` the state
file: it is binary.

## 2. Back up (nothing printed, nothing overwritten)

`mkdir` without -p fails if the folder already exists, so an old backup is never
reused or overwritten. If it fails, change the 1 to 2.

    mkdir ~/omen-backup-1
    grep -r . /sys/devices/platform/omen-rgb-keyboard/rgb_zones > ~/omen-backup-1/rgb_zones.txt 2>&1
    cp -n -p /var/lib/omen-rgb-keyboard/state ~/omen-backup-1/state

If that last line says permission denied, use these two instead:

    sudo cp -n -p /var/lib/omen-rgb-keyboard/state ~/omen-backup-1/state
    sudo chown "$USER": ~/omen-backup-1/state

Check the copy matches (prints two hash lines, they must be equal):

    sha256sum /var/lib/omen-rgb-keyboard/state ~/omen-backup-1/state

## What the driver does on a zone write (read from the driver source)

Source: OmenLinux/omen-rgb-keyboard, src/zones/omen_zones.c and
src/state/omen_state.c (current default branch, read 14:16).
- Zone files read back the colour from the keyboard as `#rrggbb`. They accept
  `RRGGBB` (hex, parsed with kstrtoul base 16, max FFFFFF). The `all` file reads
  back zone00's colour, so it is not an independent value.
- A zone write stops any driver animation, sets the mode to static, scales the
  colour by the current brightness (100 here, so no change), writes it to
  the keyboard through HP WMI, then saves the whole driver state (mode, speed,
  brightness, the four zone colours, gradient config) to
  /var/lib/omen-rgb-keyboard/state, truncating the file. That is why the state
  file is backed up first.
- The state file is used by the driver when it is next loaded; this app never
  edits it.

## 3. Install (files only, lighting-only helper)

    cd omen-centre
    sudo sh install.sh --lighting-only

Installs: app, helper `/usr/libexec/omen-centre/omenctl`, one polkit action,
one desktop entry, and the marker `/etc/omen-centre/lighting-only` that makes
the helper refuse every `fan/*` key. It writes no setting.

## 4. Test (one Apply)

Launch the app normally (not read-only, not with sudo). Lighting page:
click the first tile (Z0, red). In the small picker type 0000FF into the
Hex box, press Enter (or the Set button), then the picker's Apply button.
Z0 turns blue in the app but nothing is written yet. Then press the footer
Apply once. Leave Z1-Z3, brightness, effect and speed alone. A normal polkit password dialog
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
