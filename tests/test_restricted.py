import importlib, os, sys, tempfile, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


class RestrictedTest(unittest.TestCase):
    def setUp(self):
        import sysfs_backend
        self.be = importlib.reload(sysfs_backend)
        os.environ.pop("OMEN_READ_ONLY", None)

    def tearDown(self):
        os.environ.pop("OMEN_LIGHTING_ONLY_MARKER", None)

    def test_fan_refused_client_side(self):
        m = tempfile.mktemp(); open(m, "w").close()
        os.environ["OMEN_LIGHTING_ONLY_MARKER"] = m
        self.assertTrue(self.be.fan_restricted())
        with self.assertRaises(self.be.BackendError):
            self.be.set_value("fan/max_fan", "1")

    def test_unrestricted_without_marker(self):
        os.environ["OMEN_LIGHTING_ONLY_MARKER"] = "/nonexistent/x"
        self.assertFalse(self.be.fan_restricted())


if __name__ == "__main__":
    unittest.main()
