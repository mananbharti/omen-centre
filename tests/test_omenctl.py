#!/usr/bin/env python3
"""Unit tests for the omenctl privileged helper against a mock sysfs tree."""
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OMENCTL = os.path.join(ROOT, "helper", "omenctl")

ALL_KEYS = [
    "rgb_zones/zone00", "rgb_zones/zone01", "rgb_zones/zone02",
    "rgb_zones/zone03", "rgb_zones/all", "rgb_zones/brightness",
    "rgb_zones/animation_mode", "rgb_zones/animation_speed",
    "rgb_zones/gradient_config", "rgb_zones/brightness_lock",
    "rgb_zones/mute_led", "rgb_zones/mute_state",
    "fan/max_fan", "fan/thermal_profile",
    "fan/fan_curve", "fan/fan_curve_enable", "fan/fan_temp_zone",
]


def run_ctl(root, *args):
    env = dict(os.environ, OMEN_SYSFS_ROOT=root)
    return subprocess.run(["python3", OMENCTL] + list(args),
                          capture_output=True, text=True, env=env)


class OmenCtlTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="omen-mock-")
        for key in ALL_KEYS:
            path = os.path.join(self.tmp, key)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as fh:
                fh.write("")

    def read_mock(self, key):
        with open(os.path.join(self.tmp, key)) as fh:
            return fh.read().strip()

    def test_valid_writes(self):
        cases = [
            ("rgb_zones/zone00", "FF0000"),
            ("rgb_zones/zone03", "00ff9a"),
            ("rgb_zones/all", "FFFFFF"),
            ("rgb_zones/brightness", "75"),
            ("rgb_zones/brightness", "0"),
            ("rgb_zones/brightness", "100"),
            ("rgb_zones/animation_mode", "rainbow"),
            ("rgb_zones/animation_speed", "10"),
            ("rgb_zones/gradient_config", "0,1,2:FF0000,00FF00;3:800080"),
            ("rgb_zones/brightness_lock", "1"),
            ("rgb_zones/mute_led", "0"),
            ("rgb_zones/mute_state", "1"),
            ("fan/max_fan", "1"),
            ("fan/thermal_profile", "performance"),
            ("fan/thermal_profile", "silent"),
            ("fan/fan_curve", "30:20 40:30 55:45 70:65 85:90"),
            ("fan/fan_curve", "40:30 90:100"),
            ("fan/fan_curve_enable", "1"),
            ("fan/fan_temp_zone", "x86_pkg_temp"),
        ]
        for key, value in cases:
            proc = run_ctl(self.tmp, "set", key, value)
            self.assertEqual(proc.returncode, 0, f"{key}={value}: {proc.stderr}")
            self.assertEqual(self.read_mock(key), value)

    def test_rejections(self):
        cases = [
            ("rgb_zones/zone00", "red"),            # not hex
            ("rgb_zones/zone00", "FF000"),          # 5 chars
            ("rgb_zones/zone00", "FF00000"),        # 7 chars
            ("rgb_zones/zone00", "GG0000"),         # bad hex
            ("rgb_zones/all", "FF0000; reboot"),    # injection attempt
            ("rgb_zones/brightness", "101"),
            ("rgb_zones/brightness", "-1"),
            ("rgb_zones/brightness", "50.5"),
            ("rgb_zones/animation_mode", "strobe"), # not a real mode
            ("rgb_zones/animation_mode", "rainbow; rm -rf /"),
            ("rgb_zones/animation_speed", "0"),
            ("rgb_zones/animation_speed", "11"),
            ("rgb_zones/gradient_config", "0,1,2,3,4:FF0000"),  # zone 4
            ("rgb_zones/gradient_config", "0:FF000"),           # bad color
            ("rgb_zones/brightness_lock", "2"),
            ("fan/max_fan", "yes"),
            ("fan/thermal_profile", "cool"),        # not a writable preset
            ("fan/power_limits", "55 157"),         # key removed: read-only
            ("fan/power_limits", "default"),        # until a model-safe range
            ("fan/fan_curve", "30:20"),             # single point
            ("fan/fan_curve", "30:20 40:30 50:40 60:50 70:60 80:70 90:80 100:90 110:100"),  # 9 points
            ("fan/fan_curve", "30:101 40:30"),      # percent > 100
            ("fan/fan_curve", "200:50 40:30"),      # temp > 120
            ("fan/fan_curve", "30:20; rm -rf /"),
            ("fan/fan_curve_enable", "2"),
            ("fan/fan_temp_zone", "../etc/passwd"), # traversal
            ("fan/fan_temp_zone", "zone; rm"),
            ("../etc/hostname", "x"),               # unknown key
            ("fan/../../etc/passwd", "x"),
        ]
        for key, value in cases:
            proc = run_ctl(self.tmp, "set", key, value)
            self.assertNotEqual(proc.returncode, 0, f"should reject {key}={value}")
        # nothing was written by rejected attempts
        for key in ALL_KEYS:
            self.assertEqual(self.read_mock(key), "", f"reject wrote {key}")

    def test_usage(self):
        self.assertEqual(run_ctl(self.tmp).returncode, 2)
        self.assertEqual(run_ctl(self.tmp, "set", "rgb_zones/all").returncode, 2)
        self.assertEqual(run_ctl(self.tmp, "get", "rgb_zones/all", "FFFFFF").returncode, 2)

    def test_missing_node(self):
        proc = run_ctl(self.tmp, "set", "fan/nonexistent", "1")
        self.assertNotEqual(proc.returncode, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)


class LightingOnlyTest(unittest.TestCase):
    def _run(self, marker, key, val):
        import tempfile
        root = tempfile.mkdtemp()
        for d in ("rgb_zones", "fan"):
            os.makedirs(os.path.join(root, d))
        for f in ("rgb_zones/zone00", "fan/thermal_profile"):
            open(os.path.join(root, f), "w").write("x\n")
        env = dict(os.environ, OMEN_SYSFS_ROOT=root,
                   OMEN_LIGHTING_ONLY_MARKER=marker)
        r = subprocess.run([sys.executable, OMENCTL, "set", key, val],
                           env=env, capture_output=True, text=True)
        return r.returncode, open(os.path.join(root, key)).read().strip()

    def test_marker_blocks_fan_allows_colour(self):
        import tempfile
        m = tempfile.mktemp()
        open(m, "w").close()
        rc, v = self._run(m, "fan/thermal_profile", "silent")
        self.assertEqual(rc, 2); self.assertEqual(v, "x")
        rc, v = self._run(m, "rgb_zones/zone00", "FF0000")
        self.assertEqual(rc, 0); self.assertEqual(v, "FF0000")

    def test_no_marker_allows_fan(self):
        rc, v = self._run("/nonexistent/marker", "fan/thermal_profile", "silent")
        self.assertEqual(rc, 0); self.assertEqual(v, "silent")
