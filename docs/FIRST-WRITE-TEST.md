# First hardware test: one static keyboard colour

Scope: keyboard colour only. No fan changes, no driver or firmware changes,
no animation. Run each command on its own line. Nothing here is applied
automatically.

## 1. Current state (read on the real laptop, 14:13)

zone00 #ff0000, zone01-03 #ffffff, brightness 100, animation_mode static,
animation_speed 1, brightness_lock 0. (`all` also shows #ff0000 but it is a
write-style attribute and is NOT used for restore.) Do not `cat` the state
file: it is binary.

## 2. Back up (one line; unique folder; every step gated)

    D=$HOME/omen-backup-$(date +%Y%m%d-%H%M%S) && mkdir "$D" && grep -r . /sys/devices/platform/omen-rgb-keyboard/rgb_zones > "$D/rgb_zones.txt" 2>&1 && sudo cp -n -p -- /var/lib/omen-rgb-keyboard/state "$D/state" && sudo chown "$USER": "$D/state" && sudo cmp -s -- /var/lib/omen-rgb-keyboard/state "$D/state" && echo "BACKUP OK: $D"

Only "BACKUP OK" means it worked (exact byte comparison, prints nothing else).
Anything else: stop and send the output. mkdir has no -p, so an existing folder
is never reused; cp -n never overwrites.

## 2b. Version evidence (read-only, optional)

    ( cd ~/omen-rgb-keyboard && git log -1 --format='src %h %cs' ); modinfo omen_rgb_keyboard | grep -E '^(filename|version|srcversion)'; dkms status

Evidence only. Matching source and version does not prove the loaded module or
its in-memory colours equal the files on disk.

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
  file at module load), then writes the target zone. So a write to Z0 can
  also rewrite Z1-Z3 with colours that differ from what the keyboard shows
  now. Worst case is cosmetic and recoverable with the explicit writes in
  Restore, but it is a real consequence to accept before Apply. The installed
  module version is not confirmed; this is the current default branch. This
  app flashes or replaces no firmware; the driver talks to the keyboard
  through the HP WMI interface.

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
driver's memory. Restore: one line writes all four zones and reads each back; it prints
"RESTORE OK" only if every write and readback matched, and stops at the first
failure:

    Z=/sys/devices/platform/omen-rgb-keyboard/rgb_zones; w(){ echo "$2" | sudo tee "$Z/$1" >/dev/null && [ "$(cat "$Z/$1")" = "#$(echo "$2" | tr A-F a-f)" ]; }; w zone00 FF0000 && w zone01 FFFFFF && w zone02 FFFFFF && w zone03 FFFFFF && echo "RESTORE OK"

(Each write also makes the driver rewrite all zones from memory first, so the
last write wins and brief flicker is possible.) Without "RESTORE OK", send the
output.

Check afterwards:

    grep -r . /sys/devices/platform/omen-rgb-keyboard/rgb_zones/zone0*

Expected zone00 #ff0000, zone01-03 #ffffff. The backed-up state file is a
reference only; do not copy it back or edit it by hand (the driver rewrites it
on each zone write and the explicit writes above leave it consistent).

## Remove

    sudo rm -rf /usr/share/omen-centre /usr/libexec/omen-centre /etc/omen-centre
    sudo rm -f /usr/share/polkit-1/actions/com.mananbharti.omencentre.policy /usr/share/applications/com.mananbharti.OmenCentre.desktop
