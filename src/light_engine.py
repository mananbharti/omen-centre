"""Software lighting compositor.

Light Studio-style layers resolved onto the laptop's 4 hardware zones.
The driver can show ONE hardware animation at a time; this engine stacks
any number of software layers (each with its own effect, colors, speed and
zone mask), composites them per zone, and emits 4 zone colors per tick.
It writes ONLY through the existing validated sysfs backend (zone00-03) -
no EC access, no driver changes.
"""

import colorsys
import math
import time

ZONE_COUNT = 4

LAYER_TYPES = (
    "static", "breathing", "wave", "pulse", "rainbow", "gradient",
    "sparkle", "candle",
)


def _hx(rgb):
    return "%02X%02X%02X" % (rgb[0], rgb[1], rgb[2])


def _parse(hexs):
    hexs = (hexs or "FFFFFF").lstrip("#").upper()
    if len(hexs) != 6:
        hexs = "FFFFFF"
    return (int(hexs[0:2], 16), int(hexs[2:4], 16), int(hexs[4:6], 16))


def _hsv(h, s, v):
    r, g, b = colorsys.hsv_to_rgb(h % 1.0, s, v)
    return (int(r * 255), int(g * 255), int(b * 255))


def _scale(rgb, k):
    k = max(0.0, min(1.0, k))
    return (int(rgb[0] * k), int(rgb[1] * k), int(rgb[2] * k))


class Layer:
    """One software layer: an effect with colors, speed and a zone mask."""

    def __init__(self, name, ltype="wave", primary="FF8800",
                 secondary="0066FF", speed=50, zones=(0, 1, 2, 3),
                 enabled=True):
        if ltype not in LAYER_TYPES:
            raise ValueError("unknown layer type: %s" % ltype)
        if not zones or any(z < 0 or z >= ZONE_COUNT for z in zones):
            raise ValueError("zone mask must be non-empty within 0..3")
        self.name = name
        self.ltype = ltype
        self.primary = primary
        self.secondary = secondary
        self.speed = max(0, min(100, int(speed)))
        self.zones = tuple(sorted(set(zones)))
        self.enabled = enabled

    def covers(self, z):
        return z in self.zones

    def color_at(self, z, t):
        """Color this layer contributes to zone z at time t (seconds)."""
        p = _parse(self.primary)
        s = _parse(self.secondary)
        v = self.speed / 100.0
        # speed 0-100 -> 0.05..2.0 cycles/sec
        f = 0.05 + 1.95 * v
        ph = t * f
        if self.ltype == "static":
            return p
        if self.ltype == "breathing":
            k = 0.5 - 0.5 * math.cos(2 * math.pi * ph)
            return _scale(p, 0.15 + 0.85 * k)
        if self.ltype == "wave":
            zi = self.zones.index(z) if z in self.zones else 0
            k = 0.5 - 0.5 * math.cos(
                2 * math.pi * (ph + zi / max(1, len(self.zones))))
            base = _scale(s, 0.25) if self.secondary else (0, 0, 0)
            lit = _scale(p, 0.15 + 0.85 * k)
            return tuple(min(255, a + b) for a, b in zip(base, lit))
        if self.ltype == "pulse":
            k = 1.0 if (ph % 1.0) < 0.5 else 0.15
            return _scale(p, k)
        if self.ltype == "rainbow":
            zi = self.zones.index(z) if z in self.zones else 0
            return _hsv(ph + zi * 0.25, 1.0, 1.0)
        if self.ltype == "gradient":
            n = len(self.zones)
            zi = self.zones.index(z) if z in self.zones else 0
            m = zi / (n - 1) if n > 1 else 0.0
            return tuple(int(a + (b - a) * m) for a, b in zip(p, s))
        if self.ltype == "sparkle":
            h = math.sin((z + 1) * 99991 + int(ph * 8) * 7919)
            return p if h > 0.4 else _scale(p, 0.08)
        if self.ltype == "candle":
            h = math.sin(t * 13.7 + z * 3.1) * math.sin(t * 7.3 + z)
            return _scale(p, 0.65 + 0.30 * h)
        return p


class Compositor:
    """Ordered layer stack over a base color; topmost enabled layer that
    covers a zone wins for that zone."""

    def __init__(self, base_colors=None):
        self.base_colors = list(base_colors or ["FFFFFF"] * ZONE_COUNT)
        self.layers = []
        self._t0 = time.monotonic()

    def set_base(self, i, hexs):
        self.base_colors[i] = hexs

    def add_layer(self, layer):
        self.layers.append(layer)
        return layer

    def remove_layer(self, layer):
        self.layers.remove(layer)

    def tick(self, t=None):
        """Return the 4 composited zone colors as hex strings."""
        t = (time.monotonic() - self._t0) if t is None else t
        out = []
        for z in range(ZONE_COUNT):
            col = _parse(self.base_colors[z])
            for layer in self.layers:
                if layer.enabled and layer.covers(z):
                    col = layer.color_at(z, t)
            out.append(_hx(col))
        return out

    def animated(self):
        """True when any enabled layer needs ticking."""
        return any(l.enabled and l.ltype != "static" for l in self.layers)
