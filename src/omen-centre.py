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
import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
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

PRESET_COLORS = [
    "FF0000", "FF8000", "FFFF00", "00FF00", "00FFFF", "0000FF",
    "8000FF", "FF00FF", "FF0080", "FFFFFF", "AAAAAA", "555555",
]

RAIL_CSS = b"""
.omen-rail { background-color: #15151c; }
.omen-rail-button { border-radius: 8px; margin: 2px 8px; padding: 8px 4px;
  color: #b9b9c6; }
.omen-rail-button:hover { background-color: #23232e; }
.omen-rail-button:checked { background-color: #343444; color: #ffffff; }
.omen-rail-title { color: #ffffff; font-weight: 800; font-size: 18px;
  letter-spacing: 1px; margin: 12px 0 2px 0; }
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
"""


def load_theme_pref():
    try:
        with open(CONFIG_PATH) as fh:
            for line in fh:
                if line.startswith("theme="):
                    return line.strip().split("=", 1)[1]
    except OSError:
        pass
    return "dark"


def save_theme_pref(value):
    try:
        os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
        with open(CONFIG_PATH, "w") as fh:
            fh.write("theme=%s\n" % value)
    except OSError:
        pass


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
        self.toasts.set_child(outer)
        self.set_content(self.toasts)

        if not be.driver_present():
            status = Adw.StatusPage(
                title="Driver not loaded",
                description="The omen-rgb-keyboard kernel module is not "
                            "loaded. Load it and restart this app.",
                icon_name="dialog-error-symbolic")
            outer.append(status)
            return

        body = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        outer.append(body)

        # ---- OGH-style left rail ----
        rail = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        rail.add_css_class("omen-rail")
        rail.set_size_request(170, -1)
        title = Gtk.Label(label="OMEN", xalign=0.5)
        title.add_css_class("omen-rail-title")
        rail.append(title)
        sub = Gtk.Label(label="CONTROL CENTRE", xalign=0.5)
        sub.add_css_class("omen-rail-sub")
        rail.append(sub)
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
            lab = Gtk.Label(label=label)
            lab.add_css_class("caption")
            inner.append(lab)
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

    def _page(self, name):
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        clamp = Adw.Clamp()
        clamp.set_maximum_size(640)
        page = Adw.PreferencesPage()
        scroll.set_child(clamp)
        clamp.set_child(page)
        self.stack.add_named(scroll, name)
        return page

    def _paint_button(self, b, hexs):
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
        self.home_cpu_load = vital_card("CPU USE")
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
        for idx, (box, _) in enumerate(hcards):
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
        bar = getattr(self, "_apply_bar", None)
        if bar is not None:
            bar.set_visible(dirty)

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
        self.speed_spin.set_value(
            be.read_int("rgb_zones/animation_speed") or 5)
        self._loading = False

    def _on_swatch(self, _b, hexcol):
        rel = "rgb_zones/all"
        self._paint_button(self.allzone_btn, hexcol)
        self.allzone_btn._omen_hex = hexcol
        self._stage(rel, hexcol)

    # ---- Lighting ----

    def _build_lighting(self):
        page = self._page("lighting")

        grp = Adw.PreferencesGroup(
            title="Static color",
            description="Click a zone box to change its color. "
                        "Zone-to-side mapping varies by keyboard - use "
                        "Identify to see yours.")
        page.add(grp)

        zbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        zbox.set_homogeneous(True)
        self.zone_btns = []
        self._staged_writes = getattr(self, "_staged_writes", {})
        for i in range(4):
            rel = "rgb_zones/zone0%d" % i
            current = (be.read(rel) or "FFFFFF").upper()
            if len(current) != 6:
                current = "FFFFFF"
            b = Gtk.Button()
            b.add_css_class("zone-box")
            label = Gtk.Label(label="Zone %d" % i)
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
        zrow = Adw.ActionRow()
        zrow.set_child(zbox)
        grp.add(zrow)

        arow = Adw.ActionRow(title="All zones")
        allcur = (be.read("rgb_zones/all") or "FFFFFF").upper()
        if len(allcur) != 6:
            allcur = "FFFFFF"
        ab = Gtk.Button()
        ab.add_css_class("zone-box")
        self._paint_button(ab, allcur)
        ab._omen_hex = allcur
        self.allzone_btn = ab
        ab.set_valign(Gtk.Align.CENTER)
        ab.connect("clicked", self._on_zone_clicked, "rgb_zones/all", ab)
        arow.add_suffix(ab)
        grp.add(arow)

        idrow = Adw.ActionRow(
            title="Identify zones",
            subtitle="Lights zones red, green, blue, white (0 to 3)")
        ibtn = Gtk.Button(label="Identify")
        ibtn.set_valign(Gtk.Align.CENTER)
        ibtn.connect("clicked", self._on_identify)
        idrow.add_suffix(ibtn)
        grp.add(idrow)

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
        grp.add(brow)

        lockrow = Adw.ActionRow(
            title="Lock brightness",
            subtitle="Ignore the desktop dimming the keyboard when idle")
        self.lock_switch = Gtk.Switch()
        self.lock_switch.set_valign(Gtk.Align.CENTER)
        self.lock_switch.set_active(
            be.read("rgb_zones/brightness_lock") == "1")
        self.lock_switch.connect("notify::active", self._on_brightness_lock)
        lockrow.add_suffix(self.lock_switch)
        grp.add(lockrow)

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
                              subtitle="Basic set - zone tiles open a "
                                       "custom picker")
        swrow.add_suffix(sw)
        grp.add(swrow)

        self._apply_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                                  spacing=8)
        self._apply_bar.set_margin_top(6)
        self._apply_bar.set_visible(False)
        restore = Gtk.Button(label="Restore defaults")
        restore.connect("clicked", self._on_restore_defaults)
        discard = Gtk.Button(label="Discard changes")
        discard.connect("clicked", self._on_discard)
        applyb = Gtk.Button(label="Apply")
        applyb.add_css_class("suggested-action")
        applyb.connect("clicked", self._on_apply_lighting)
        self._apply_bar.append(restore)
        sp = Gtk.Box()
        sp.set_hexpand(True)
        self._apply_bar.append(sp)
        self._apply_bar.append(discard)
        self._apply_bar.append(applyb)
        barrow = Adw.ActionRow()
        barrow.set_child(self._apply_bar)
        grp.add(barrow)

        agrp = Adw.PreferencesGroup(title="Animation")
        page.add(agrp)
        flow = Gtk.FlowBox()
        flow.set_max_children_per_line(4)
        flow.set_min_children_per_line(3)
        flow.set_row_spacing(8)
        flow.set_column_spacing(8)
        flow.set_selection_mode(Gtk.SelectionMode.NONE)
        flow.set_homogeneous(True)
        current_mode = be.read("rgb_zones/animation_mode") or "static"
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
        erow = Adw.ActionRow()
        erow.set_child(flow)
        agrp.add(erow)

        srow = Adw.ActionRow(title="Speed", subtitle="1 (slow) to 10 (fast)")
        self.speed_spin = Gtk.SpinButton(adjustment=Gtk.Adjustment(
            value=be.read_int("rgb_zones/animation_speed") or 5,
            lower=1, upper=10, step_increment=1, page_increment=2))
        self.speed_spin.set_valign(Gtk.Align.CENTER)
        self.speed_spin.connect("value-changed", self._on_anim_speed)
        srow.add_suffix(self.speed_spin)
        agrp.add(srow)

        grow = Adw.ActionRow(
            title="Gradient config",
            subtitle='Applies in Gradient mode. Format: '
                     '"0,1,2:FF0000,00FF00;3:800080"')
        self.gradient_entry = Gtk.Entry()
        self.gradient_entry.set_placeholder_text(
            "0,1,2:FF0000,00FF00;3:800080")
        saved = be.read("rgb_zones/gradient_config")
        if saved:
            self.gradient_entry.set_text(saved)
        self.gradient_entry.set_width_chars(26)
        self.gradient_entry.set_valign(Gtk.Align.CENTER)
        gbtn = Gtk.Button(label="Apply")
        gbtn.set_valign(Gtk.Align.CENTER)
        gbtn.connect("clicked", self._on_gradient)
        grow.add_suffix(self.gradient_entry)
        grow.add_suffix(gbtn)
        agrp.add(grow)

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
            cbtn = Gtk.Button(label="Apply")
            cbtn.set_valign(Gtk.Align.CENTER)
            cbtn.connect("clicked", self._on_curve_apply)
            crow2.add_suffix(self.curve_entry)
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
            if back_max == "0" and back_profile == profile:
                self._toast("AUTO restored (%s profile)" % profile)
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

    def _on_curve_reset(self, _b):
        self._apply("fan/thermal_profile", "normal")
        saved = be.read("fan/fan_curve")
        if saved:
            self._loading = True
            self.curve_entry.set_text(saved)
            self._loading = False

    # ---- System Vitals ----

    def _build_vitals(self):
        page = self._page("vitals")

        grp = Adw.PreferencesGroup(title="System Vitals")
        page.add(grp)
        grid = Gtk.Grid()
        grid.set_row_spacing(10)
        grid.set_column_spacing(10)
        grid.set_margin_top(6)
        grid.set_margin_bottom(6)
        self.v_cpu_temp = vital_card("CPU TEMP")
        self.v_gpu_temp = vital_card("GPU TEMP")
        self.v_cpu_load = vital_card("CPU USE")
        self.v_ram = vital_card("RAM")
        self.v_storage = vital_card("STORAGE USED")
        self.v_net = vital_card("NETWORK")
        cards = [self.v_cpu_temp, self.v_gpu_temp, self.v_cpu_load,
                 self.v_ram, self.v_storage, self.v_net]
        for idx, (box, _) in enumerate(cards):
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
                        "Launching opens Steam.")
        page.add(grp)
        games = games_backend.list_games() if games_backend else []
        if not games:
            grp.add(Adw.ActionRow(
                title="No installed Steam games found",
                subtitle="Games you install in Steam appear here"))
        for game in games[:50]:
            row = Adw.ActionRow(title=game["name"],
                                subtitle="AppID %s" % game["appid"])
            btn = Gtk.Button(label="Launch")
            btn.set_valign(Gtk.Align.CENTER)
            btn.connect("clicked", self._on_launch_game, game["appid"])
            row.add_suffix(btn)
            grp.add(row)

    def _on_launch_game(self, _b, appid):
        if games_backend:
            subprocess.Popen(games_backend.launch_command(appid))

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
            description="Hybrid vs discrete GPU display mode is a BIOS "
                        "setting on this model; no runtime switching "
                        "from Linux.")
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
        grp.add(Adw.ActionRow(
            title="Not available from Linux",
            subtitle="Network Booster; overclocking/undervolting (no "
                     "Linux driver path, no BIOS controls on this "
                     "model); GPU MUX switching (BIOS-only here)"))

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

        ct = be.read_cpu_temp_mc()
        ct_s = ("%.0f°C" % (ct / 1000.0)) if ct is not None else "n/a"
        gt = be.read_gpu_temp_mc()
        gt_s = ("%.0f°C" % (gt / 1000.0)) if gt is not None else "n/a"
        load = be.read_cpu_load_pct()
        load_s = ("%.0f%%" % load) if load is not None else "-"
        total, used = be.read_mem()
        ram_s = ("%.0f%%" % (100.0 * used / total)) if total else "n/a"
        st = be.read_storage()
        net = be.read_net_kbps()

        self.home_cpu_temp[1].set_label(ct_s)
        self.home_gpu_temp[1].set_label(gt_s)
        self.home_cpu_load[1].set_label(load_s)
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
        self.v_cpu_load[1].set_label(load_s)
        self.v_ram[1].set_label(ram_s)
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
