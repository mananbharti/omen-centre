"""Backend for Omen Control Centre.

Reads the omen-rgb-keyboard driver sysfs directly (world-readable).
Writes go through the validated root helper `omenctl` via pkexec, which
asks for admin authentication per polkit policy (auth_admin_keep).

Dev/test mode: set OMEN_SYSFS_ROOT to a mock tree and OMEN_DIRECT_WRITE=1
to write without pkexec.
"""
import os
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


def read_int(rel):
    v = read(rel)
    if v is None:
        return None
    try:
        return int(v)
    except ValueError:
        return None


def set_value(rel, value):
    """Write a validated value via the privileged helper.

    Raises BackendError with a human-readable reason on failure.
    """
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
    return None


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
