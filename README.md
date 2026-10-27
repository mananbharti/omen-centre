# Omen Control Centre

Native Linux control app for HP Omen laptops running the
[OmenLinux omen-rgb-keyboard](https://github.com/OmenLinux/omen-rgb-keyboard)
kernel driver. Built and tested on Fedora 44 Workstation (HP Omen 16-wf1xxx).

Python 3 + GTK 4 + Libadwaita. No dependencies beyond a standard Fedora
Workstation install. Follows the system light/dark style.

## Features

**Lighting**
- Per-zone colors (4 zones) and all-zone color
- Brightness 0-100
- 12 animations (static, breathing, rainbow, wave, pulse, chase, sparkle,
  candle, aurora, disco, gradient, spectrum) with speed 1-10
- Custom gradient configuration
- Brightness lock (stops the desktop dimming the keyboard when idle)
- "Identify zones" button: lights zones red/green/blue/white so you can see
  which key area each zone covers (mapping varies by keyboard)

**Fans & performance**
- Live CPU / GPU fan RPM
- Max fan toggle
- Thermal profiles: Silent / Normal / Performance (same presets as the
  GNOME power menu via power-profiles-daemon)
- CPU power limits PL1/PL2 (watts), with restore-defaults
- Custom fan curve editor (2-8 points) - shown only when the laptop's fan
  interface supports it; classic-interface models get an honest
  "not supported on this model" note instead of a dead control

**Vitals**
- CPU package temperature, GPU temperature (when the GPU driver exposes it),
  CPU load, memory usage

**Not available from Linux on this machine** (named in-app, no fake toggles):
Network Booster, overclocking/undervolting, GPU display mode (MUX) switching.
**Not yet implemented:** game library, in-app Omen-key shortcut binding
(bindable today in GNOME Settings > Keyboard > Custom Shortcuts).

## Security model

- The GUI runs unprivileged and only reads sysfs.
- All writes go through `omenctl`, a small root helper that validates every
  key against a fixed allowlist and every value against typed, bounded
  validators (hex colors, integer ranges, curve grammar). No shell strings,
  no arbitrary paths.
- The helper runs via pkexec under a polkit policy that requires admin
  authentication (`auth_admin_keep`): one password prompt, remembered for a
  few minutes. No blanket passwordless grant.
- Power-limit bounds reference the CPU spec (i7-14650HX: 55 W base,
  157 W max turbo per Intel ARK); "Restore defaults" always hands control
  back to the BIOS.

## Install

```bash
sudo sh install.sh
```

Then launch **Omen Control Centre** from the app grid.

Requirements: the `omen-rgb-keyboard` DKMS module loaded
(`lsmod | grep omen_rgb_keyboard`), python3, gtk4, libadwaita, pkexec
(all present on Fedora Workstation).

## Development / testing without an Omen laptop

```bash
# unit tests for the privileged helper (mock sysfs, no root)
python3 tests/test_omenctl.py

# run the GUI against a mock sysfs tree
mkdir -p /tmp/omen-mock/{rgb_zones,fan}
for f in zone00 zone01 zone02 zone03 all brightness animation_mode \
         animation_speed gradient_config brightness_lock mute_led mute_state; do
  echo -n "" > /tmp/omen-mock/rgb_zones/$f
done
for f in max_fan thermal_profile power_limits fan_curve fan_curve_enable \
         fan_temp_zone cpu_fan_rpm gpu_fan_rpm; do
  echo -n "" > /tmp/omen-mock/fan/$f
done
echo -n "3000" > /tmp/omen-mock/fan/cpu_fan_rpm
echo -n "3300" > /tmp/omen-mock/fan/gpu_fan_rpm
echo -n "normal" > /tmp/omen-mock/fan/thermal_profile
echo -n "FFFFFF" > /tmp/omen-mock/rgb_zones/all
echo -n "100" > /tmp/omen-mock/rgb_zones/brightness
echo -n "static" > /tmp/omen-mock/rgb_zones/animation_mode
echo -n "5" > /tmp/omen-mock/rgb_zones/animation_speed
echo -n "30:20 50:40 70:70 90:100" > /tmp/omen-mock/fan/fan_curve
echo -n "0" > /tmp/omen-mock/fan/fan_curve_enable
OMEN_SYSFS_ROOT=/tmp/omen-mock OMEN_DIRECT_WRITE=1 \
OMENCTL=$PWD/helper/omenctl python3 src/omen-centre.py
```

## Verification status (honest)

Verified on the target machine (2026-10-27): module load, all-zone color,
single-zone color, max-fan ramp and release, thermal-profile write, RPM
reads. Driver-supported but not yet exercised on this machine: zones 1-3,
brightness slider, animations, gradient, brightness lock, power limits,
persistence across reboot, cooling response under sustained load.

## License

GPL-3.0 (matching the kernel driver).
