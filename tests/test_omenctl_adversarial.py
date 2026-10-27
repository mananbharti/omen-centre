"""Adversarial tests for the privileged helper omenctl.

Run against a mock sysfs root. Every attack case must be REJECTED
(exit 2) and must leave the mock tree untouched; valid cases must pass.
"""
import os
import subprocess
import sys
import tempfile
import unittest

HELPER = os.path.join(os.path.dirname(__file__), "..", "helper", "omenctl")

KEYS = [
    "rgb_zones/zone00", "rgb_zones/zone01", "rgb_zones/zone02",
    "rgb_zones/zone03", "rgb_zones/all", "rgb_zones/brightness",
    "rgb_zones/animation_mode", "rgb_zones/animation_speed",
    "rgb_zones/gradient_config", "rgb_zones/brightness_lock",
    "rgb_zones/mute_led", "rgb_zones/mute_state",
    "fan/max_fan", "fan/thermal_profile",
    "fan/fan_curve", "fan/fan_curve_enable", "fan/fan_temp_zone",
]


def run_helper(root, args, env_extra=None):
    env = {"PATH": "/usr/bin:/bin", "OMEN_SYSFS_ROOT": root}
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, "-I", HELPER] + args,
        capture_output=True, text=True, env=env, timeout=10)


class Adversarial(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        for k in KEYS:
            p = os.path.join(self.root, k)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w") as fh:
                fh.write("ORIGINAL\n")
        self.sentinel = os.path.join(self.root, "..",
                                     "sentinel-%d" % os.getpid())
        with open(self.sentinel, "w") as fh:
            fh.write("UNTOUCHED\n")

    def tearDown(self):
        self.tmp.cleanup()
        if os.path.exists(self.sentinel):
            os.unlink(self.sentinel)

    def assert_tree_intact(self):
        for k in KEYS:
            p = os.path.join(self.root, k)
            if os.path.islink(p):
                continue  # replaced mid-test; that test restores it
            with open(p) as fh:
                self.assertEqual(fh.read(), "ORIGINAL\n",
                                 "attack modified %s" % k)
        with open(self.sentinel) as fh:
            self.assertEqual(fh.read(), "UNTOUCHED\n")

    def assert_rejected(self, args, env_extra=None):
        r = run_helper(self.root, args, env_extra)
        self.assertEqual(r.returncode, 2,
                         "expected rejection of %r, got rc=%d (%s%s)"
                         % (args, r.returncode, r.stdout, r.stderr))
        self.assert_tree_intact()

    # --- key-space attacks ---
    def test_unknown_key(self):
        self.assert_rejected(["set", "rgb_zones/zone99", "FF0000"])

    def test_path_traversal_key(self):
        self.assert_rejected(["set", "rgb_zones/../../fan/max_fan", "1"])
        self.assert_rejected(["set", "../sentinel", "x"])

    def test_absolute_path_key(self):
        self.assert_rejected(["set", "/etc/passwd", "x"])

    def test_key_case_sensitivity(self):
        self.assert_rejected(["set", "RGB_ZONES/ZONE00", "FF0000"])

    def test_extra_args(self):
        self.assert_rejected(["set", "fan/max_fan", "1", "extra"])

    def test_wrong_verb(self):
        self.assert_rejected(["get", "fan/max_fan", "1"])

    # --- value attacks ---
    def test_shell_metachars(self):
        self.assert_rejected(["set", "rgb_zones/zone00",
                              "FF0000; rm -rf /"])
        self.assert_rejected(["set", "rgb_zones/zone00", "$(id)"])
        self.assert_rejected(["set", "rgb_zones/zone00", "`id`"])

    def test_newline_injection(self):
        self.assert_rejected(["set", "rgb_zones/zone00", "FF0000\n1"])
        self.assert_rejected(["set", "fan/thermal_profile",
                              "normal\nmax_fan"])
        self.assert_rejected(["set", "rgb_zones/gradient_config",
                              "0:FF0000\n3:000000"])

    def test_numeric_overflow(self):
        self.assert_rejected(["set", "rgb_zones/brightness", "101"])
        self.assert_rejected(["set", "rgb_zones/brightness", "-1"])
        self.assert_rejected(["set", "rgb_zones/animation_speed",
                              "999999999999999999999"])
        self.assert_rejected(["set", "rgb_zones/brightness", "1e2"])

    def test_overlong_values(self):
        self.assert_rejected(["set", "rgb_zones/gradient_config",
                              "0:FF0000;" * 200])
        self.assert_rejected(["set", "fan/fan_temp_zone", "A" * 33])

    def test_curve_attacks(self):
        self.assert_rejected(["set", "fan/fan_curve", "30:101 40:50"])
        self.assert_rejected(["set", "fan/fan_curve", "-5:50 40:50"])
        self.assert_rejected(["set", "fan/fan_curve", "30:50"])
        self.assert_rejected(
            ["set", "fan/fan_curve",
             " ".join("%d:50" % t for t in range(9))])
        self.assert_rejected(["set", "fan/fan_curve", "30:50;40:60"])

    def test_power_limits_key_not_writable(self):
        # removed from the allowlist until a model-safe range exists;
        # even a well-formed value must be rejected as an unknown key
        self.assert_rejected(["set", "fan/power_limits", "55 157"])
        self.assert_rejected(["set", "fan/power_limits", "default"])

    def test_gradient_attacks(self):
        self.assert_rejected(["set", "rgb_zones/gradient_config",
                              "4:FF0000"])
        self.assert_rejected(["set", "rgb_zones/gradient_config",
                              "0:GGGGGG"])
        self.assert_rejected(["set", "rgb_zones/gradient_config",
                              "0:FF0000:"])
        self.assert_rejected(["set", "rgb_zones/gradient_config", ""])

    def test_case_and_whitespace_padding(self):
        self.assert_rejected(["set", "fan/thermal_profile", "Silent"])
        self.assert_rejected(["set", "fan/thermal_profile", " normal"])
        self.assert_rejected(["set", "fan/thermal_profile", "normal "])
        self.assert_rejected(["set", "fan/max_fan", "01"])

    # --- environment attacks ---
    def test_pythonpath_ignored(self):
        evil = os.path.join(self.root, "evil")
        os.makedirs(evil)
        with open(os.path.join(evil, "os.py"), "w") as fh:
            fh.write("raise SystemExit('PWNED')\n")
        r = run_helper(self.root,
                       ["set", "fan/max_fan", "1"],
                       env_extra={"PYTHONPATH": evil})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("PWNED", r.stdout + r.stderr)

    def test_pythonstartup_ignored(self):
        evil = os.path.join(self.root, "startup.py")
        with open(evil, "w") as fh:
            fh.write("import sys; sys.exit('PWNED')\n")
        r = run_helper(self.root,
                       ["set", "fan/max_fan", "1"],
                       env_extra={"PYTHONSTARTUP": evil})
        self.assertEqual(r.returncode, 0, r.stderr)

    # --- symlink + root-path defenses ---
    def test_symlinked_allowlisted_node_rejected(self):
        # replace an allowlisted node with a symlink escaping the root:
        # realpath containment must reject the write
        outside = os.path.join(self.root, "..",
                               "escape-%d" % os.getpid())
        with open(outside, "w") as fh:
            fh.write("UNTOUCHED\n")
        node = os.path.join(self.root, "fan", "max_fan")
        os.unlink(node)
        os.symlink(outside, node)
        try:
            r = run_helper(self.root, ["set", "fan/max_fan", "1"])
            self.assertEqual(r.returncode, 2,
                             "symlink escape was not rejected")
            with open(outside) as fh:
                self.assertEqual(fh.read(), "UNTOUCHED\n")
        finally:
            os.unlink(node)
            with open(node, "w") as fh:
                fh.write("ORIGINAL\n")
            os.unlink(outside)

    def test_env_override_ignored_when_root(self):
        # with geteuid patched to 0 the fixed /sys path must win over
        # OMEN_SYSFS_ROOT even without pkexec scrubbing the environment
        import importlib.util
        import importlib.machinery
        loader = importlib.machinery.SourceFileLoader("omenctl", HELPER)
        spec = importlib.util.spec_from_loader("omenctl", loader)
        mod = importlib.util.module_from_spec(spec)
        real_geteuid = os.geteuid
        os.environ["OMEN_SYSFS_ROOT"] = self.root
        os.geteuid = lambda: 0
        try:
            spec.loader.exec_module(mod)
        finally:
            os.geteuid = real_geteuid
        self.assertEqual(mod.SYSFS_ROOT, mod.DEFAULT_SYSFS_ROOT)

    # --- happy path ---
    def test_valid_writes(self):
        for key, val in (("rgb_zones/zone00", "FF0000"),
                         ("fan/thermal_profile", "performance"),
                         ("fan/max_fan", "1"),
                         ("fan/fan_curve", "30:20 50:45 70:65 90:100"),
                         ("rgb_zones/gradient_config",
                          "0,1:FF0000,00FF00;3:800080")):
            r = run_helper(self.root, ["set", key, val])
            self.assertEqual(r.returncode, 0, "%s: %s" % (key, r.stderr))
            with open(os.path.join(self.root, key)) as fh:
                self.assertEqual(fh.read(), val + "\n")
            with open(os.path.join(self.root, key), "w") as fh:
                fh.write("ORIGINAL\n")


if __name__ == "__main__":
    unittest.main()
