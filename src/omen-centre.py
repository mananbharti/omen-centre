#!/usr/bin/env python3
"""Omen Control Centre - Linux control app mirroring Windows OMEN Gaming Hub.

Reference: HP's official OMEN Gaming Hub documentation (support.hp.com,
doc ish_3912817-3737596-16) for notebook Performance Control (Power mode:
ECO/Balanced/Performance; Thermal Control: AUTO/MAX/MANUAL; real-time
CPU/GPU temp + CPU utilization dashboard), Lighting (Static/Animation
modes, per-zone colors, brightness), System Vitals, My Games, and Key
assignments. Hardware control goes through the OmenLinux
omen-rgb-keyboard kernel driver.

GUI runs unprivileged; writes are validated and applied by the root
helper `omenctl` through pkexec (admin authentication per polkit policy).
"""
import os
import subprocess
import time
import shutil
import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import light_engine as le
import sysfs_backend as be

try:
    import games_backend
except ImportError:
    games_backend = None
try:
    import omenkey_backend
except ImportError:
    omenkey_backend = None

APP_ID = "com.mananbharti.OmenCentre"
CONFIG_PATH = os.environ.get(
    "OMEN_CONFIG",
    os.path.expanduser("~/.config/omen-centre.ini"))

NAV = [
    ("home", "Home", "go-home-symbolic"),
    ("lighting", "Lighting", "input-keyboard-symbolic"),
    ("performance", "Performance", "power-profile-performance-symbolic"),
    ("vitals", "System Vitals", "applications-system-symbolic"),
    ("games", "My Games", "applications-games-symbolic"),
    ("settings", "Settings", "emblem-system-symbolic"),
]

EFFECTS = [
    ("static", "Static"), ("breathing", "Breathing"), ("rainbow", "Rainbow"),
    ("wave", "Wave"), ("pulse", "Pulse"), ("chase", "Chase"),
    ("sparkle", "Sparkle"), ("candle", "Candle"), ("aurora", "Aurora"),
    ("disco", "Disco"), ("gradient", "Gradient"), ("spectrum", "Spectrum"),
]

# Driver effect -> closest software-preview layer type. Chase, Aurora,
# Disco and Spectrum are approximated by the nearest supported layer;
# the driver animates in software (kernel timer + workqueue) regardless.
EFFECT_PREVIEW = {
    "static": "static", "breathing": "breathing", "rainbow": "rainbow",
    "wave": "wave", "pulse": "pulse", "chase": "wave",
    "sparkle": "sparkle", "candle": "candle", "aurora": "rainbow",
    "disco": "sparkle", "gradient": "gradient", "spectrum": "rainbow",
}

PRESET_COLORS = [
    "FF0000", "FF8000", "FFFF00", "00FF00", "00FFFF", "0000FF",
    "8000FF", "FF00FF", "FF0080", "FFFFFF", "AAAAAA", "555555",
]

RAIL_CSS = b"""
.omen-rail { background-color: #050507; }
.omen-rail-button { border-radius: 8px; margin: 2px 8px; padding: 8px 4px;
  color: #b9b9c6; }
.omen-rail-button:hover { background-color: #23232e; }
.omen-rail-button:checked { background-color: #343444; color: #ffffff; }
.omen-rail-title { color: #d92642; font-weight: 800; font-size: 20px;
  margin: 14px 0 14px 0; }
.omen-rail-sub { color: #6d6d7d; font-size: 11px; margin-bottom: 10px; }
.vital-card { background-color: alpha(@card_bg_color, 0.55);
  border-radius: 12px; padding: 12px; }
.vital-value { font-size: 24px; font-weight: 700; }
.vital-label { font-size: 12px; opacity: 0.65; }
.zone-box { border-radius: 8px; border: 2px solid alpha(black, 0.3);
  min-width: 64px; min-height: 44px; }
.mode-card { border-radius: 10px; border: 2px solid transparent;
  background-color: alpha(@card_bg_color, 0.55); }
.mode-card:checked { border-color: #d92642;
  background-color: alpha(@card_bg_color, 0.8); }
.swatch { border-radius: 6px; min-width: 28px; min-height: 28px;
  padding: 0; }
.readonly-banner { background-color: #5a4a12; color: #ffe9a8;
  padding: 6px 14px; font-weight: 600; }
.apply-footer { background-color: #1c1c26; padding: 8px 16px;
  border-top: 1px solid #2c2c3a; }
.omen-tab { background: none; border: none; border-radius: 0;
  padding: 4px 14px 6px 14px; color: #b9b9c6; font-weight: 600;
  box-shadow: none; min-height: 0; }
.omen-tab:hover { color: #ffffff; }
.omen-tab:checked { color: #ffffff;
  border-bottom: 2px solid #d92642; }
.omen-studio list.boxed-list { background-color: #0e0e11;
  border-radius: 0; border: none; box-shadow: none; }
.omen-studio row, .omen-studio row:hover { background-color: #0e0e11; }
.omen-studio list.boxed-list list.boxed-list { background-color: #1a1a1f; }
.omen-studio list.boxed-list list.boxed-list row { background-color: #1a1a1f; }
.canvas-controls { background-color: alpha(#101016, 0.92);
  border-radius: 8px; padding: 4px 6px; margin-bottom: 10px; }
.pcolor-swatch { border-radius: 6px; min-width: 44px; min-height: 30px;
  padding: 0; border: 1px solid alpha(white, 0.15); }
"""


def load_pref(key, default=None):
    try:
        with open(CONFIG_PATH) as fh:
            for line in fh:
                if line.startswith(key + "="):
                    return line.strip().split("=", 1)[1]
    except OSError:
        pass
    return default


def save_pref(key, value):
    lines = []
    try:
        with open(CONFIG_PATH) as fh:
            lines = [l for l in fh if not l.startswith(key + "=")]
    except OSError:
        pass
    try:
        os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
        with open(CONFIG_PATH, "w") as fh:
            fh.writelines(lines)
            fh.write("%s=%s\n" % (key, value))
    except OSError:
        pass


def load_theme_pref():
    return load_pref("theme", "dark")


def save_theme_pref(value):
    save_pref("theme", value)



ART_URL = "https://cdn.cloudflare.steamstatic.com/steam/apps/%s/header.jpg"
ART_CACHE = os.environ.get(
    "OMEN_ART_CACHE",
    os.path.expanduser("~/.cache/omen-centre/art"))


def fetch_art(appid, done):
    """Download (or read cache) the capsule art, then call done(path)."""
    import threading
    import urllib.request

    def work():
        os.makedirs(ART_CACHE, exist_ok=True)
        path = os.path.join(ART_CACHE, "%s.jpg" % appid)
        if not os.path.exists(path):
            try:
                with urllib.request.urlopen(ART_URL % appid,
                                            timeout=15) as resp:
                    data = resp.read(2 * 1024 * 1024)
                with open(path, "wb") as fh:
                    fh.write(data)
            except Exception:
                GLib.idle_add(done, None)
                return
        GLib.idle_add(done, path)

    threading.Thread(target=work, daemon=True).start()


class Gauge(Gtk.DrawingArea):
    """OGH-style circular utilization gauge."""

    def __init__(self):
        super().__init__()
        self._fraction = 0.0
        self._text = "-"
        self.set_content_width(120)
        self.set_content_height(120)
        self.set_draw_func(self._draw)

    def set_value(self, fraction, text):
        self._fraction = max(0.0, min(1.0, fraction))
        self._text = text
        self.queue_draw()

    def _draw(self, area, cr, w, h):
        import math
        cx, cy = w / 2.0, h / 2.0
        r = min(w, h) / 2.0 - 8.0
        # track
        cr.set_source_rgba(0.5, 0.5, 0.55, 0.25)
        cr.set_line_width(7.0)
        cr.arc(cx, cy, r, 0, 2 * math.pi)
        cr.stroke()
        # value arc (from top, clockwise)
        if self._fraction > 0:
            cr.set_source_rgba(0.66, 0.44, 0.84, 0.9)
            cr.arc(cx, cy, r, -math.pi / 2,
                   -math.pi / 2 + 2 * math.pi * self._fraction)
            cr.stroke()
        # center text
        cr.set_source_rgba(0.93, 0.93, 0.94, 1.0)
        cr.select_font_face("Sans")
        cr.set_font_size(20)
        (xb, yb, tw, th) = cr.text_extents(self._text)[:4]
        cr.move_to(cx - tw / 2.0 - xb, cy + th / 2.0)
        cr.show_text(self._text)


def gauge_card(title):
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
    box.add_css_class("card")
    box.set_margin_top(4)
    box.set_margin_bottom(4)
    box.set_margin_start(4)
    box.set_margin_end(4)
    t = Gtk.Label(label=title)
    t.add_css_class("caption")
    g = Gauge()
    sub = Gtk.Label(label="-")
    sub.add_css_class("caption")
    box.append(t)
    box.append(g)
    box.append(sub)
    return box, g, sub


class CurveGraph(Gtk.DrawingArea):
    """OGH-style fan curve editor: X = temp C, Y = fan %, draggable points."""

    T_MIN, T_MAX = 20, 110
    PAD = 34

    def __init__(self, entry):
        super().__init__()
        self.entry = entry
        self.points = []          # list of [temp, pct]
        self._drag = None
        self.set_content_width(420)
        self.set_content_height(220)
        self.set_draw_func(self._draw)
        drag = Gtk.GestureDrag()
        drag.connect("drag-begin", self._on_begin)
        drag.connect("drag-update", self._on_update)
        drag.connect("drag-end", self._on_end)
        self.add_controller(drag)
        self.load_from_entry()

    def load_from_entry(self):
        pts = []
        for tok in self.entry.get_text().split():
            if ":" in tok:
                t, p = tok.split(":", 1)
                try:
                    pts.append([int(t), int(p)])
                except ValueError:
                    pass
        pts.sort()
        self.points = pts[:8]
        self.queue_draw()

    def _xy(self, t, p, w, h):
        x = self.PAD + (t - self.T_MIN) / (self.T_MAX - self.T_MIN) \
            * (w - 2 * self.PAD)
        y = h - self.PAD - p / 100.0 * (h - 2 * self.PAD)
        return x, y

    def _tp(self, x, y, w, h):
        t = self.T_MIN + (x - self.PAD) / (w - 2 * self.PAD) \
            * (self.T_MAX - self.T_MIN)
        p = (h - self.PAD - y) / (h - 2 * self.PAD) * 100.0
        return int(round(t)), int(round(max(0.0, min(100.0, p))))

    def _draw(self, area, cr, w, h):
        import math  # noqa: F401
        # axes
        cr.set_source_rgba(0.6, 0.6, 0.65, 0.6)
        cr.set_line_width(1.0)
        cr.move_to(self.PAD, self.PAD - 10)
        cr.line_to(self.PAD, h - self.PAD)
        cr.line_to(w - self.PAD + 10, h - self.PAD)
        cr.stroke()
        cr.select_font_face("Sans")
        cr.set_font_size(10)
        cr.set_source_rgba(0.7, 0.7, 0.75, 0.8)
        for t in range(self.T_MIN + 10, self.T_MAX, 20):
            x, _ = self._xy(t, 0, w, h)
            cr.move_to(x - 8, h - self.PAD + 14)
            cr.show_text("%d°" % t)
        for p in (0, 25, 50, 75, 100):
            _, y = self._xy(self.T_MIN, p, w, h)
            cr.move_to(4, y + 3)
            cr.show_text("%d%%" % p)
        if len(self.points) >= 2:
            # curve line
            cr.set_source_rgba(0.66, 0.44, 0.84, 0.9)
            cr.set_line_width(2.0)
            for i, (t, p) in enumerate(self.points):
                x, y = self._xy(t, p, w, h)
                cr.line_to(x, y) if i else cr.move_to(x, y)
            cr.stroke()
        # handles
        for t, p in self.points:
            x, y = self._xy(t, p, w, h)
            cr.set_source_rgba(0.85, 0.25, 0.35, 1.0)
            cr.arc(x, y, 6, 0, 6.2832)
            cr.fill()

    def _nearest(self, x, y, w, h):
        best, bd = None, 18.0
        for i, (t, p) in enumerate(self.points):
            px, py = self._xy(t, p, w, h)
            d = ((px - x) ** 2 + (py - y) ** 2) ** 0.5
            if d < bd:
                best, bd = i, d
        return best

    def _on_begin(self, g, x, y):
        w, h = self.get_width(), self.get_height()
        self._drag = self._nearest(x, y, w, h)
        self._sx, self._sy = x, y

    def _on_update(self, g, dx, dy):
        if self._drag is None:
            return
        w, h = self.get_width(), self.get_height()
        t, p = self._tp(self._sx + dx, self._sy + dy, w, h)
        i = self._drag
        lo = self.points[i - 1][0] + 1 if i > 0 else self.T_MIN
        hi = self.points[i + 1][0] - 1 if i < len(self.points) - 1 \
            else self.T_MAX
        self.points[i][0] = max(lo, min(hi, t))
        self.points[i][1] = p
        self.entry.set_text(" ".join("%d:%d" % (t2, p2)
                                     for t2, p2 in self.points))
        self.queue_draw()

    def _on_end(self, g, dx, dy):
        self._drag = None

    def _sync_entry(self):
        self.entry.set_text(" ".join("%d:%d" % (t, p)
                                     for t, p in self.points))

    def add_point(self):
        if len(self.points) >= 8:
            return False
        # insert at the widest temperature gap
        best, gap = 0, -1
        for i in range(len(self.points) - 1):
            g = self.points[i + 1][0] - self.points[i][0]
            if g > gap:
                best, gap = i, g
        if not self.points:
            self.points = [[30, 30]]
        elif len(self.points) == 1:
            t = min(self.T_MAX, self.points[0][0] + 20)
            self.points.append([t, min(100, self.points[0][1] + 20)])
        elif gap >= 2:
            t = (self.points[best][0] + self.points[best + 1][0]) // 2
            p = (self.points[best][1] + self.points[best + 1][1]) // 2
            self.points.insert(best + 1, [t, p])
        else:
            return False
        self._sync_entry()
        self.queue_draw()
        return True

    def remove_point(self):
        if len(self.points) <= 2:
            return False
        self.points.pop()
        self._sync_entry()
        self.queue_draw()
        return True


class KeyboardPreview(Gtk.DrawingArea):
    """Stylized 4-zone keyboard, tinted per zone; click a zone to recolor."""

    def __init__(self, on_zone_click):
        super().__init__()
        self.zone_colors = ["FFFFFF"] * 4
        self._on_zone_click = on_zone_click
        self.set_content_width(460)
        self.set_content_height(150)
        self.set_draw_func(self._draw)
        click = Gtk.GestureClick()
        click.connect("released", self._on_click)
        self.add_controller(click)

    def set_zone_color(self, i, hexs):
        self.zone_colors[i] = hexs
        self.queue_draw()

    def _draw(self, area, cr, w, h):
        pad = 10.0
        zw = (w - 2 * pad) / 4.0
        for i in range(4):
            r = int(self.zone_colors[i][0:2], 16) / 255.0
            g = int(self.zone_colors[i][2:4], 16) / 255.0
            b = int(self.zone_colors[i][4:6], 16) / 255.0
            x = pad + i * zw
            # zone band
            cr.set_source_rgba(r, g, b, 0.85)
            self._rounded(cr, x + 2, pad, zw - 4, h - 2 * pad, 8)
            cr.fill()
            # faint key grid
            cr.set_source_rgba(0, 0, 0, 0.18)
            cr.set_line_width(1.0)
            for row in range(1, 5):
                y = pad + row * (h - 2 * pad) / 5.0
                cr.move_to(x + 6, y)
                cr.line_to(x + zw - 6, y)
                cr.stroke()
            for col in range(1, 6):
                kx = x + col * (zw - 4) / 6.0
                cr.move_to(kx, pad + 5)
                cr.line_to(kx, h - pad - 5)
                cr.stroke()
            # zone label
            cr.set_source_rgba(1, 1, 1, 0.85)
            cr.select_font_face("Sans")
            cr.set_font_size(11)
            cr.move_to(x + 8, pad + 16)
            cr.show_text("Z%d" % i)

    def _rounded(self, cr, x, y, w, h, r):
        cr.new_sub_path()
        cr.arc(x + w - r, y + r, r, -1.5708, 0)
        cr.arc(x + w - r, y + h - r, r, 0, 1.5708)
        cr.arc(x + r, y + h - r, r, 1.5708, 3.1416)
        cr.arc(x + r, y + r, r, 3.1416, 4.7124)
        cr.close_path()

    def _on_click(self, g, n, x, y):
        w = self.get_width()
        zw = (w - 20.0) / 4.0
        i = int((x - 10.0) / zw)
        if 0 <= i <= 3 and self._on_zone_click is not None:
            self._on_zone_click(i)


def fmt_temp(mc):
    """millidegree-C string honoring the unit preference"""
    if mc is None:
        return "n/a"
    c = mc / 1000.0
    if load_pref("unit", "C") == "F":
        return "%.0f°F" % (c * 9.0 / 5.0 + 32.0)
    return "%.0f°C" % c


class LightCanvas(Gtk.DrawingArea):
    """Light Studio-style canvas: gridded workspace with a stylized laptop;
    the keyboard deck carries the 4 hardware zone bands. Click a band to
    recolor that zone."""

    def __init__(self, on_zone_click):
        super().__init__()
        self.zone_colors = ["FFFFFF"] * 4
        self.selected = -1
        self.zoom = 1.0
        self._vs = 1.0
        self._on_zone_click = on_zone_click
        self._band_rects = []
        self.set_content_width(420)
        self.set_content_height(470)
        self.set_draw_func(self._draw)
        click = Gtk.GestureClick()
        click.connect("released", self._on_click)
        self.add_controller(click)

    def set_zone_color(self, i, hexs):
        self.zone_colors[i] = hexs
        self.queue_draw()

    def _rounded(self, cr, x, y, w, h, r):
        cr.new_sub_path()
        cr.arc(x + w - r, y + r, r, -1.5708, 0)
        cr.arc(x + w - r, y + h - r, r, 0, 1.5708)
        cr.arc(x + r, y + h - r, r, 1.5708, 3.1416)
        cr.arc(x + r, y + r, r, 3.1416, 4.7124)
        cr.close_path()

    def _draw(self, area, cr, w, h):
        # gridded workspace background (Light Studio canvas)
        cr.set_source_rgb(0.07, 0.07, 0.09)
        cr.rectangle(0, 0, w, h)
        cr.fill()
        cr.set_source_rgba(1, 1, 1, 0.045)
        cr.set_line_width(1.0)
        step = 24.0
        gx = 0.0
        while gx <= w:
            cr.move_to(gx, 0)
            cr.line_to(gx, h)
            cr.stroke()
            gx += step
        gy = 0.0
        while gy <= h:
            cr.move_to(0, gy)
            cr.line_to(w, gy)
            cr.stroke()
            gy += step
        # laptop geometry (zoom around center)
        cr.save()
        # fixed-aspect virtual device space: never stretch the laptop
        rw, rh = w, h
        vw, vh = 560.0, 330.0
        self._vs = min(rw / vw, rh / vh) * 0.95
        cr.translate(rw / 2.0, rh / 2.0)
        cr.scale(self.zoom * self._vs, self.zoom * self._vs)
        cr.translate(-vw / 2.0, -vh / 2.0)
        w, h = vw, vh
        lw = vw * 0.84
        scr_w, scr_h = lw * 0.78, h * 0.34
        cx = w / 2.0
        scr_x, scr_y = cx - scr_w / 2.0, h * 0.09
        # screen bezel + content
        cr.set_source_rgb(0.12, 0.12, 0.14)
        self._rounded(cr, scr_x, scr_y, scr_w, scr_h, 8)
        cr.fill()
        cr.set_source_rgb(0.16, 0.17, 0.22)
        self._rounded(cr, scr_x + 6, scr_y + 6, scr_w - 12, scr_h - 12, 5)
        cr.fill()
        # red diamond accent on screen (generic stylized mark) with glow
        dxc, dyc, dr = cx, scr_y + scr_h / 2.0, min(9.0, scr_h * 0.12)
        cr.set_source_rgba(0.85, 0.15, 0.25, 0.6)
        cr.move_to(dxc, dyc - dr)
        cr.line_to(dxc + dr, dyc)
        cr.line_to(dxc, dyc + dr)
        cr.line_to(dxc - dr, dyc)
        cr.close_path()
        cr.fill()
        # hinge
        cr.set_source_rgb(0.10, 0.10, 0.12)
        cr.rectangle(cx - scr_w / 2.0, scr_y + scr_h, scr_w, 5)
        cr.fill()
        # deck
        deck_w, deck_h = lw, h * 0.36
        deck_x, deck_y = cx - deck_w / 2.0, scr_y + scr_h + 7
        cr.set_source_rgb(0.15, 0.15, 0.17)
        self._rounded(cr, deck_x, deck_y, deck_w, deck_h, 8)
        cr.fill()
        # keyboard area with 4 zone bands
        kb_x, kb_y = deck_x + 10, deck_y + 8
        kb_w, kb_h = deck_w - 20, deck_h * 0.60
        cr.set_source_rgb(0.09, 0.09, 0.11)
        self._rounded(cr, kb_x, kb_y, kb_w, kb_h, 5)
        cr.fill()
        self._band_rects = []
        zw = kb_w / 4.0
        for i in range(4):
            bx = kb_x + i * zw
            self._band_rects.append((bx, kb_y, zw, kb_h))
        # realistic key matrix: backlight tinted per zone, dark keycaps
        rows, cols = 6, 16
        kgap = 2.0
        kw = (kb_w - (cols + 1) * kgap) / cols
        kh = (kb_h - (rows + 1) * kgap) / rows
        for ri in range(rows):
            for ci in range(cols):
                zone = min(3, ci * 4 // cols)
                zr = int(self.zone_colors[zone][0:2], 16) / 255.0
                zg = int(self.zone_colors[zone][2:4], 16) / 255.0
                zb = int(self.zone_colors[zone][4:6], 16) / 255.0
                kx = kb_x + kgap + ci * (kw + kgap)
                ky = kb_y + kgap + ri * (kh + kgap)
                # backlight glow bed
                cr.set_source_rgba(zr, zg, zb, 1.0)
                self._rounded(cr, kx, ky, kw, kh, 2.0)
                cr.fill()
                # keycap face (dark, lets the glow rim show)
                cr.set_source_rgba(0.10, 0.10, 0.12, 0.92)
                self._rounded(cr, kx + kw * 0.18, ky + kh * 0.18,
                              kw * 0.64, kh * 0.64, 1.5)
                cr.fill()
        # selection rectangle + handles around the selected zone
        if 0 <= self.selected < 4:
            bx, by, bw, bh = self._band_rects[self.selected]
            cr.set_source_rgba(1, 1, 1, 0.95)
            cr.set_line_width(1.6)
            self._rounded(cr, bx + 1, by + 1, bw - 2, bh - 2, 4)
            cr.stroke()
            cr.set_source_rgb(1, 1, 1)
            hs = 5.0
            for hx, hy in ((bx + 1, by + 1), (bx + bw - 1, by + 1),
                           (bx + 1, by + bh - 1), (bx + bw - 1, by + bh - 1)):
                cr.rectangle(hx - hs / 2, hy - hs / 2, hs, hs)
                cr.fill()
        # touchpad
        tp_w = deck_w * 0.30
        tp_h = deck_h - kb_h - 22
        if tp_h > 8:
            cr.set_source_rgb(0.11, 0.11, 0.13)
            self._rounded(cr, cx - tp_w / 2.0, kb_y + kb_h + 6,
                          tp_w, tp_h, 4)
            cr.fill()
        # device bounds outline (decorative; no resize handles - none are functional)
        fx, fy = deck_x - 8, scr_y - 8
        fw, fh = deck_w + 16, (deck_y + deck_h + 8) - fy
        cr.set_source_rgba(1, 1, 1, 0.55)
        cr.set_line_width(1.2)
        cr.rectangle(fx, fy, fw, fh)
        cr.stroke()
        cr.restore()
        # keep hit-test rects in device coordinates
        k = self.zoom * self._vs
        self._band_rects = [
            ((bx - vw / 2.0) * k + rw / 2.0, (by - vh / 2.0) * k + rh / 2.0,
             bw * k, bh * k)
            for (bx, by, bw, bh) in self._band_rects]

    def set_zoom(self, z):
        self.zoom = max(0.5, min(3.0, z))
        self.queue_draw()

    def _on_click(self, g, n, x, y):
        for i, (bx, by, bw, bh) in enumerate(self._band_rects):
            if bx <= x <= bx + bw and by <= y <= by + bh:
                self.selected = i
                self.queue_draw()
                if self._on_zone_click:
                    self._on_zone_click(i)
                return



class ColorPicker(Gtk.Popover):
    """Portable color picker: RGB sliders, hex entry, preset swatches."""

    def __init__(self, initial_hex, on_apply):
        super().__init__()
        self._on_apply = on_apply
        self._updating = False
        self._hex = initial_hex.upper()

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        for m in ("top", "bottom", "start", "end"):
            getattr(box, "set_margin_" + m)(12)
        box.set_size_request(260, -1)
        self.set_child(box)

        self.preview = Gtk.Box()
        self.preview.set_size_request(-1, 36)
        self.preview.add_css_class("card")
        box.append(self.preview)

        self.scales = {}
        for name in ("R", "G", "B"):
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            row.append(Gtk.Label(label=name, width_chars=1))
            scale = Gtk.Scale.new_with_range(
                Gtk.Orientation.HORIZONTAL, 0, 255, 1)
            scale.set_hexpand(True)
            scale.set_draw_value(True)
            scale.connect("value-changed", self._on_scale)
            row.append(scale)
            box.append(row)
            self.scales[name] = scale

        hexrow = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        hexrow.append(Gtk.Label(label="Hex"))
        self.hex_entry = Gtk.Entry()
        self.hex_entry.set_max_length(6)
        self.hex_entry.set_hexpand(True)
        self.hex_entry.connect("activate", self._on_hex_entry)
        hexbtn = Gtk.Button(label="Set")
        hexbtn.connect("clicked", self._on_hex_entry)
        hexrow.append(self.hex_entry)
        hexrow.append(hexbtn)
        box.append(hexrow)

        grid = Gtk.FlowBox()
        grid.set_max_children_per_line(6)
        grid.set_min_children_per_line(6)
        grid.set_row_spacing(6)
        grid.set_column_spacing(6)
        grid.set_selection_mode(Gtk.SelectionMode.NONE)
        for c in PRESET_COLORS:
            b = Gtk.Button()
            b.set_size_request(28, 28)
            self._paint(b, c)
            b.connect("clicked", lambda _b, cc=c: self._set_hex(cc))
            grid.append(b)
        box.append(grid)

        apply_btn = Gtk.Button(label="Apply")
        apply_btn.add_css_class("suggested-action")
        apply_btn.connect("clicked", self._on_apply_clicked)
        box.append(apply_btn)

        self._set_hex(self._hex)

    def _paint(self, widget, hexs):
        provider = Gtk.CssProvider()
        provider.load_from_data(
            ("* { background-color: #%s; border-radius: 6px; "
             "border: 1px solid alpha(black, 0.35); }" % hexs).encode())
        widget.get_style_context().add_provider(
            provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    def _set_hex(self, hexs):
        hexs = hexs.strip().lstrip("#").upper()
        if len(hexs) != 6 or any(c not in "0123456789ABCDEF" for c in hexs):
            return
        self._hex = hexs
        self._updating = True
        self.scales["R"].set_value(int(hexs[0:2], 16))
        self.scales["G"].set_value(int(hexs[2:4], 16))
        self.scales["B"].set_value(int(hexs[4:6], 16))
        self.hex_entry.set_text(hexs)
        self._paint(self.preview, hexs)
        self._updating = False

    def _on_scale(self, _scale):
        if self._updating:
            return
        hexs = "%02X%02X%02X" % tuple(
            round(self.scales[n].get_value()) for n in ("R", "G", "B"))
        self._hex = hexs
        self._updating = True
        self.hex_entry.set_text(hexs)
        self._paint(self.preview, hexs)
        self._updating = False

    def _on_hex_entry(self, *_a):
        self._set_hex(self.hex_entry.get_text())

    def _on_apply_clicked(self, _b):
        self._on_apply(self._hex)
        self.popdown()


def vital_card(label):
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
    box.add_css_class("vital-card")
    value = Gtk.Label(label="-", xalign=0)
    value.add_css_class("vital-value")
    value.set_ellipsize(3)  # Pango.EllipsizeMode.END
    lab = Gtk.Label(label=label, xalign=0)
    lab.add_css_class("vital-label")
    box.append(value)
    box.append(lab)
    box.set_hexpand(True)
    return box, value


class OmenWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app)
        self.set_title("Omen Control Centre")
        self.set_default_size(860, 620)
        self._loading = True

        provider = Gtk.CssProvider()
        provider.load_from_data(RAIL_CSS)
        Gtk.StyleContext.add_provider_for_display(
            self.get_display(), provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.toasts = Adw.ToastOverlay()
        self.toasts.set_vexpand(True)
        if os.environ.get("OMEN_READ_ONLY") == "1":
            ro = Gtk.Label(
                label="Read-only mode: readings only. Apply, Restore and "
                      "hardware changes are disabled; nothing is written "
                      "to the keyboard or fans.")
            ro.set_wrap(True)
            ro.add_css_class("readonly-banner")
            outer.append(ro)
        outer.append(self.toasts)
        self.set_content(outer)

        if not be.driver_present():
            status = Adw.StatusPage(
                title="Driver not loaded",
                description="The omen-rgb-keyboard kernel module is not "
                            "loaded. Load it and restart this app.",
                icon_name="dialog-error-symbolic")
            self.toasts.set_child(status)
            return

        body = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        self.toasts.set_child(body)

        # fixed footer for staged lighting writes; sits below the toast
        # overlay so toasts never cover the buttons
        self._footer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        self._footer.add_css_class("apply-footer")
        self._footer.set_visible(True)
        outer.append(self._footer)

        # ---- OGH-style left rail ----
        rail = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        rail.add_css_class("omen-rail")
        rail.set_size_request(64, -1)
        title = Gtk.Label(label="\u25c6", xalign=0.5)
        title.add_css_class("omen-rail-title")
        title.set_tooltip_text("OMEN Control Centre")
        rail.append(title)
        body.append(rail)

        self.stack = Gtk.Stack()
        self.stack.set_hexpand(True)
        self.stack.set_vexpand(True)
        self.stack.set_transition_type(
            Gtk.StackTransitionType.CROSSFADE)
        body.append(self.stack)

        self.nav_buttons = {}
        first = None
        for name, label, icon in NAV:
            b = Gtk.ToggleButton()
            b.add_css_class("omen-rail-button")
            b.set_has_frame(False)
            inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            inner.append(Gtk.Image.new_from_icon_name(icon))
            b.set_tooltip_text(label)
            b.update_property([Gtk.AccessibleProperty.LABEL], [label])
            b.set_child(inner)
            b.connect("toggled", self._on_nav, name)
            if first is None:
                first = b
            else:
                b.set_group(first)
            rail.append(b)
            self.nav_buttons[name] = b

        self._build_home()
        self._build_lighting()
        self._build_performance()
        self._build_vitals()
        self._build_games()
        self._build_settings()

        self._loading = False
        start = os.environ.get("OMEN_START_PAGE", "home")
        if start in self.nav_buttons:
            self.nav_buttons[start].set_active(True)
            self.stack.set_visible_child_name(start)
        else:
            self.nav_buttons["home"].set_active(True)
        GLib.timeout_add_seconds(2, self._refresh_live)
        GLib.timeout_add(400, self._refresh_live)

    # ---- shared helpers ----

    def _on_nav(self, button, name):
        if button.get_active():
            self.stack.set_visible_child_name(name)
            self._sync_footer()

    def _sync_footer(self):
        # Lighting footer: on the Lighting page, or anywhere while changes
        # are staged so they are never hidden.
        f = getattr(self, "_footer", None)
        if f is not None:
            f.set_visible(self.stack.get_visible_child_name() == "lighting"
                          or bool(getattr(self, "_staged_writes", None)))

    def _apply(self, rel, value, revert=None):
        if self._loading:
            return True
        try:
            be.set_value(rel, value)
        except be.BackendError as exc:
            self.toasts.add_toast(Adw.Toast(title="Failed: %s" % exc))
            if revert is not None:
                self._loading = True
                revert()
                self._loading = False
        return False

    def _toast(self, text):
        self.toasts.add_toast(Adw.Toast(title=text))

    def _page(self, name, maxw=920):
        # Adw.PreferencesPage scrolls internally; wrapping it in another
        # ScrolledWindow double-scrolls and breaks pointer hit-testing on
        # content below the fold (rendered position != picked position).
        clamp = Adw.Clamp()
        clamp.set_maximum_size(maxw)
        page = Adw.PreferencesPage()
        clamp.set_child(page)
        self.stack.add_named(clamp, name)
        return page

    def _paint_button(self, b, hexs):
        kb = getattr(self, "kb_preview", None)
        if kb is not None and b in getattr(self, "zone_btns", []):
            kb.set_zone_color(self.zone_btns.index(b), hexs)
        provider = Gtk.CssProvider()
        provider.load_from_data(
            (".zone-box, .swatch { background-color: #%s; }"
             % hexs).encode())
        b.get_style_context().add_provider(
            provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    # ---- Home ----

    def _build_home(self):
        page = self._page("home")

        grp = Adw.PreferencesGroup(title="OMEN 16")
        page.add(grp)
        row = Adw.ActionRow(
            title="Live dashboard",
            subtitle="Real-time readings, like OMEN Gaming Hub's "
                     "Performance Control dashboard")
        grp.add(row)

        cards = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        cards.set_margin_top(6)
        cards.set_margin_bottom(6)
        cards.set_homogeneous(True)
        self.home_cpu_temp = vital_card("CPU TEMP")
        self.home_gpu_temp = vital_card("GPU TEMP")
        self.home_cpu_load = gauge_card("CPU")
        self.home_cpu_fan = vital_card("CPU FAN")
        self.home_gpu_fan = vital_card("GPU FAN")
        grid = Gtk.Grid()
        grid.set_row_spacing(10)
        grid.set_column_spacing(10)
        grid.set_margin_top(6)
        grid.set_margin_bottom(6)
        hcards = [self.home_cpu_temp, self.home_gpu_temp,
                  self.home_cpu_load, self.home_cpu_fan,
                  self.home_gpu_fan]
        for idx, item in enumerate(hcards):
            box = item[0]
            box.set_hexpand(True)
            grid.attach(box, idx % 3, idx // 3, 1, 1)
        grp.add(grid)

        pgrp = Adw.PreferencesGroup(
            title="Power mode",
            description="Balanced is the default. Silent minimizes fan "
                        "noise (roughly OMEN's ECO, without its display "
                        "and turbo caps); Performance favors gaming.")
        page.add(pgrp)
        self.power_buttons = {}
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        box.set_homogeneous(True)
        for mode, profile in (("Silent", "silent"), ("Balanced", "normal"),
                              ("Performance", "performance")):
            b = Gtk.ToggleButton(label=mode)
            b.connect("toggled", self._on_power_mode, profile)
            box.append(b)
            self.power_buttons[profile] = b
        self.power_buttons["normal"].set_group(
            self.power_buttons["silent"])
        self.power_buttons["performance"].set_group(
            self.power_buttons["silent"])
        current = (be.read("fan/thermal_profile") or "normal").lower()
        btn = self.power_buttons.get(current)
        self._loading = True
        if btn:
            btn.set_active(True)
        self._loading = False
        prow = Adw.ActionRow()
        prow.set_child(box)
        pgrp.add(prow)

    def _on_power_mode(self, button, profile):
        if not button.get_active():
            return
        self._apply("fan/thermal_profile", profile)
        self._sync_profile_controls(profile)

    def _sync_profile_controls(self, profile):
        self._loading = True
        for p, b in getattr(self, "power_buttons", {}).items():
            b.set_active(p == profile)
        for p, b in getattr(self, "mode_cards", {}).items():
            b.set_active(p == profile)
        self._loading = False

    # ---- Lighting staged Apply (OGH-style commit workflow) ----

    DEFAULT_ZONE_COLORS = ("FFFFFF", "FFFFFF", "FFFFFF", "FFFFFF")

    def _stage(self, rel, value):
        if self._loading:
            return
        self._staged_writes[rel] = value
        self._lighting_dirty(True)

    def _lighting_dirty(self, dirty=True):
        for b in getattr(self, "_dirty_btns", ()):
            b.set_sensitive(bool(dirty))
        if os.environ.get("OMEN_READ_ONLY") == "1" and getattr(
                self, "_dirty_btns", None):
            self._dirty_btns[1].set_sensitive(False)
        self._sync_footer()

    def _on_discard(self, _b):
        self._staged_writes = {}
        self._reload_lighting_ui()
        self._lighting_dirty(False)
        self._toast("Changes discarded")

    def _on_restore_defaults(self, _b):
        for i, color in enumerate(self.DEFAULT_ZONE_COLORS):
            self._stage("rgb_zones/zone0%d" % i, color)
            self._paint_button(self.zone_btns[i], color)
            self.zone_btns[i]._omen_hex = color
        self._stage("rgb_zones/all",
                    self.DEFAULT_ZONE_COLORS[0])
        self._paint_button(self.allzone_btn,
                           self.DEFAULT_ZONE_COLORS[0])
        self.allzone_btn._omen_hex = self.DEFAULT_ZONE_COLORS[0]
        self._toast("Defaults staged - press Apply to commit")

    def _on_apply_lighting(self, _b):
        failed = False
        for rel, value in list(self._staged_writes.items()):
            try:
                be.set_value(rel, value)
            except be.BackendError as exc:
                self.toasts.add_toast(
                    Adw.Toast(title="Failed: %s" % exc))
                failed = True
        self._staged_writes = {}
        self._lighting_dirty(False)
        self._reload_lighting_ui()
        if not failed:
            self._toast("Lighting applied")

    def _reload_lighting_ui(self):
        self._loading = True
        for i, b in enumerate(self.zone_btns):
            cur = (be.read("rgb_zones/zone0%d" % i) or "FFFFFF").upper()
            if len(cur) != 6:
                cur = "FFFFFF"
            self._paint_button(b, cur)
            b._omen_hex = cur
        allcur = (be.read("rgb_zones/all") or "FFFFFF").upper()
        if len(allcur) != 6:
            allcur = "FFFFFF"
        self._paint_button(self.allzone_btn, allcur)
        self.allzone_btn._omen_hex = allcur
        self.brightness_scale.set_value(
            be.read_int("rgb_zones/brightness") or 100)
        mode = be.read("rgb_zones/animation_mode") or "static"
        btn = self.effect_buttons.get(mode)
        if btn:
            btn.set_active(True)
        keys = [k for k, _l in EFFECTS]
        if mode in keys:
            self.effect_dd.set_selected(keys.index(mode))
        self.speed_spin.set_value(
            be.read_int("rgb_zones/animation_speed") or 5)
        self._loading = False

    def _on_swatch(self, _b, hexcol):
        rel = "rgb_zones/all"
        self._paint_button(self.allzone_btn, hexcol)
        self.allzone_btn._omen_hex = hexcol
        self._stage(rel, hexcol)

    # ---- Lighting (Light Studio-style editor) ----
    # Hardware truth: the installed driver exposes 4 zones + all-zone, one
    # global animation + speed, brightness, and gradient_config. "Layers"
    # here map to what the hardware can really do (base colors + one effect)

    def _build_lighting(self):
        page = self._page("lighting", maxw=1600)
        self._staged_writes = getattr(self, "_staged_writes", {})

        tgrp = Adw.PreferencesGroup()
        tgrp.add_css_class("omen-studio")
        page.add(tgrp)
        self._ls_stack = Gtk.Stack()
        self._ls_stack.set_transition_type(
            Gtk.StackTransitionType.CROSSFADE)
        tabs = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        tabs.set_halign(Gtk.Align.START)
        tabs.set_margin_start(6)
        self._ls_tab_btns = {}
        for _tname, _tlabel in (("layout", "Layout"),
                                ("effects", "Effects")):
            tb = Gtk.ToggleButton(label=_tlabel)
            tb.add_css_class("omen-tab")
            tb.connect("toggled", self._on_ls_tab, _tname)
            self._ls_tab_btns[_tname] = tb
            tabs.append(tb)
        self._ls_tab_btns["layout"].set_active(True)
        trow = Adw.ActionRow()
        trow.set_child(tabs)
        tgrp.add(trow)
        srow2 = Adw.ActionRow()
        srow2.set_child(self._ls_stack)
        tgrp.add(srow2)

        # ---------------- Layout tab ----------------
        layout = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        layout.set_margin_top(6)
        layout.set_margin_bottom(6)

        body = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        layout.append(body)

        # Layers panel (left)
        layers = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        layers.set_size_request(190, -1)
        lcap = Gtk.Label(label="Layers")
        lcap.add_css_class("heading")
        lcap.set_halign(Gtk.Align.START)
        layers.append(lcap)
        newbtn = Gtk.Button(label="+ Create New Layer")
        newbtn.set_tooltip_text(
            "Software layers: composited live on the canvas preview. "
            "Hardware output stays 4-zone static via Apply.")
        newbtn.connect("clicked", self._on_ls_new_layer)
        layers.append(newbtn)
        self._ls_lbox = Gtk.ListBox()
        self._ls_lbox.add_css_class("boxed-list")
        self._ls_lbox.set_selection_mode(Gtk.SelectionMode.SINGLE)
        layers.append(self._ls_lbox)
        self._ls_editor = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                                  spacing=6)
        layers.append(self._ls_editor)
        body.append(layers)

        # Canvas (center)
        canvas_wrap = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                              spacing=2)
        canvas_wrap.set_hexpand(True)
        self.kb_preview = LightCanvas(self._on_preview_zone)
        self.kb_preview.set_hexpand(True)
        self.kb_preview.set_vexpand(True)
        coverlay = Gtk.Overlay()
        coverlay.set_child(self.kb_preview)
        coverlay.set_vexpand(True)
        zsel = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        zl = Gtk.Label(label="Zone Selection")
        zl.add_css_class("heading")
        zsel.append(zl)
        for _ic, _tip in (("input-mouse-symbolic", "Select zone (click a key band)"),
                          ("view-fullscreen-symbolic", "Box select: not available, driver exposes 4 fixed zones")):
            _b = Gtk.Button.new_from_icon_name(_ic)
            _b.add_css_class("flat")
            _b.set_tooltip_text(_tip)
            if "Box select" in _tip:
                _b.set_sensitive(False)
            zsel.append(_b)
        canvas_wrap.append(zsel)
        canvas_wrap.append(coverlay)
        zbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        zbar.set_halign(Gtk.Align.CENTER)
        zbar.set_valign(Gtk.Align.END)
        zbar.add_css_class("canvas-controls")
        zm = Gtk.Button.new_from_icon_name("zoom-out-symbolic")
        zm.connect("clicked", lambda _b: self._ls_zoom(-0.25))
        self._ls_zoom_label = Gtk.Label(label="100%")
        zp = Gtk.Button.new_from_icon_name("zoom-in-symbolic")
        zp.connect("clicked", lambda _b: self._ls_zoom(0.25))
        zf = Gtk.Button.new_from_icon_name("zoom-fit-best-symbolic")
        zf.set_tooltip_text("Fit")
        zm.set_tooltip_text("Zoom out")
        zp.set_tooltip_text("Zoom in")
        zf.connect("clicked", lambda _b: self._ls_zoom(0.0, reset=True))
        for wdg in (zm, self._ls_zoom_label, zp, zf):
            zbar.append(wdg)
        coverlay.add_overlay(zbar)
        snapbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        snapbar.set_halign(Gtk.Align.CENTER)
        snap = Gtk.Button(label="Stage static snapshot")
        snap.set_tooltip_text(
            "Stages the current preview as a static 4-zone color set - "
            "press Apply to write. Animations stay preview-only; the "
            "driver interface is not used for streaming.")
        snap.connect("clicked", self._on_ls_snapshot)
        snapbar.append(snap)
        snote = Gtk.Label(
            label="Layers are a software preview. Snapshot stages a static "
                  "4-zone result; animation stays in preview.")
        snote.add_css_class("dim-label")
        snote.add_css_class("caption")
        canvas_wrap.append(snote)
        canvas_wrap.append(snapbar)
        body.append(canvas_wrap)

        # Right effects panel
        rpanel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        rpanel.set_size_request(196, -1)
        ecap = Gtk.Label(label="Effects")
        ecap.add_css_class("heading")
        ecap.set_halign(Gtk.Align.START)
        rpanel.append(ecap)
        self.effect_dd = Gtk.DropDown.new_from_strings(
            [label for _k, label in EFFECTS])
        keys = [k for k, _l in EFFECTS]
        current_mode = be.read("rgb_zones/animation_mode") or "static"
        self._loading = True
        if current_mode in keys:
            self.effect_dd.set_selected(keys.index(current_mode))
        self._loading = False
        self.effect_dd.connect("notify::selected", self._on_effect_dd)
        rpanel.append(self.effect_dd)

        plab = Gtk.Label(label="Primary color")
        plab.set_halign(Gtk.Align.START)
        plab.add_css_class("dim-label")
        plab.add_css_class("caption")
        rpanel.append(plab)
        zones = [(be.read("rgb_zones/zone0%d" % i) or "").upper()
                 for i in range(4)]
        if all(len(z) == 6 for z in zones) and len(set(zones)) == 1:
            allcur = zones[0]
        else:
            allcur = (be.read("rgb_zones/all") or "FFFFFF").upper()
        if len(allcur) != 6:
            allcur = "FFFFFF"
        ab = Gtk.Button()
        ab.add_css_class("swatch")
        ab.add_css_class("pcolor-swatch")
        ab.set_halign(Gtk.Align.START)
        self._paint_button(ab, allcur)
        ab._omen_hex = allcur
        self.allzone_btn = ab
        ab.set_tooltip_text("Applies to all zones - click for picker")
        ab.connect("clicked", self._on_zone_clicked, "rgb_zones/all", ab)
        rpanel.append(ab)

        slab = Gtk.Label(label="Speed")
        slab.set_halign(Gtk.Align.START)
        slab.add_css_class("dim-label")
        slab.add_css_class("caption")
        rpanel.append(slab)
        self.speed_spin = Gtk.Scale.new_with_range(
            Gtk.Orientation.HORIZONTAL, 1, 10, 1)
        self.speed_spin.set_value(
            be.read_int("rgb_zones/animation_speed") or 5)
        self.speed_spin.set_round_digits(0)
        self.speed_spin.set_draw_value(True)
        self.speed_spin.set_value_pos(Gtk.PositionType.RIGHT)
        self.speed_spin.connect("value-changed", self._on_anim_speed)
        rpanel.append(self.speed_spin)

        note = Gtk.Label(
            label="Per-key lighting, random-color options and peripheral "
                  "sync are not exposed by the installed driver "
                  "(4 zones verified).")
        note.add_css_class("dim-label")
        note.add_css_class("caption")
        note.set_wrap(True)
        note.set_max_width_chars(28)
        note.set_halign(Gtk.Align.START)
        rpanel.append(note)
        body.append(rpanel)

        # Zone tiles under the canvas
        zbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        zbox.set_homogeneous(True)
        zbox.set_halign(Gtk.Align.CENTER)
        self.zone_btns = []
        for i in range(4):
            rel = "rgb_zones/zone0%d" % i
            current = (be.read(rel) or "FFFFFF").upper()
            if len(current) != 6:
                current = "FFFFFF"
            b = Gtk.Button()
            b.add_css_class("zone-box")
            b.set_size_request(64, 36)
            label = Gtk.Label(label="Z%d" % i)
            label.add_css_class("caption")
            wrap = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            wrap.set_halign(Gtk.Align.CENTER)
            wrap.append(b)
            wrap.append(label)
            self._paint_button(b, current)
            b._omen_hex = current
            b.connect("clicked", self._on_zone_clicked, rel, b)
            zbox.append(wrap)
            self.zone_btns.append(b)
        for i, b in enumerate(self.zone_btns):
            self.kb_preview.set_zone_color(i, b._omen_hex)
        layout.append(zbox)
        zcap = Gtk.Label(
            label="Zones in logical order (Z0-Z3) - physical side mapping "
                  "varies by keyboard; use Identify to see yours.")
        zcap.add_css_class("dim-label")
        zcap.add_css_class("caption")
        layout.append(zcap)

        self._ls_stack.add_titled(layout, "layout", "Layout")

        # ---------------- Effects tab ----------------
        effects_page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                               spacing=8)
        effects_page.set_margin_top(6)
        effects_page.set_margin_bottom(6)
        flow = Gtk.FlowBox()
        flow.set_max_children_per_line(4)
        flow.set_min_children_per_line(3)
        flow.set_row_spacing(8)
        flow.set_column_spacing(8)
        flow.set_selection_mode(Gtk.SelectionMode.NONE)
        flow.set_homogeneous(True)
        self.effect_buttons = {}
        first = None
        for key, label in EFFECTS:
            b = Gtk.ToggleButton(label=label)
            b.connect("toggled", self._on_effect, key)
            if first is None:
                first = b
            else:
                b.set_group(first)
            flow.append(b)
            self.effect_buttons[key] = b
        active = self.effect_buttons.get(current_mode)
        self._loading = True
        if active:
            active.set_active(True)
        self._loading = False
        effects_page.append(flow)

        pcap = Gtk.Label(
            label="Preview - software approximation; the keyboard\'s "
                  "driver animates the selected effect in software by "
                  "sending color updates to the keyboard repeatedly "
                  "while the laptop is awake.")
        pcap.add_css_class("dim-label")
        pcap.add_css_class("caption")
        pcap.set_halign(Gtk.Align.START)
        pcap.set_wrap(True)
        pcap.set_xalign(0)
        effects_page.append(pcap)
        pframe = Gtk.Frame()
        pframe.set_hexpand(True)
        self.eff_preview = LightCanvas(None)
        self.eff_preview.set_hexpand(True)
        self.eff_preview.set_content_height(240)
        pframe.set_child(self.eff_preview)
        effects_page.append(pframe)

        grow = Adw.ActionRow(
            title="Gradient config",
            subtitle='Used in Gradient mode. Format: '
                     '"0,1,2:FF0000,00FF00;3:800080". Staged like the '
                     'other lighting changes - the footer Apply writes it.')
        self.gradient_entry = Gtk.Entry()
        self.gradient_entry.set_placeholder_text(
            "0,1,2:FF0000,00FF00;3:800080")
        saved = be.read("rgb_zones/gradient_config")
        if saved:
            self.gradient_entry.set_text(saved)
        self.gradient_entry.set_width_chars(26)
        self.gradient_entry.set_valign(Gtk.Align.CENTER)
        gbtn = Gtk.Button(label="Stage")
        gbtn.set_tooltip_text(
            "Stages this gradient config; the footer Apply writes it")
        gbtn.set_valign(Gtk.Align.CENTER)
        gbtn.connect("clicked", self._on_gradient)
        grow.add_suffix(self.gradient_entry)
        grow.add_suffix(gbtn)
        ggrp = Adw.PreferencesGroup()
        ggrp.add(grow)
        effects_page.append(ggrp)

        self._ls_stack.add_titled(effects_page, "effects", "Effects")
        _t = os.environ.get("OMEN_LS_TAB")
        if _t in ("layout", "effects"):
            self._ls_tab_btns[_t].set_active(True)

        # ---------------- software compositor (preview only) ----------------
        self._ls_comp = le.Compositor(
            [b._omen_hex for b in self.zone_btns])
        self._ls_selected = None  # selected software layer or None (Base)
        self._ls_rebuild_layers()
        GLib.timeout_add(100, self._ls_preview_tick)
        if os.environ.get("OMEN_LS_DEMO"):
            GLib.timeout_add_seconds(2, self._ls_demo)

        # ---------------- shared controls ----------------
        ctrl = Adw.PreferencesGroup()
        page.add(ctrl)
        idrow = Adw.ActionRow(
            title="Identify zones",
            subtitle="Lights zones red, green, blue, white (0 to 3)")
        ibtn = Gtk.Button(label="Identify")
        ibtn.set_valign(Gtk.Align.CENTER)
        ibtn.connect("clicked", self._on_identify)
        idrow.add_suffix(ibtn)
        ctrl.add(idrow)

        brow = Adw.ActionRow(title="Brightness")
        self.brightness_scale = Gtk.Scale.new_with_range(
            Gtk.Orientation.HORIZONTAL, 0, 100, 1)
        self.brightness_scale.set_size_request(240, -1)
        self.brightness_scale.set_draw_value(True)
        self.brightness_scale.set_value(
            be.read_int("rgb_zones/brightness") or 100)
        self._brightness_timer = None
        self.brightness_scale.connect("value-changed", self._on_brightness)
        brow.add_suffix(self.brightness_scale)
        ctrl.add(brow)

        lockrow = Adw.ActionRow(
            title="Lock brightness",
            subtitle="Ignore the desktop dimming the keyboard when idle")
        self.lock_switch = Gtk.Switch()
        self.lock_switch.set_valign(Gtk.Align.CENTER)
        self.lock_switch.set_active(
            be.read("rgb_zones/brightness_lock") == "1")
        self.lock_switch.connect("notify::active", self._on_brightness_lock)
        lockrow.add_suffix(self.lock_switch)
        ctrl.add(lockrow)

        sw = Gtk.FlowBox()
        sw.set_max_children_per_line(18)
        sw.set_min_children_per_line(9)
        sw.set_selection_mode(Gtk.SelectionMode.NONE)
        for hexcol in ("FF0000", "FF4400", "FF8800", "FFCC00", "FFFF00",
                       "88FF00", "00FF00", "00FF88", "00FFCC", "00CCFF",
                       "0088FF", "0000FF", "4400FF", "8800FF", "CC00FF",
                       "FF00CC", "FF0066", "FFFFFF"):
            sb = Gtk.Button()
            sb.add_css_class("swatch")
            sb.set_size_request(28, 28)
            sb.set_tooltip_text("#" + hexcol)
            self._paint_button(sb, hexcol)
            sb.connect("clicked", self._on_swatch, hexcol)
            sw.append(sb)
        swrow = Adw.ActionRow(title="Colors",
                              subtitle="Basic set - applies to all zones; "
                                       "zone tiles open a custom picker")
        swrow.add_suffix(sw)
        ctrl.add(swrow)

        self._apply_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                                  spacing=8)
        self._apply_bar.set_hexpand(True)
        restore = Gtk.Button(label="Restore defaults")
        restore.connect("clicked", self._on_restore_defaults)
        discard = Gtk.Button(label="Discard changes")
        discard.connect("clicked", self._on_discard)
        applyb = Gtk.Button(label="Apply")
        applyb.add_css_class("suggested-action")
        applyb.connect("clicked", self._on_apply_lighting)
        self._dirty_btns = (discard, applyb)
        if os.environ.get("OMEN_READ_ONLY") == "1":
            applyb.set_label("Apply (read-only)")
            restore.set_sensitive(False)
            restore.set_tooltip_text("Disabled in read-only mode")
        discard.set_sensitive(False)
        applyb.set_sensitive(False)
        restore.set_tooltip_text(
            "Stages default zone colors; nothing is written until Apply")
        self._apply_bar.append(restore)
        sp = Gtk.Box()
        sp.set_hexpand(True)
        self._apply_bar.append(sp)
        self._apply_bar.append(discard)
        self._apply_bar.append(applyb)
        self._footer.append(self._apply_bar)

    def _on_ls_tab(self, btn, name):
        if not btn.get_active():
            return
        if self._ls_stack.get_child_by_name(name) is None:
            return  # pages not added yet (initial activation at build)
        for n, b in self._ls_tab_btns.items():
            if n != name:
                b.handler_block_by_func(self._on_ls_tab)
                b.set_active(False)
                b.handler_unblock_by_func(self._on_ls_tab)
        self._ls_stack.set_visible_child_name(name)

    def _ls_zoom(self, delta, reset=False):
        z = 1.0 if reset else self.kb_preview.zoom + delta
        self.kb_preview.set_zoom(z)
        self._ls_zoom_label.set_label("%d%%" % round(self.kb_preview.zoom * 100))

    def _on_ls_new_layer(self, _b):
        n = len(self._ls_comp.layers) + 1
        layer = self._ls_comp.add_layer(
            le.Layer("Layer %d" % n, ltype="wave"))
        self._ls_selected = layer
        self._ls_rebuild_layers()

    def _ls_rebuild_layers(self):
        lbox = self._ls_lbox
        while True:
            row = lbox.get_row_at_index(0)
            if row is None:
                break
            lbox.remove(row)
        base = Adw.ActionRow(title="Base",
                             subtitle="Zone colors (4 hardware zones)")
        base.set_activatable(True)
        base.connect("activated", self._on_ls_layer_row, None)
        lbox.append(base)
        for layer in self._ls_comp.layers:
            r = Adw.ActionRow(
                title=layer.name,
                subtitle="%s - zones %s" % (
                    layer.ltype,
                    ",".join("Z%d" % z for z in layer.zones)))
            r.set_activatable(True)
            r.connect("activated", self._on_ls_layer_row, layer)
            eye = Gtk.ToggleButton()
            eye.add_css_class("flat")
            eye.set_child(Gtk.Image.new_from_icon_name(
                "view-reveal-symbolic" if layer.enabled
                else "view-conceal-symbolic"))
            eye.set_active(layer.enabled)
            eye.set_tooltip_text("Show/hide layer in preview")
            eye.connect("toggled", self._on_ls_eye, layer)
            r.add_suffix(eye)
            dele = Gtk.Button()
            dele.add_css_class("flat")
            dele.set_child(Gtk.Image.new_from_icon_name(
                "user-trash-symbolic"))
            dele.set_tooltip_text("Delete layer")
            dele.connect("clicked", self._on_ls_delete, layer)
            r.add_suffix(dele)
            lbox.append(r)
            if layer is self._ls_selected:
                lbox.select_row(r)
        self._ls_rebuild_editor()

    def _on_ls_layer_row(self, _row, layer):
        self._ls_selected = layer
        if layer is None:
            self._ls_stack.set_visible_child_name("layout")
        self._ls_rebuild_editor()

    def _on_ls_eye(self, btn, layer):
        layer.enabled = btn.get_active()
        btn.set_child(Gtk.Image.new_from_icon_name(
            "view-reveal-symbolic" if layer.enabled
            else "view-conceal-symbolic"))
        self._ls_rebuild_layers()

    def _on_ls_delete(self, _btn, layer):
        self._ls_comp.remove_layer(layer)
        if self._ls_selected is layer:
            self._ls_selected = None
        self._ls_rebuild_layers()

    def _ls_rebuild_editor(self):
        box = self._ls_editor
        while True:
            ch = box.get_first_child()
            if ch is None:
                break
            box.remove(ch)
        layer = self._ls_selected
        if layer is None:
            return
        cap = Gtk.Label(label="Edit: " + layer.name)
        cap.add_css_class("heading")
        cap.set_halign(Gtk.Align.START)
        box.append(cap)
        dd = Gtk.DropDown.new_from_strings(
            [t.capitalize() for t in le.LAYER_TYPES])
        dd.set_selected(le.LAYER_TYPES.index(layer.ltype))
        dd.connect("notify::selected", self._on_ls_type, layer)
        box.append(dd)
        for attr, label in (("primary", "Primary"), ("secondary", "Secondary")):
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            lab = Gtk.Label(label=label)
            lab.set_hexpand(True)
            lab.set_halign(Gtk.Align.START)
            row.append(lab)
            b = Gtk.Button()
            b.add_css_class("zone-box")
            b.set_size_request(32, 22)
            self._paint_button(b, getattr(layer, attr))
            b._omen_hex = getattr(layer, attr)
            b.connect("clicked", self._on_ls_color, layer, attr)
            row.append(b)
            box.append(row)
        sp = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 5)
        sp.set_value(layer.speed)
        sp.set_tooltip_text("Speed")
        sp.connect("value-changed", self._on_ls_speed, layer)
        box.append(sp)
        chips = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        chips.set_halign(Gtk.Align.CENTER)
        for z in range(4):
            t = Gtk.ToggleButton(label="Z%d" % z)
            t.set_active(z in layer.zones)
            t.connect("toggled", self._on_ls_zone_chip, layer, z)
            chips.append(t)
        box.append(chips)

    def _on_ls_type(self, dd, _p, layer):
        layer.ltype = le.LAYER_TYPES[dd.get_selected()]

    def _on_ls_color(self, b, layer, attr):
        picker = ColorPicker(
            getattr(layer, attr),
            lambda h: self._ls_color_done(layer, attr, h, b))
        picker.set_parent(b)
        picker.popup()

    def _ls_color_done(self, layer, attr, hexs, b):
        setattr(layer, attr, hexs)
        self._paint_button(b, hexs)

    def _on_ls_speed(self, scale, layer):
        layer.speed = int(scale.get_value())

    def _on_ls_zone_chip(self, btn, layer, z):
        zones = set(layer.zones)
        if btn.get_active():
            zones.add(z)
        elif len(zones) > 1:
            zones.discard(z)
        else:
            btn.set_active(True)  # keep at least one zone
            return
        layer.zones = tuple(sorted(zones))

    def _on_ls_snapshot(self, _b):
        out = self._ls_comp.tick()
        for i in range(4):
            self._stage("rgb_zones/zone0%d" % i, out[i])
        # driver forces static on zone writes; stage it so the UI agrees
        # and Discard reverts it like any staged change
        self._stage("rgb_zones/animation_mode", "static")
        self._toast("Static snapshot staged - press Apply to write")

    def _ls_demo(self):
        w = self._ls_comp.add_layer(
            le.Layer("Wave", ltype="wave", primary="FF8800",
                     secondary="220044", speed=60))
        b = self._ls_comp.add_layer(
            le.Layer("Breath", ltype="breathing", primary="00CCFF",
                     speed=35, zones=(0, 3)))
        self._ls_selected = w
        self._ls_rebuild_layers()
        return False

    def _ls_preview_tick(self):
        comp = getattr(self, "_ls_comp", None)
        kb = getattr(self, "kb_preview", None)
        if comp is None or kb is None:
            return True
        for i, b in enumerate(getattr(self, "zone_btns", [])):
            comp.set_base(i, b._omen_hex)
        if comp.layers:
            out = comp.tick()
        else:
            out = [b._omen_hex for b in getattr(self, "zone_btns", [])]
        if out != getattr(self, "_ls_last_out", None):
            self._ls_last_out = list(out)
            for i in range(min(4, len(out))):
                kb.set_zone_color(i, out[i])
        ep = getattr(self, "eff_preview", None)
        if (ep is not None and
                self._ls_stack.get_visible_child_name() == "effects"):
            mode = "static"
            for k, btn in getattr(self, "effect_buttons", {}).items():
                if btn.get_active():
                    mode = k
                    break
            ltype = EFFECT_PREVIEW.get(mode, "static")
            layer = getattr(self, "_eff_layer", None)
            if layer is None:
                layer = le.Layer("preview")
                self._eff_layer = layer
            layer.ltype = ltype
            layer.primary = getattr(self.allzone_btn, "_omen_hex",
                                    "FFFFFF")
            layer.speed = int(self.speed_spin.get_value()) * 10
            now = time.time()
            hexes = [le._hx(layer.color_at(z, now)) for z in range(4)]
            if hexes != getattr(self, "_eff_last", None):
                self._eff_last = list(hexes)
                for i in range(4):
                    ep.set_zone_color(i, hexes[i])
        return True

    def _on_effect_dd(self, dd, _pspec):
        if self._loading:
            return
        keys = [k for k, _l in EFFECTS]
        idx = dd.get_selected()
        if 0 <= idx < len(keys):
            self._stage("rgb_zones/animation_mode", keys[idx])


    def _on_preview_zone(self, i):
        b = self.zone_btns[i]
        picker = ColorPicker(
            b._omen_hex,
            lambda h: self._on_color_chosen("rgb_zones/zone0%d" % i, h, b))
        picker.set_parent(b)
        picker.popup()

    def _on_zone_clicked(self, b, rel, _b):
        picker = ColorPicker(b._omen_hex,
                             lambda h: self._on_color_chosen(rel, h, b))
        picker.set_parent(b)
        picker.popup()

    def _on_color_chosen(self, rel, hexs, b):
        self._paint_button(b, hexs)
        b._omen_hex = hexs
        self._stage(rel, hexs)

    def _on_identify(self, _b):
        for i, c in enumerate(["FF0000", "00FF00", "0000FF", "FFFFFF"]):
            self._apply("rgb_zones/zone0%d" % i, c)
            self._paint_button(self.zone_btns[i], c)
            self.zone_btns[i]._omen_hex = c
        self._toast("Zones lit red, green, blue, white (zone 0 to 3)")

    def _on_brightness(self, scale):
        if self._loading:
            return
        if self._brightness_timer is not None:
            GLib.source_remove(self._brightness_timer)
        self._brightness_timer = GLib.timeout_add(
            350, self._apply_brightness, int(scale.get_value()))

    def _apply_brightness(self, value):
        self._brightness_timer = None
        self._stage("rgb_zones/brightness", str(value))
        return GLib.SOURCE_REMOVE

    def _on_brightness_lock(self, sw, _p):
        self._apply("rgb_zones/brightness_lock",
                    "1" if sw.get_active() else "0",
                    revert=lambda: sw.set_active(not sw.get_active()))

    def _on_effect(self, button, key):
        if not button.get_active():
            return
        self._stage("rgb_zones/animation_mode", key)

    def _on_anim_speed(self, spin):
        self._stage("rgb_zones/animation_speed",
                    str(int(spin.get_value())))

    def _on_gradient(self, _b):
        self._stage("rgb_zones/gradient_config",
                    self.gradient_entry.get_text().strip())

    # ---- Performance (OGH Performance Control mirror) ----

    def _build_performance(self):
        page = self._page("performance")

        dgrp = Adw.PreferencesGroup(title="Performance Control")
        page.add(dgrp)
        cards = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        cards.set_margin_top(6)
        cards.set_margin_bottom(6)
        cards.set_homogeneous(True)
        self.perf_cpu_temp = vital_card("CPU TEMP")
        self.perf_gpu_temp = vital_card("GPU TEMP")
        self.perf_cpu_load = vital_card("CPU USE")
        for box, _ in (self.perf_cpu_temp, self.perf_gpu_temp,
                       self.perf_cpu_load):
            cards.append(box)
        dgrp.add(cards)

        mgrp = Adw.PreferencesGroup(
            title="Optimize your performance",
            description="Select the mode that works best for you. "
                        "Comfort maps to the driver's silent preset, "
                        "Default to normal, Performance to performance.")
        page.add(mgrp)
        cards2 = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        cards2.set_homogeneous(True)
        self.mode_cards = {}
        for name, profile, desc in (
            ("Comfort", "silent",
             "Suitable for casual tasks. Lowers CPU and GPU "
             "temperatures."),
            ("Default", "normal", "Suitable for all types of tasks."),
            ("Performance", "performance",
             "Suitable for gaming. May increase heat and noise "
             "levels."),
        ):
            b = Gtk.ToggleButton()
            b.add_css_class("mode-card")
            inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            inner.set_margin_top(12)
            inner.set_margin_bottom(12)
            inner.set_margin_start(12)
            inner.set_margin_end(12)
            t = Gtk.Label(label=name)
            t.add_css_class("title-4")
            d = Gtk.Label(label=desc, wrap=True)
            d.add_css_class("caption")
            d.set_justify(Gtk.Justification.CENTER)
            inner.append(t)
            inner.append(d)
            b.set_child(inner)
            b.connect("toggled", self._on_mode_card, profile)
            cards2.append(b)
            self.mode_cards[profile] = b
        self.mode_cards["normal"].set_group(self.mode_cards["silent"])
        self.mode_cards["performance"].set_group(
            self.mode_cards["silent"])
        current = (be.read("fan/thermal_profile") or "normal").lower()
        btn = self.mode_cards.get(current)
        self._loading = True
        if btn:
            btn.set_active(True)
        self._loading = False
        mgrp.add(cards2)

        tgrp = Adw.PreferencesGroup(
            title="Fan Speed",
            description="AUTO: the BIOS adjusts fan speed to load. "
                        "MAX: both fans at full speed. "
                        "MANUAL: your own fan curve, where supported.")
        page.add(tgrp)

        tc = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        tc.set_homogeneous(True)
        self.tc_buttons = {}
        for label in ("AUTO", "MAX"):
            b = Gtk.ToggleButton(label=label)
            b.connect("toggled", self._on_thermal_control, label)
            tc.append(b)
            self.tc_buttons[label] = b
        self.tc_buttons["MAX"].set_group(self.tc_buttons["AUTO"])
        curve_supported = be.fan_curve_supported()
        if curve_supported:
            b = Gtk.ToggleButton(label="MANUAL")
            b.connect("toggled", self._on_thermal_control, "MANUAL")
            b.set_group(self.tc_buttons["AUTO"])
            tc.append(b)
            self.tc_buttons["MANUAL"] = b
        max_on = be.read("fan/max_fan") == "1"
        curve_on = be.read("fan/fan_curve_enable") == "1"
        self._loading = True
        if max_on:
            self.tc_buttons["MAX"].set_active(True)
        elif curve_on and curve_supported:
            self.tc_buttons["MANUAL"].set_active(True)
        else:
            self.tc_buttons["AUTO"].set_active(True)
        self._loading = False
        trow = Adw.ActionRow()
        trow.set_child(tc)
        tgrp.add(trow)

        rpms = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        rpms.set_homogeneous(True)
        self.perf_cpu_fan = vital_card("CPU FAN")
        self.perf_gpu_fan = vital_card("GPU FAN")
        for box, _ in (self.perf_cpu_fan, self.perf_gpu_fan):
            rpms.append(box)
        tgrp.add(rpms)

        if curve_supported:
            fgrp = Adw.PreferencesGroup(
                title="Manual fan curve",
                description='Points as "tempC:percent", 2 to 8, e.g. '
                            '"30:20 40:30 55:45 70:65 85:90". The driver '
                            "clamps percentages to the BIOS fan table. "
                            "Rollback: re-applying a power mode restores "
                            "the preset curve.")
            page.add(fgrp)
            crow2 = Adw.ActionRow(title="Curve points")
            self.curve_entry = Gtk.Entry()
            saved = be.read("fan/fan_curve")
            if saved:
                self.curve_entry.set_text(saved)
            self.curve_entry.set_width_chars(32)
            self.curve_entry.set_valign(Gtk.Align.CENTER)
            self.curve_graph = CurveGraph(self.curve_entry)
            self.curve_entry.connect(
                "changed",
                lambda e: self.curve_graph.load_from_entry())
            grow = Adw.ActionRow(
                title="Drag points to shape the curve",
                subtitle="X: temperature, Y: fan speed")
            grow.set_child(self.curve_graph)
            fgrp.add(grow)
            cbtn = Gtk.Button(label="Apply")
            cbtn.set_valign(Gtk.Align.CENTER)
            cbtn.connect("clicked", self._on_curve_apply)
            addb = Gtk.Button(label="+ Point")
            addb.set_valign(Gtk.Align.CENTER)
            addb.connect(
                "clicked",
                lambda b: self.curve_graph.add_point()
                or self._toast("Curve already has 8 points"))
            rmb = Gtk.Button(label="- Point")
            rmb.set_valign(Gtk.Align.CENTER)
            rmb.connect(
                "clicked",
                lambda b: self.curve_graph.remove_point()
                or self._toast("Curve needs at least 2 points"))
            crow2.add_suffix(self.curve_entry)
            crow2.add_suffix(addb)
            crow2.add_suffix(rmb)
            crow2.add_suffix(cbtn)
            fgrp.add(crow2)
            resetrow = Adw.ActionRow(
                title="Rollback",
                subtitle="Re-apply the Balanced preset curve")
            rbtn = Gtk.Button(label="Reset to Balanced preset")
            rbtn.set_valign(Gtk.Align.CENTER)
            rbtn.connect("clicked", self._on_curve_reset)
            resetrow.add_suffix(rbtn)
            fgrp.add(resetrow)
        else:
            fgrp = Adw.PreferencesGroup(
                title="Manual fan curve",
                description="This model's fan interface does not "
                            "support manual curves. AUTO and MAX "
                            "still work.")
            page.add(fgrp)
            row = Adw.ActionRow(title="Not supported on this model")
            row.add_suffix(Gtk.Image.new_from_icon_name(
                "dialog-information-symbolic"))
            fgrp.add(row)

        pgrp = Adw.PreferencesGroup(
            title="CPU power limits",
            description="PL1/PL2 are BIOS defaults. Custom limits stay "
                        "read-only here: no safe wattage range is "
                        "published for this exact model. Intel reference "
                        "for i7-14650HX: 55 W base, 157 W max turbo.")
        page.add(pgrp)
        pl = be.read("fan/power_limits")
        pl_text = "BIOS default"
        if pl and pl not in ("default", "0 0"):
            parts = pl.split()
            if len(parts) == 2 and all(p.isdigit() for p in parts):
                pl_text = "PL1 %s W, PL2 %s W (last written)" % (
                    parts[0], parts[1])
        pgrp.add(Adw.ActionRow(title="Current limits", subtitle=pl_text))

    def _on_mode_card(self, button, profile):
        if not button.get_active():
            return
        self._apply("fan/thermal_profile", profile)
        self._sync_profile_controls(profile)

    def _on_thermal_control(self, button, label):
        if self._loading or not button.get_active():
            return
        curve_supported = "MANUAL" in self.tc_buttons
        if label == "AUTO":
            # Verified on-device sequence (2026-10-27 incident): writing
            # max_fan 0 alone leaves the EC at its last state (fans can
            # sit stopped); re-applying the thermal profile restores the
            # preset fan behavior and restarts the curve worker.
            self._apply("fan/max_fan", "0")
            profile = (be.read("fan/thermal_profile") or "normal").lower()
            if profile not in be.THERMAL_PROFILES:
                profile = "normal"
            self._apply("fan/thermal_profile", profile)
            back_max = be.read("fan/max_fan")
            back_profile = be.read("fan/thermal_profile")
            # Readback proves the preset was ACCEPTED, not that fans
            # recovered. RPM is the observable fan state: report it
            # separately, without claiming cooling or recovery.
            cpu_rpm, gpu_rpm = be.read_rpm()
            rpm_s = None
            if cpu_rpm is not None and gpu_rpm is not None:
                rpm_s = "fans read %d/%d RPM" % (cpu_rpm, gpu_rpm)
            if back_max == "0" and back_profile == profile:
                if rpm_s is not None:
                    if cpu_rpm == 0 and gpu_rpm == 0:
                        self._toast(
                            "AUTO accepted (%s profile) but fans read "
                            "0/0 RPM - watch temps" % profile)
                    else:
                        self._toast(
                            "AUTO accepted (%s profile), %s"
                            % (profile, rpm_s))
                else:
                    self._toast(
                        "AUTO accepted (%s profile); fan RPM not "
                        "readable here" % profile)
            else:
                self._toast("AUTO restore uncertain: max_fan=%r "
                            "profile=%r - check fans" % (
                                back_max, back_profile))
        elif label == "MAX":
            self._apply("fan/max_fan", "1")
        elif label == "MANUAL":
            if not curve_supported:
                return
            self._apply("fan/max_fan", "0")
            points = self.curve_entry.get_text().strip()
            if points:
                self._apply("fan/fan_curve", points)
            self._apply("fan/fan_curve_enable", "1")

    def _on_curve_apply(self, _b):
        self._apply("fan/fan_curve", self.curve_entry.get_text().strip())
        g = getattr(self, "curve_graph", None)
        if g is not None:
            g.load_from_entry()

    def _on_curve_reset(self, _b):
        self._apply("fan/thermal_profile", "normal")
        saved = be.read("fan/fan_curve")
        if saved:
            self._loading = True
            self.curve_entry.set_text(saved)
            self._loading = False
            g = getattr(self, "curve_graph", None)
            if g is not None:
                g.load_from_entry()

    # ---- System Vitals ----

    def _on_unit(self, row, _p):
        save_pref("unit", "F" if row.get_selected() == 1 else "C")
        self._refresh_live()

    def _build_vitals(self):
        page = self._page("vitals")

        ugrp = Adw.PreferencesGroup()
        page.add(ugrp)
        units = Gtk.StringList()
        units.append("°C")
        units.append("°F")
        urow = Adw.ComboRow(title="Temperature unit", model=units)
        urow.set_selected(1 if load_pref("unit", "C") == "F" else 0)
        urow.connect("notify::selected", self._on_unit)
        ugrp.add(urow)

        grp = Adw.PreferencesGroup(title="System Vitals")
        page.add(grp)
        grid = Gtk.Grid()
        grid.set_row_spacing(10)
        grid.set_column_spacing(10)
        grid.set_margin_top(6)
        grid.set_margin_bottom(6)
        self.v_cpu_temp = vital_card("CPU TEMP")
        self.v_gpu_temp = vital_card("GPU TEMP")
        self.v_cpu_load = gauge_card("CPU")
        self.v_ram = gauge_card("MEMORY")
        self.v_storage = vital_card("STORAGE USED")
        self.v_net = vital_card("NETWORK")
        cards = [self.v_cpu_temp, self.v_gpu_temp, self.v_cpu_load,
                 self.v_ram, self.v_storage, self.v_net]
        for idx, item in enumerate(cards):
            box = item[0]
            box.set_hexpand(True)
            grid.attach(box, idx % 3, idx // 3, 1, 1)
        grp.add(grid)

        fgrp = Adw.PreferencesGroup(title="Fans")
        page.add(fgrp)
        fr = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        fr.set_homogeneous(True)
        self.v_cpu_fan = vital_card("CPU FAN")
        self.v_gpu_fan = vital_card("GPU FAN")
        for box, _ in (self.v_cpu_fan, self.v_gpu_fan):
            fr.append(box)
        fgrp.add(fr)

        pgrp = Adw.PreferencesGroup(
            title="Top Processes",
            description="Highest CPU use since the last refresh")
        page.add(pgrp)
        self.proc_rows = []
        for _ in range(5):
            r = Adw.ActionRow(title="-", subtitle="-")
            pgrp.add(r)
            self.proc_rows.append(r)
        self._prev_proc = {}
        self._prev_cpu_total = None

    def _sample_top_processes(self):
        procs = {}
        try:
            with open("/proc/stat") as fh:
                total = sum(int(x) for x in
                            fh.readline().split()[1:])
        except OSError:
            return None
        for pid in os.listdir("/proc"):
            if not pid.isdigit():
                continue
            try:
                with open("/proc/%s/stat" % pid) as fh:
                    data = fh.read()
            except OSError:
                continue
            rparen = data.rfind(")")
            if rparen < 0:
                continue
            comm = data[data.find("(") + 1:rparen]
            fields = data[rparen + 2:].split()
            if len(fields) < 13:
                continue
            try:
                jiffies = int(fields[11]) + int(fields[12])
            except ValueError:
                continue
            procs[pid] = (comm, jiffies)
        if self._prev_cpu_total is None:
            self._prev_proc = procs
            self._prev_cpu_total = total
            return None
        total_delta = total - self._prev_cpu_total
        usage = []
        if total_delta > 0:
            for pid, (comm, jiffies) in procs.items():
                prev = self._prev_proc.get(pid)
                if prev is None:
                    continue
                delta = jiffies - prev[1]
                if delta > 0:
                    usage.append((100.0 * delta / total_delta, comm))
        self._prev_proc = procs
        self._prev_cpu_total = total
        usage.sort(reverse=True)
        return usage[:5]

    # ---- My Games ----

    def _build_games(self):
        page = self._page("games")
        grp = Adw.PreferencesGroup(
            title="My Games",
            description="Installed Steam games on this machine. "
                        "Launching opens Steam, with GameMode and "
                        "MangoHud applied automatically when those "
                        "Fedora packages are installed.")
        page.add(grp)
        games = games_backend.list_games() if games_backend else []
        if not games:
            grp.add(Adw.ActionRow(
                title="No installed Steam games found",
                subtitle="Games you install in Steam appear here"))
        flow = Gtk.FlowBox()
        flow.set_max_children_per_line(4)
        flow.set_min_children_per_line(2)
        flow.set_row_spacing(10)
        flow.set_column_spacing(10)
        flow.set_selection_mode(Gtk.SelectionMode.NONE)
        flow.set_homogeneous(True)
        for game in games[:50]:
            card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            card.add_css_class("card")
            card.set_margin_top(6)
            card.set_margin_bottom(6)
            card.set_margin_start(6)
            card.set_margin_end(6)
            pic = Gtk.Picture()
            pic.set_size_request(-1, 100)
            pic.set_keep_aspect_ratio(True)
            pic.set_can_shrink(True)
            card.append(pic)
            name = Gtk.Label(label=game["name"], wrap=True)
            name.add_css_class("caption")
            card.append(name)
            btn = Gtk.Button(label="Launch")
            btn.connect("clicked", self._on_launch_game, game["appid"])
            card.append(btn)

            def set_art(path, pic=pic, card=card):
                if path:
                    pic.set_filename(path)
                return False

            fetch_art(game["appid"], set_art)
            flow.append(card)
        grow = Adw.ActionRow()
        grow.set_child(flow)
        grp.add(grow)

    def _on_launch_game(self, _b, appid):
        if not games_backend:
            return
        # Native equivalents of OGH's Boost/overlay: run through GameMode
        # and MangoHud when the user has them installed. Absent tools are
        # simply skipped - no install prompts, no privilege changes.
        cmd = list(games_backend.launch_command(appid))
        if shutil.which("gamemoderun"):
            cmd = ["gamemoderun"] + cmd
        env = dict(os.environ)
        if shutil.which("mangohud"):
            env["MANGOHUD"] = "1"
        subprocess.Popen(cmd, env=env)

    # ---- Settings ----

    def _build_settings(self):
        page = self._page("settings")

        agrp = Adw.PreferencesGroup(title="Appearance")
        page.add(agrp)
        themes = Gtk.StringList()
        for t in ("Auto", "Light", "Dark"):
            themes.append(t)
        self.theme_combo = Adw.ComboRow(title="Theme", model=themes)
        current = load_theme_pref()
        self.theme_combo.set_selected(
            {"auto": 0, "light": 1, "dark": 2}.get(current, 2))
        self.theme_combo.connect("notify::selected", self._on_theme)
        agrp.add(self.theme_combo)

        kgrp = Adw.PreferencesGroup(
            title="Key assignments",
            description="The Omen key can open this app, like the OMEN "
                        "key opens OMEN Gaming Hub on Windows.")
        page.add(kgrp)
        if omenkey_backend and omenkey_backend.schema_available():
            krow = Adw.ActionRow(
                title="Omen key opens this app",
                subtitle="Uses a GNOME custom shortcut (XF86DOS). "
                         "Reversible; no admin rights needed.")
            self.omen_key_switch = Gtk.Switch()
            self.omen_key_switch.set_valign(Gtk.Align.CENTER)
            self.omen_key_switch.set_active(omenkey_backend.is_bound())
            self.omen_key_switch.connect(
                "notify::active", self._on_omen_key)
            krow.add_suffix(self.omen_key_switch)
            kgrp.add(krow)
        else:
            kgrp.add(Adw.ActionRow(
                title="Shortcut settings unavailable",
                subtitle="GNOME settings schema not found on this system"))

        ggrp = Adw.PreferencesGroup(
            title="Graphics",
            description="Running a specific app on the NVIDIA GPU works "
                        "on Linux via PRIME render offload. Whole-"
                        "display hybrid/discrete switching is a BIOS "
                        "option on this model class (exact options on "
                        "this unit unverified); OGH's Graphics switcher "
                        "uses the Windows graphics stack.")
        page.add(ggrp)

        grp = Adw.PreferencesGroup(title="About")
        page.add(grp)
        grp.add(Adw.ActionRow(
            title="Driver",
            subtitle="omen-rgb-keyboard (OmenLinux): RGB lighting, "
                     "fans, thermal profiles"))
        grp.add(Adw.ActionRow(
            title="Settings persistence",
            subtitle="The driver is designed to save lighting and fan "
                     "settings and restore them at boot (not yet "
                     "verified on this machine)"))
        mgrp = Adw.PreferencesGroup(
            title="OMEN Gaming Hub equivalents",
            description="How this app's coverage maps to the Windows "
                        "OMEN Gaming Hub feature set")
        page.add(mgrp)
        mgrp.add(Adw.ActionRow(
            title="Covered by this app",
            subtitle="Keyboard lighting, fan and thermal control, "
                     "system vitals, game library, Omen-key shortcut"))
        mgrp.add(Adw.ActionRow(
            title="Covered by native Fedora tools",
            subtitle="Macros and key remapping: input-remapper. "
                     "In-game FPS overlay: MangoHud. Per-game "
                     "performance boost: GameMode. All in Fedora's "
                     "repos (dnf install)."))
        mgrp.add(Adw.ActionRow(
            title="Possible natively, not yet implemented here",
            subtitle="Per-app network prioritization (Linux cgroup "
                     "v2 + nftables/tc can do it; HP's own Network "
                     "Booster implementation is Windows-only). "
                     "Light Studio-style custom effect canvas."))
        mgrp.add(Adw.ActionRow(
            title="Not available on this hardware",
            subtitle="OMEN AI, Shop Games, Deals, Gallery (HP cloud "
                     "and storefront services). GPU display-mode (MUX) "
                     "switching is BIOS-only on this model."))
        mgrp.add(Adw.ActionRow(
            title="Unverified on this unit",
            subtitle="Overclocking/undervolting: Intel HX chips are "
                     "unlocked, but HP notebook BIOSes usually lock "
                     "voltage control; OGH itself offers no "
                     "overclocking on this notebook class. Needs a "
                     "device check before any claims."))
        mgrp.add(Adw.ActionRow(
            title="Software layers: preview-only",
            subtitle="Light Studio-style multi-layer compositing runs "
                     "live on the canvas preview. Not streamed to the "
                     "keyboard: per the driver source, every zone write "
                     "force-stops the driver animation and does a "
                     "synchronous state save to disk, and sustained "
                     "hardware write-rate tolerance is not validated. "
                     "Use 'Stage static snapshot' for a static 4-zone "
                     "result."))

    def _on_theme(self, row, _p):
        theme = ("auto", "light", "dark")[row.get_selected()]
        save_theme_pref(theme)
        scheme = {"auto": Adw.ColorScheme.DEFAULT,
                  "light": Adw.ColorScheme.FORCE_LIGHT,
                  "dark": Adw.ColorScheme.FORCE_DARK}[theme]
        self.get_application().props.style_manager.set_color_scheme(scheme)

    def _on_omen_key(self, sw, _p):
        if self._loading:
            return
        ok, err = (omenkey_backend.bind() if sw.get_active()
                   else omenkey_backend.unbind())
        if not ok:
            self._toast("Could not change shortcut: %s" % err)
            self._loading = True
            sw.set_active(not sw.get_active())
            self._loading = False

    # ---- live refresh ----

    def _refresh_live(self):
        cpu_rpm, gpu_rpm = be.read_rpm()

        ct_s = fmt_temp(be.read_cpu_temp_mc())
        gt_s = fmt_temp(be.read_gpu_temp_mc())
        load = be.read_cpu_load_pct()
        load_s = ("%.0f%%" % load) if load is not None else "-"
        total, used = be.read_mem()
        ram_s = ("%.0f%%" % (100.0 * used / total)) if total else "n/a"
        st = be.read_storage()
        net = be.read_net_kbps()

        self.home_cpu_temp[1].set_label(ct_s)
        self.home_gpu_temp[1].set_label(gt_s)
        self.home_cpu_load[1].set_value((load or 0.0) / 100.0, load_s)
        self.home_cpu_load[2].set_label("Utilization")
        self.home_cpu_fan[1].set_label(
            ("%d RPM" % cpu_rpm) if cpu_rpm is not None else "n/a")
        self.home_gpu_fan[1].set_label(
            ("%d RPM" % gpu_rpm) if gpu_rpm is not None else "n/a")

        self.perf_cpu_temp[1].set_label(ct_s)
        self.perf_gpu_temp[1].set_label(gt_s)
        self.perf_cpu_load[1].set_label(load_s)
        self.perf_cpu_fan[1].set_label(
            ("%d RPM" % cpu_rpm) if cpu_rpm is not None else "n/a")
        self.perf_gpu_fan[1].set_label(
            ("%d RPM" % gpu_rpm) if gpu_rpm is not None else "n/a")

        self.v_cpu_temp[1].set_label(ct_s)
        self.v_gpu_temp[1].set_label(gt_s)
        load_f = (load or 0.0) / 100.0
        self.v_cpu_load[1].set_value(load_f, load_s)
        ram_f = (100.0 * used / total / 100.0) if total else 0.0
        self.v_ram[1].set_value(ram_f, ram_s)
        self.v_ram[2].set_label(
            ("%.1f/%.1f GB" % (used / 1e6, total / 1e6)) if total else "-")
        self.v_cpu_load[2].set_label("Utilization")
        self.v_storage[1].set_label(
            ("%.0f%%" % st) if st is not None else "n/a")
        self.v_net[1].set_label(
            ("↓%.0f ↑%.0f KB/s" % net) if net is not None else "n/a")
        self.v_cpu_fan[1].set_label(
            ("%d RPM" % cpu_rpm) if cpu_rpm is not None else "n/a")
        self.v_gpu_fan[1].set_label(
            ("%d RPM" % gpu_rpm) if gpu_rpm is not None else "n/a")

        usage = self._sample_top_processes()
        if usage is not None:
            for i, row in enumerate(self.proc_rows):
                if i < len(usage):
                    pct, comm = usage[i]
                    row.set_title(comm)
                    row.set_subtitle("%.1f%% CPU" % pct)
                else:
                    row.set_title("-")
                    row.set_subtitle("-")
        return GLib.SOURCE_CONTINUE


class OmenApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID,
                         flags=Gio.ApplicationFlags.FLAGS_NONE)
        theme = load_theme_pref()
        if os.environ.get("OMEN_FORCE_DARK") == "1":
            theme = "dark"
        scheme = {"auto": Adw.ColorScheme.DEFAULT,
                  "light": Adw.ColorScheme.FORCE_LIGHT,
                  "dark": Adw.ColorScheme.FORCE_DARK}[theme]
        self.props.style_manager.set_color_scheme(scheme)

    def do_activate(self):
        win = self.props.active_window
        if not win:
            win = OmenWindow(self)
        win.present()


def main():
    Adw.init()
    app = OmenApp()
    return app.run(sys.argv)


if __name__ == "__main__":
    main()
