# First hardware test: one static keyboard colour

Scope: keyboard colour only. No fan changes, no driver or firmware changes,
no animation. Run each command on its own line. Nothing here is applied
automatically.

## 1. Current state (read on the real laptop, 14:13)

zone00 #ff0000, zone01-03 #ffffff, brightness 100, animation_mode static,
animation_speed 1, brightness_lock 0. (`all` also shows #ff0000 but it is a
write-style attribute and is NOT used for restore.) Do not `cat` the state
file: it is binary.

## 2. Back up (unique folder, nothing overwritten, nothing printed)

Run these one at a time in the same terminal. Each line only runs if the
previous one worked and you are inside the new backup folder.

    D=$HOME/omen-backup-$(date +%Y%m%d-%H%M%S); mkdir "$D" && cd "$D" && pwd

    [ "$PWD" = "$D" ] && [ ! -e rgb_zones.txt ] && grep -r . /sys/devices/platform/omen-rgb-keyboard/rgb_zones > rgb_zones.txt 2>&1 && ls -l rgb_zones.txt

    [ "$PWD" = "$D" ] && [ ! -e state ] && sudo cp -n -p /var/lib/omen-rgb-keyboard/state state && sudo chown "$USER": state && sudo sha256sum /var/lib/omen-rgb-keyboard/state state

The two hashes must be equal. If any line prints nothing or an error, stop and
send the output; do not carry on.

## 2b. Check the saved state agrees with the keyboard (read-only)

The driver rewrites all four zones from its own memory on any zone write, and
that memory is loaded from the state file. If the file disagrees with what the
keyboard shows, an Apply could change zones you did not touch. This decodes the
backup copy only:

    python3 ~/Downloads/omen-centre/tools/decode_state.py "$D/state"

If it ends "saved colours match the keyboard", continue. If it says DIFFERENT,
stop and send the output; no Apply until we agree what to do.

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
- Every zone write first calls animation_stop(), which rewrites ALL FOUR zones
  to the keyboard from the driver's in-memory colours (loaded from the state
  file at module load, see step 2b), then writes the target zone. So a write
  to Z0 can also rewrite Z1-Z3. The installed module version is not confirmed;
  this is the current default branch.

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

Recovery uses the saved values in `rgb_zones.txt` (zone00 #ff0000, zone01-03
#ffffff), written explicitly for all four zones so none is left to the
driver's memory. In the app set Z0 FF0000 and Apply, then check; or by hand:

    echo FF0000 | sudo tee /sys/devices/platform/omen-rgb-keyboard/rgb_zones/zone00
    echo FFFFFF | sudo tee /sys/devices/platform/omen-rgb-keyboard/rgb_zones/zone01
    echo FFFFFF | sudo tee /sys/devices/platform/omen-rgb-keyboard/rgb_zones/zone02
    echo FFFFFF | sudo tee /sys/devices/platform/omen-rgb-keyboard/rgb_zones/zone03

Check afterwards:

    grep -r . /sys/devices/platform/omen-rgb-keyboard/rgb_zones/zone0*

Expected zone00 #ff0000, zone01-03 #ffffff. The backed-up state file is a
reference only; do not copy it back or edit it by hand (the driver rewrites it
on each zone write and the explicit writes above leave it consistent).

## Remove

    sudo rm -rf /usr/share/omen-centre /usr/libexec/omen-centre /etc/omen-centre
    sudo rm -f /usr/share/polkit-1/actions/com.mananbharti.omencentre.policy /usr/share/applications/com.mananbharti.OmenCentre.desktop
