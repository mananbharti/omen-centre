"""Backend for Omen Control Centre.

Reads the omen-rgb-keyboard driver sysfs directly (world-readable).
Writes go through the validated root helper `omenctl` via pkexec, which
asks for admin authentication per polkit policy (auth_admin_keep).

Dev/test mode: set OMEN_SYSFS_ROOT to a mock tree and OMEN_DIRECT_WRITE=1
to write without pkexec.
"""
import os
import re
import subprocess

SYSFS_ROOT = os.environ.get(
    "OMEN_SYSFS_ROOT", "/sys/devices/platform/omen-rgb-keyboard")
OMENCTL = os.environ.get(
    "OMENCTL", "/usr/libexec/omen-centre/omenctl")
DIRECT_WRITE = os.environ.get("OMEN_DIRECT_WRITE") == "1"

ANIMATION_MODES = [
    "static", "breathing", "rainbow", "wave", "pulse", "chase",
    "sparkle", "candle", "aurora", "disco", "gradient", "spectrum",
]
THERMAL_PROFILES = ["silent", "normal", "performance"]


class BackendError(Exception):
    pass


def _path(rel):
    return os.path.join(SYSFS_ROOT, rel)


def driver_present():
    return os.path.isdir(SYSFS_ROOT)


def read(rel):
    """Read a sysfs attribute; None if missing/unreadable."""
    try:
        with open(_path(rel)) as fh:
            return fh.read().strip()
    except OSError:
        return None


def read_hex(rel):
    """Colour attribute as 'RRGGBB' uppercase, or None.

    The driver reports colours as '#rrggbb' (seen on a real Omen 16);
    writes take plain 'RRGGBB'.
    """
    v = read(rel)
    if v is None:
        return None
    v = v.strip().lstrip("#").upper()
    return v if re.fullmatch(r"[0-9A-F]{6}", v) else None


def read_int(rel):
    v = read(rel)
    if v is None:
        return None
    try:
        return int(v)
    except ValueError:
        return None


def fan_restricted():
    """True while the root-owned lighting-only marker exists."""
    return os.path.exists(os.environ.get(
        "OMEN_LIGHTING_ONLY_MARKER", "/etc/omen-centre/lighting-only"))


FAN_RESTRICTED_MSG = ("Fan and power controls are unavailable: this install "
                      "is lighting-only (fan writes are refused).")


def set_value(rel, value):
    """Write a validated value via the privileged helper.

    Raises BackendError with a human-readable reason on failure.
    """
    if os.environ.get("OMEN_READ_ONLY") == "1":
        raise BackendError("Read-only mode (OMEN_READ_ONLY=1): "
                           "nothing was written.")
    if rel.startswith("fan/") and fan_restricted():
        raise BackendError(FAN_RESTRICTED_MSG)
    if DIRECT_WRITE:
        cmd = ["python3", OMENCTL, "set", rel, str(value)]
        env = dict(os.environ, OMEN_SYSFS_ROOT=SYSFS_ROOT)
        proc = subprocess.run(cmd, capture_output=True, text=True, env=env)
    else:
        proc = subprocess.run(
            ["pkexec", OMENCTL, "set", rel, str(value)],
            capture_output=True, text=True)
    if proc.returncode == 0:
        return
    if proc.returncode == 126 or proc.returncode == 127:
        raise BackendError("Authentication dismissed or pkexec unavailable.")
    reason = (proc.stderr or proc.stdout).strip() or "write failed"
    raise BackendError(reason)


def fan_curve_supported():
    """Probe once: CLASSIC fan interfaces reject fan_curve reads."""
    try:
        with open(_path("fan/fan_curve")) as fh:
            fh.read()
        return True
    except OSError:
        return False


def read_rpm():
    return read_int("fan/cpu_fan_rpm"), read_int("fan/gpu_fan_rpm")


def read_cpu_temp_mc():
    """CPU package temp in millidegrees from the x86_pkg_temp zone."""
    base = "/sys/class/thermal"
    try:
        zones = os.listdir(base)
    except OSError:
        return None
    for z in sorted(zones):
        try:
            with open(os.path.join(base, z, "type")) as fh:
                if fh.read().strip() != "x86_pkg_temp":
                    continue
            with open(os.path.join(base, z, "temp")) as fh:
                return int(fh.read().strip())
        except (OSError, ValueError):
            continue
    return None


def read_gpu_temp_mc():
    """Discrete GPU temp from nouveau/nvidia/amdgpu hwmon, if exposed."""
    base = "/sys/class/hwmon"
    wanted = {"nouveau", "nvidia", "amdgpu"}
    try:
        hws = os.listdir(base)
    except OSError:
        return None
    for hw in sorted(hws):
        try:
            with open(os.path.join(base, hw, "name")) as fh:
                if fh.read().strip() not in wanted:
                    continue
            with open(os.path.join(base, hw, "temp1_input")) as fh:
                return int(fh.read().strip())
        except (OSError, ValueError):
            continue
    return _read_nvidia_smi_mc()


_NV_CACHE = [0.0, None]
PCI_ROOT = os.environ.get("OMEN_PCI_ROOT", "/sys/bus/pci/devices")
NV_PROC = os.environ.get("OMEN_NV_PROC", "/proc/driver/nvidia/version")
GPU_REASON = ""


def _nvidia_pci_state():
    """Runtime power state of the first NVIDIA display device, no wake.

    Reads the kernel's PCI runtime_status ("active" / "suspended" / ...)
    which is a plain sysfs attribute and does not power the device up.
    Returns None when no NVIDIA display device is present.
    """
    try:
        devs = sorted(os.listdir(PCI_ROOT))
    except OSError:
        return None
    for d in devs:
        base = os.path.join(PCI_ROOT, d)
        try:
            with open(os.path.join(base, "vendor")) as fh:
                if fh.read().strip().lower() != "0x10de":
                    continue
            with open(os.path.join(base, "class")) as fh:
                if not fh.read().strip().startswith("0x03"):
                    continue
        except OSError:
            continue
        try:
            with open(os.path.join(base, "power", "runtime_status")) as fh:
                return fh.read().strip()
        except OSError:
            return "unknown"
    return None


def _read_nvidia_smi_mc():
    """GPU temp via nvidia-smi, only when it cannot wake the GPU.

    The proprietary NVIDIA driver exposes no hwmon temperature. nvidia-smi
    is queried only when the PCI device is runtime-"active" (already
    awake) and the proprietary module is loaded; cached for 10 s. Sets
    GPU_REASON whenever it returns None. Opt-in only (OMEN_GPU_SMI=1); the active-state
    check narrows but cannot rule out a wake race.
    """
    import shutil
    import time
    global GPU_REASON
    if os.environ.get("OMEN_GPU_SMI") != "1":
        GPU_REASON = ("GPU temperature polling is off by default (the "
                      "proprietary driver has no sensor; nvidia-smi could "
                      "wake the GPU). Start with OMEN_GPU_SMI=1 to opt in")
        return None
    state = _nvidia_pci_state()
    if state is None:
        GPU_REASON = "No NVIDIA GPU found on the PCI bus"
        return None
    if state != "active":
        GPU_REASON = ("GPU is %s (runtime power state); not waking it "
                      "to read the temperature" % state)
        return None
    if not os.path.exists(NV_PROC):
        GPU_REASON = ("GPU is awake but exposes no temperature sensor "
                      "through the loaded driver (proprietary NVIDIA "
                      "driver not loaded)")
        return None
    exe = shutil.which("nvidia-smi")
    if not exe:
        GPU_REASON = "nvidia-smi not found"
        return None
    now = time.monotonic()
    if now - _NV_CACHE[0] < 10.0:
        return _NV_CACHE[1]
    _NV_CACHE[0] = now
    try:
        out = subprocess.run(
            [exe, "--query-gpu=temperature.gpu",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=3).stdout.strip()
        _NV_CACHE[1] = int(out.splitlines()[0]) * 1000
        GPU_REASON = ""
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        _NV_CACHE[1] = None
        GPU_REASON = "nvidia-smi did not return a temperature"
    return _NV_CACHE[1]


class _LoadSampler:
    def __init__(self):
        self._prev = None

    def sample(self):
        try:
            with open("/proc/stat") as fh:
                parts = fh.readline().split()
            vals = list(map(int, parts[1:]))
            idle = vals[3] + (vals[4] if len(vals) > 4 else 0)
            total = sum(vals)
        except (OSError, ValueError, IndexError):
            return None
        prev, self._prev = self._prev, (total, idle)
        if prev is None:
            return None
        dt, di = total - prev[0], idle - prev[1]
        if dt <= 0:
            return None
        return max(0.0, min(100.0, 100.0 * (dt - di) / dt))


_load_sampler = _LoadSampler()


def read_cpu_load_pct():
    return _load_sampler.sample()


def read_mem():
    try:
        info = {}
        with open("/proc/meminfo") as fh:
            for line in fh:
                k, _, rest = line.partition(":")
                info[k] = int(rest.strip().split()[0])
        total = info["MemTotal"]
        avail = info.get("MemAvailable", 0)
        return total, total - avail
    except (OSError, ValueError, KeyError):
        return None, None


def read_storage():
    """Used fraction of the root filesystem, 0-100."""
    try:
        st = os.statvfs("/")
        total = st.f_blocks * st.f_frsize
        avail = st.f_bavail * st.f_frsize
        if total <= 0:
            return None
        return 100.0 * (total - avail) / total
    except OSError:
        return None


class _NetSampler:
    def __init__(self):
        self._prev = None

    def sample(self):
        try:
            rx = tx = 0
            with open("/proc/net/dev") as fh:
                for line in fh:
                    if ":" not in line:
                        continue
                    iface, rest = line.split(":", 1)
                    if iface.strip() == "lo":
                        continue
                    fields = rest.split()
                    rx += int(fields[0])
                    tx += int(fields[8])
            import time
            now = time.monotonic()
        except (OSError, ValueError, IndexError):
            return None
        prev, self._prev = self._prev, (now, rx, tx)
        if prev is None:
            return None
        dt = now - prev[0]
        if dt <= 0:
            return None
        return ((rx - prev[1]) / 1024.0 / dt, (tx - prev[2]) / 1024.0 / dt)


_net_sampler = _NetSampler()


def read_net_kbps():
    """(download, upload) KB/s since the previous sample."""
    return _net_sampler.sample()
