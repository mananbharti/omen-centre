import os, re, subprocess, tempfile, unittest

ROOT = os.path.join(os.path.dirname(__file__), "..")


class InstallTest(unittest.TestCase):
    def test_staged_install_has_every_imported_module(self):
        dest = tempfile.mkdtemp()
        subprocess.run(["sh", "install.sh", "--lighting-only"], cwd=ROOT,
                       env=dict(os.environ, DESTDIR=dest), check=True,
                       capture_output=True)
        inst = os.path.join(dest, "usr/share/omen-centre")
        src = os.path.join(ROOT, "src")
        local = {f[:-3] for f in os.listdir(src) if f.endswith(".py")}
        for f in os.listdir(src):
            if not f.endswith(".py"):
                continue
            text = open(os.path.join(src, f)).read()
            for m in re.findall(r"^(?:import|from) (\w+)", text, re.M):
                if m in local:
                    self.assertTrue(os.path.exists(
                        os.path.join(inst, m + ".py")), m)
        self.assertTrue(os.path.exists(
            os.path.join(dest, "etc/omen-centre/lighting-only")))
        # plain re-run keeps the marker
        subprocess.run(["sh", "install.sh"], cwd=ROOT,
                       env=dict(os.environ, DESTDIR=dest), check=True,
                       capture_output=True)
        self.assertTrue(os.path.exists(
            os.path.join(dest, "etc/omen-centre/lighting-only")))


if __name__ == "__main__":
    unittest.main()
