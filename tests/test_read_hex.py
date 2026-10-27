import importlib, os, sys, tempfile, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


class ReadHexTest(unittest.TestCase):
    def test_driver_format(self):
        d = tempfile.mkdtemp()
        os.environ["OMEN_SYSFS_ROOT"] = d
        import sysfs_backend
        be = importlib.reload(sysfs_backend)
        os.makedirs(os.path.join(d, "rgb_zones"))
        for name, val, want in (("zone00", "#ff0000\n", "FF0000"),
                                ("zone01", "FFFFFF\n", "FFFFFF"),
                                ("mute_led", "Write '1' to turn on\n", None),
                                ("zone02", "#fff\n", None)):
            open(os.path.join(d, "rgb_zones", name), "w").write(val)
            self.assertEqual(be.read_hex("rgb_zones/" + name), want)
        self.assertIsNone(be.read_hex("rgb_zones/missing"))


if __name__ == "__main__":
    unittest.main()
