#!/usr/bin/env python3
"""Read-only: decode a COPY of /var/lib/omen-rgb-keyboard/state and compare
it with what the keyboard reports now. Writes nothing, prints no raw bytes.

Layout from OmenLinux/omen-rgb-keyboard src/state/omen_state.h:
int mode, int speed, int brightness, 4 x {b,g,r}, gradient config.
The driver reads exactly sizeof(struct) = 228 bytes.

Usage: decode_state.py <copy-of-state-file> [sysfs_root]
"""
import os
import struct
import sys

SYSFS = "/sys/devices/platform/omen-rgb-keyboard"
MODES = ["static", "breathing", "rainbow", "wave", "pulse", "chase",
         "sparkle", "candle", "aurora", "disco", "gradient", "spectrum"]


def live(root, name):
    try:
        with open(os.path.join(root, "rgb_zones", name)) as fh:
            v = fh.read().strip().lstrip("#").lower()
        return v if len(v) == 6 else None
    except OSError:
        return None


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    root = argv[2] if len(argv) > 2 else SYSFS
    data = open(argv[1], "rb").read()
    print("state file size: %d bytes (driver expects 228)" % len(data))
    if len(data) < 24:
        print("too short to decode")
        return 1
    mode, speed, bright = struct.unpack_from("<iii", data, 0)
    mname = MODES[mode] if 0 <= mode < len(MODES) else "invalid(%d)" % mode
    print("saved mode=%s speed=%d brightness=%d" % (mname, speed, bright))
    differ = False
    for z in range(4):
        b, g, r = data[12 + 3 * z: 15 + 3 * z]
        saved = "%02x%02x%02x" % (r, g, b)
        now = live(root, "zone0%d" % z)
        same = (now == saved)
        differ |= not same
        print("zone0%d saved #%s  keyboard now #%s  %s" % (
            z, saved, now, "same" if same else "DIFFERENT"))
    if len(data) != 228:
        print("RESULT: size differs from 228; driver would ignore this file")
        return 1
    print("RESULT: " + ("DIFFERENT - do not Apply yet, send this output"
                        if differ else "saved colours match the keyboard"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
