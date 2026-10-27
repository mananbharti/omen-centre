"""GPU temperature fallback: never wakes a suspended GPU, always says why."""
import importlib
import os
import stat
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


class GpuTempTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="gpu-")
        self.pci = os.path.join(self.tmp, "pci")
        self.proc = os.path.join(self.tmp, "nvversion")
        self.bin = os.path.join(self.tmp, "bin")
        os.makedirs(self.pci)
        os.makedirs(self.bin)
        self.marker = os.path.join(self.tmp, "smi-called")
        os.environ["OMEN_PCI_ROOT"] = self.pci
        os.environ["OMEN_NV_PROC"] = self.proc
        os.environ["PATH"] = self.bin + os.pathsep + os.environ["PATH"]
        os.environ.pop("OMEN_GPU_SMI", None)
        import sysfs_backend
        self.be = importlib.reload(sysfs_backend)

    def _gpu(self, status, vendor="0x10de", cls="0x030000"):
        d = os.path.join(self.pci, "0000:01:00.0")
        os.makedirs(os.path.join(d, "power"), exist_ok=True)
        for name, val in (("vendor", vendor), ("class", cls)):
            open(os.path.join(d, name), "w").write(val + "\n")
        open(os.path.join(d, "power", "runtime_status"), "w").write(
            status + "\n")

    def _smi(self, out="61"):
        p = os.path.join(self.bin, "nvidia-smi")
        open(p, "w").write("#!/bin/sh\ntouch '%s'\necho %s\n"
                           % (self.marker, out))
        os.chmod(p, os.stat(p).st_mode | stat.S_IXUSR)

    def test_no_nvidia_device(self):
        self.assertIsNone(self.be._read_nvidia_smi_mc())
        self.assertIn("No NVIDIA GPU", self.be.GPU_REASON)

    def test_suspended_gpu_is_not_woken(self):
        self._gpu("suspended")
        open(self.proc, "w").write("x")
        self._smi()
        self.assertIsNone(self.be._read_nvidia_smi_mc())
        self.assertFalse(os.path.exists(self.marker))
        self.assertIn("suspended", self.be.GPU_REASON)

    def test_active_without_proprietary_driver(self):
        self._gpu("active")
        self._smi()
        self.assertIsNone(self.be._read_nvidia_smi_mc())
        self.assertFalse(os.path.exists(self.marker))
        self.assertIn("not loaded", self.be.GPU_REASON)

    def test_active_with_driver_reads_temp(self):
        self._gpu("active")
        open(self.proc, "w").write("x")
        self._smi("61")
        self.assertEqual(self.be._read_nvidia_smi_mc(), 61000)
        self.assertTrue(os.path.exists(self.marker))

    def test_garbage_output_is_honest(self):
        self._gpu("active")
        open(self.proc, "w").write("x")
        self._smi("N/A")
        self.assertIsNone(self.be._read_nvidia_smi_mc())
        self.assertIn("did not return", self.be.GPU_REASON)

    def test_kill_switch(self):
        self._gpu("active")
        open(self.proc, "w").write("x")
        self._smi()
        os.environ["OMEN_GPU_SMI"] = "0"
        self.assertIsNone(self.be._read_nvidia_smi_mc())
        self.assertFalse(os.path.exists(self.marker))


if __name__ == "__main__":
    unittest.main()
