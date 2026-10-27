#!/usr/bin/env python3
"""Omen Control Centre - native Linux control app for HP Omen laptops.

Controls the omen-rgb-keyboard kernel driver:
  - 4-zone RGB keyboard lighting, brightness, 12 animations, gradient
  - Fan RPM, max fan, thermal profiles, power limits, custom fan curves
  - System vitals

GUI runs unprivileged; writes are validated and applied by the root
helper `omenctl` through pkexec (admin authentication per polkit policy).
"""
import os
import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sysfs_backend as be

APP_ID = "com.mananbharti.OmenCentre"
ZONE_NAMES = ["Zone 0", "Zone 1", "Zone 2", "Zone 3"]

PRESET_COLORS = [
    "FFFFFF", "FF0000", "00FF00", "0000FF", "FFFF00", "FF00FF",
    "00FFFF", "FF8000", "8000FF", "FF0080", "80FF00", "0080FF",
]


def hex_to_rgba(hexs):
    rgba = Gdk.RGBA()
    rgba.parse("#" + hexs)
    return rgba


def rgba_to_hex(rgba):
    return "%02X%02X%02X" % (
        round(rgba.red * 255), round(rgba.green * 255), round(rgba.blue * 255))


class ColorPicker(Gtk.Popover):
    """Portable color picker: RGB sliders, hex entry, preset swatches."""

    def __init__(self, initial_hex, on_apply):
        super().__init__()
        self._on_apply = on_apply
        self._updating = False
        self._hex = initial_hex.upper()

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.set_margin_top(12)
        box.set_margin_bottom(12)
        box.set_margin_start(12)
        box.set_margin_end(12)
        box.set_size_request(260, -1)
        self.set_child(box)

        self.preview = Gtk.Box()
        self.preview.set_size_request(-1, 36)
        self.preview.add_css_class("card")
        box.append(self.preview)

        self.scales = {}
        for name in ("R", "G", "B"):
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            label = Gtk.Label(label=name, width_chars=1)
            scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 255, 1)
            scale.set_hexpand(True)
            scale.set_draw_value(True)
            scale.connect("value-changed", self._on_scale)
            row.append(label)
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


class OmenWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app)
        self.set_title("Omen Control Centre")
        self.set_default_size(680, 640)
        self._loading = True
        self._last_good = {}

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.toasts = Adw.ToastOverlay()
        self.toasts.set_child(outer)
        self.set_content(self.toasts)

        header = Adw.HeaderBar()
        outer.append(header)

        self.stack = Adw.ViewStack()
        self.stack.set_vexpand(True)
        outer.append(self.stack)

        switcher = Adw.ViewSwitcherBar()
        switcher.set_stack(self.stack)
        switcher.set_reveal(True)
        outer.append(switcher)

        if not be.driver_present():
            status = Adw.StatusPage(
                title="Driver not loaded",
                description="The omen-rgb-keyboard kernel module is not "
                            "loaded. Load it and restart this app.",
                icon_name="dialog-error-symbolic")
            self.stack.add_titled(status, "missing", "Omen")
            return

        self._build_lighting()
        self._build_fans()
        self._build_vitals()
        self._build_about()

        self._loading = False
        start_page = os.environ.get("OMEN_START_PAGE")
        if start_page:
            self.stack.set_visible_child_name(start_page)
        GLib.timeout_add_seconds(2, self._refresh_live)
        GLib.timeout_add(400, self._refresh_live)

    # ---- helpers -------------------------------------------------------

    def _apply(self, rel, value, revert=None):
        if self._loading:
            return True
        try:
            be.set_value(rel, value)
            self._last_good[rel] = value
        except be.BackendError as exc:
            self.toasts.add_toast(Adw.Toast(title="Failed: %s" % exc))
            if revert is not None:
                self._loading = True
                revert()
                self._loading = False
        return False

    def _toast(self, text):
        self.toasts.add_toast(Adw.Toast(title=text))

    def _swatch(self, hexs):
        b = Gtk.Button()
        b.set_size_request(36, 24)
        b.set_valign(Gtk.Align.CENTER)
        provider = Gtk.CssProvider()
        provider.load_from_data(
            ("* { background-color: #%s; border-radius: 6px; "
             "border: 1px solid alpha(black, 0.35); }" % hexs).encode())
        b.get_style_context().add_provider(
            provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        b._omen_hex = hexs
        return b

    def _repaint_swatch(self, b, hexs):
        self._paint_button(b, hexs)
        b._omen_hex = hexs

    def _paint_button(self, b, hexs):
        provider = Gtk.CssProvider()
        provider.load_from_data(
            ("* { background-color: #%s; border-radius: 6px; "
             "border: 1px solid alpha(black, 0.35); }" % hexs).encode())
        b.get_style_context().add_provider(
            provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    # ---- Lighting page -------------------------------------------------

    def _build_lighting(self):
        page = Adw.PreferencesPage()
        self.stack.add_titled(page, "lighting", "Lighting")
        page.set_icon_name("input-keyboard-symbolic")

        grp = Adw.PreferencesGroup(
            title="Zones",
            description="Pick a color per zone or set all at once. "
                        "Zone-to-side mapping varies by keyboard.")
        page.add(grp)

        self.zone_swatches = []
        for i, name in enumerate(ZONE_NAMES + ["All zones"]):
            rel = "rgb_zones/zone0%d" % i if i < 4 else "rgb_zones/all"
            current = be.read(rel) or "FFFFFF"
            current = current.upper() if len(current) == 6 else "FFFFFF"
            row = Adw.ActionRow(title=name)
            sw = self._swatch(current)
            self._paint_button(sw, current)
            sw._omen_hex = current
            sw.connect("clicked", self._on_swatch, rel, sw)
            row.add_suffix(sw)
            grp.add(row)
            self.zone_swatches.append(sw)

        identify = Adw.ActionRow(
            title="Identify zones",
            subtitle="Lights the four zones red, green, blue, white "
                     "so you can see which key area each zone covers")
        ibtn = Gtk.Button(label="Identify")
        ibtn.set_valign(Gtk.Align.CENTER)
        ibtn.connect("clicked", self._on_identify)
        identify.add_suffix(ibtn)
        grp.add(identify)

        bgrp = Adw.PreferencesGroup(title="Brightness")
        page.add(bgrp)
        brow = Adw.ActionRow(title="Keyboard brightness")
        self.brightness_scale = Gtk.Scale.new_with_range(
            Gtk.Orientation.HORIZONTAL, 0, 100, 1)
        self.brightness_scale.set_size_request(240, -1)
        self.brightness_scale.set_draw_value(True)
        self.brightness_scale.set_value(be.read_int("rgb_zones/brightness") or 100)
        self._brightness_timer = None
        self.brightness_scale.connect("value-changed", self._on_brightness)
        brow.add_suffix(self.brightness_scale)
        bgrp.add(brow)

        lockrow = Adw.ActionRow(
            title="Lock brightness",
            subtitle="Ignore the desktop dimming the keyboard when idle")
        self.lock_switch = Gtk.Switch()
        self.lock_switch.set_valign(Gtk.Align.CENTER)
        self.lock_switch.set_active(
            be.read("rgb_zones/brightness_lock") == "1")
        self.lock_switch.connect("notify::active", self._on_brightness_lock)
        lockrow.add_suffix(self.lock_switch)
        bgrp.add(lockrow)

        agrp = Adw.PreferencesGroup(title="Animation")
        page.add(agrp)
        modes = Gtk.StringList()
        for m in be.ANIMATION_MODES:
            modes.append(m)
        self.anim_row = Adw.ComboRow(title="Mode", model=modes)
        current_mode = be.read("rgb_zones/animation_mode") or "static"
        if current_mode in be.ANIMATION_MODES:
            self.anim_row.set_selected(be.ANIMATION_MODES.index(current_mode))
        self.anim_row.connect("notify::selected", self._on_anim_mode)
        agrp.add(self.anim_row)

        speed_row = Adw.ActionRow(title="Speed", subtitle="1 (slow) to 10 (fast)")
        self.speed_spin = Gtk.SpinButton(adjustment=Gtk.Adjustment(
            value=be.read_int("rgb_zones/animation_speed") or 5,
            lower=1, upper=10, step_increment=1, page_increment=2))
        self.speed_spin.set_valign(Gtk.Align.CENTER)
        self.speed_spin.connect("value-changed", self._on_anim_speed)
        speed_row.add_suffix(self.speed_spin)
        agrp.add(speed_row)

        ggrp = Adw.PreferencesGroup(
            title="Gradient animation",
            description='Format: "0,1,2:FF0000,00FF00;3:800080". '
                        "Applies when animation mode is gradient.")
        page.add(ggrp)
        self.gradient_row = Adw.ActionRow(title="Gradient config")
        self.gradient_entry = Gtk.Entry()
        self.gradient_entry.set_placeholder_text("0,1,2:FF0000,00FF00;3:800080")
        saved = be.read("rgb_zones/gradient_config")
        if saved:
            self.gradient_entry.set_text(saved)
        self.gradient_entry.set_width_chars(28)
        self.gradient_entry.set_valign(Gtk.Align.CENTER)
        gbtn = Gtk.Button(label="Apply")
        gbtn.set_valign(Gtk.Align.CENTER)
        gbtn.connect("clicked", self._on_gradient)
        self.gradient_row.add_suffix(self.gradient_entry)
        self.gradient_row.add_suffix(gbtn)
        ggrp.add(self.gradient_row)

    def _on_swatch(self, sw, rel, _sw):
        picker = ColorPicker(sw._omen_hex,
                             lambda h: self._on_color_chosen(rel, h, sw))
        picker.set_parent(sw)
        picker.popup()

    def _on_color_chosen(self, rel, hexs, sw):
        old = sw._omen_hex
        self._repaint_swatch(sw, hexs)
        self._apply(rel, hexs, revert=lambda: self._repaint_swatch(sw, old))

    def _on_identify(self, _b):
        colors = ["FF0000", "00FF00", "0000FF", "FFFFFF"]
        for i, c in enumerate(colors):
            self._apply("rgb_zones/zone0%d" % i, c)
            self._repaint_swatch(self.zone_swatches[i], c)
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
        self._apply("rgb_zones/brightness", str(value))
        return GLib.SOURCE_REMOVE

    def _on_brightness_lock(self, sw, _p):
        self._apply("rgb_zones/brightness_lock", "1" if sw.get_active() else "0",
                    revert=lambda: sw.set_active(not sw.get_active()))

    def _on_anim_mode(self, row, _p):
        mode = be.ANIMATION_MODES[row.get_selected()]
        self._apply("rgb_zones/animation_mode", mode)

    def _on_anim_speed(self, spin):
        self._apply("rgb_zones/animation_speed", str(int(spin.get_value())))

    def _on_gradient(self, _b):
        self._apply("rgb_zones/gradient_config",
                    self.gradient_entry.get_text().strip())

    # ---- Fans page -----------------------------------------------------

    def _build_fans(self):
        page = Adw.PreferencesPage()
        self.stack.add_titled(page, "fans", "Fans")
        page.set_icon_name("power-profile-performance-symbolic")

        grp = Adw.PreferencesGroup(title="Fan speed")
        page.add(grp)
        self.cpu_rpm_row = Adw.ActionRow(title="CPU fan")
        self.cpu_rpm_label = Gtk.Label(label="-")
        self.cpu_rpm_label.set_valign(Gtk.Align.CENTER)
        self.cpu_rpm_row.add_suffix(self.cpu_rpm_label)
        grp.add(self.cpu_rpm_row)
        self.gpu_rpm_row = Adw.ActionRow(title="GPU fan")
        self.gpu_rpm_label = Gtk.Label(label="-")
        self.gpu_rpm_label.set_valign(Gtk.Align.CENTER)
        self.gpu_rpm_row.add_suffix(self.gpu_rpm_label)
        grp.add(self.gpu_rpm_row)

        cgrp = Adw.PreferencesGroup(
            title="Cooling",
            description="Silent / Normal / Performance match the system "
                        "power menu. Max fan runs both fans at full speed.")
        page.add(cgrp)
        maxrow = Adw.ActionRow(title="Max fan")
        self.max_switch = Gtk.Switch()
        self.max_switch.set_valign(Gtk.Align.CENTER)
        self.max_switch.set_active(be.read("fan/max_fan") == "1")
        self.max_switch.connect("notify::active", self._on_max_fan)
        maxrow.add_suffix(self.max_switch)
        cgrp.add(maxrow)

        profiles = Gtk.StringList()
        for p in ("Silent", "Normal", "Performance"):
            profiles.append(p)
        self.profile_row = Adw.ComboRow(title="Thermal profile", model=profiles)
        current = (be.read("fan/thermal_profile") or "normal").lower()
        if current in be.THERMAL_PROFILES:
            self.profile_row.set_selected(be.THERMAL_PROFILES.index(current))
        self.profile_row.connect("notify::selected", self._on_profile)
        cgrp.add(self.profile_row)

        pgrp = Adw.PreferencesGroup(
            title="CPU power limits",
            description="PL1 (sustained) and PL2 (turbo) in watts. "
                        "Intel reference for i7-14650HX: 55 W base, "
                        "157 W max turbo. 'Restore defaults' hands "
                        "control back to the BIOS.")
        page.add(pgrp)
        pl = be.read("fan/power_limits")
        pl1, pl2 = 55, 157
        if pl and pl not in ("default", "0 0"):
            parts = pl.split()
            if len(parts) == 2 and all(p.isdigit() for p in parts):
                pl1, pl2 = int(parts[0]), int(parts[1])
        pl1_row = Adw.ActionRow(
            title="PL1 (sustained)", subtitle="watts")
        self.pl1_spin = Gtk.SpinButton(adjustment=Gtk.Adjustment(
            value=pl1, lower=15, upper=200, step_increment=5,
            page_increment=10))
        self.pl1_spin.set_valign(Gtk.Align.CENTER)
        pl1_row.add_suffix(self.pl1_spin)
        pgrp.add(pl1_row)
        pl2_row = Adw.ActionRow(
            title="PL2 (turbo)", subtitle="watts, must be >= PL1")
        self.pl2_spin = Gtk.SpinButton(adjustment=Gtk.Adjustment(
            value=pl2, lower=15, upper=200, step_increment=5,
            page_increment=10))
        self.pl2_spin.set_valign(Gtk.Align.CENTER)
        pl2_row.add_suffix(self.pl2_spin)
        pgrp.add(pl2_row)
        btnrow = Adw.ActionRow()
        applyb = Gtk.Button(label="Apply limits")
        applyb.add_css_class("suggested-action")
        applyb.set_valign(Gtk.Align.CENTER)
        applyb.connect("clicked", self._on_power_limits)
        defb = Gtk.Button(label="Restore defaults")
        defb.set_valign(Gtk.Align.CENTER)
        defb.connect("clicked", self._on_power_defaults)
        btnrow.add_suffix(defb)
        btnrow.add_suffix(applyb)
        pgrp.add(btnrow)

        fgrp = Adw.PreferencesGroup(title="Custom fan curve")
        page.add(fgrp)
        if be.fan_curve_supported():
            fgrp.set_description(
                'Points as "tempC:percent", 2 to 8 points, e.g. '
                '"30:20 40:30 55:45 70:65 85:90". Choosing a thermal '
                "profile replaces the custom curve with the preset.")
            self.curve_enable = Gtk.Switch()
            self.curve_enable.set_valign(Gtk.Align.CENTER)
            self.curve_enable.set_active(
                be.read("fan/fan_curve_enable") == "1")
            self.curve_enable.connect("notify::active", self._on_curve_enable)
            cerow = Adw.ActionRow(title="Custom curve enabled")
            cerow.add_suffix(self.curve_enable)
            fgrp.add(cerow)
            crow = Adw.ActionRow(title="Curve points")
            self.curve_entry = Gtk.Entry()
            saved = be.read("fan/fan_curve")
            if saved:
                self.curve_entry.set_text(saved)
            self.curve_entry.set_width_chars(32)
            self.curve_entry.set_valign(Gtk.Align.CENTER)
            cbtn = Gtk.Button(label="Apply")
            cbtn.set_valign(Gtk.Align.CENTER)
            cbtn.connect("clicked", self._on_curve_apply)
            crow.add_suffix(self.curve_entry)
            crow.add_suffix(cbtn)
            fgrp.add(crow)
        else:
            fgrp.set_description(
                "This model's fan interface does not support custom "
                "curves. Automatic profiles and Max fan still work.")
            row = Adw.ActionRow(title="Not supported on this model")
            row.add_suffix(Gtk.Image.new_from_icon_name(
                "dialog-information-symbolic"))
            fgrp.add(row)

    def _on_max_fan(self, sw, _p):
        self._apply("fan/max_fan", "1" if sw.get_active() else "0",
                    revert=lambda: sw.set_active(not sw.get_active()))

    def _on_profile(self, row, _p):
        profile = be.THERMAL_PROFILES[row.get_selected()]
        self._apply("fan/thermal_profile", profile)

    def _on_power_limits(self, _b):
        pl1 = int(self.pl1_spin.get_value())
        pl2 = int(self.pl2_spin.get_value())
        if pl2 < pl1:
            self._toast("PL2 must be greater than or equal to PL1")
            return
        self._apply("fan/power_limits", "%d %d" % (pl1, pl2))

    def _on_power_defaults(self, _b):
        self._apply("fan/power_limits", "default")

    def _on_curve_enable(self, sw, _p):
        self._apply("fan/fan_curve_enable", "1" if sw.get_active() else "0",
                    revert=lambda: sw.set_active(not sw.get_active()))

    def _on_curve_apply(self, _b):
        self._apply("fan/fan_curve", self.curve_entry.get_text().strip())

    # ---- Vitals page ---------------------------------------------------

    def _build_vitals(self):
        page = Adw.PreferencesPage()
        self.stack.add_titled(page, "vitals", "Vitals")
        page.set_icon_name("applications-system-symbolic")

        grp = Adw.PreferencesGroup(title="Temperatures")
        page.add(grp)
        self.cpu_temp_row = Adw.ActionRow(title="CPU package")
        self.cpu_temp_label = Gtk.Label(label="-")
        self.cpu_temp_label.set_valign(Gtk.Align.CENTER)
        self.cpu_temp_row.add_suffix(self.cpu_temp_label)
        grp.add(self.cpu_temp_row)
        self.gpu_temp_row = Adw.ActionRow(title="GPU")
        self.gpu_temp_label = Gtk.Label(label="-")
        self.gpu_temp_label.set_valign(Gtk.Align.CENTER)
        self.gpu_temp_row.add_suffix(self.gpu_temp_label)
        grp.add(self.gpu_temp_row)

        ugrp = Adw.PreferencesGroup(title="Load")
        page.add(ugrp)
        self.cpu_load_row = Adw.ActionRow(title="CPU usage")
        self.cpu_load_bar = Gtk.ProgressBar()
        self.cpu_load_bar.set_size_request(220, -1)
        self.cpu_load_bar.set_valign(Gtk.Align.CENTER)
        self.cpu_load_row.add_suffix(self.cpu_load_bar)
        ugrp.add(self.cpu_load_row)
        self.mem_row = Adw.ActionRow(title="Memory")
        self.mem_bar = Gtk.ProgressBar()
        self.mem_bar.set_size_request(220, -1)
        self.mem_bar.set_valign(Gtk.Align.CENTER)
        self.mem_row.add_suffix(self.mem_bar)
        ugrp.add(self.mem_row)

    # ---- About page ----------------------------------------------------

    def _build_about(self):
        page = Adw.PreferencesPage()
        self.stack.add_titled(page, "about", "About")
        page.set_icon_name("help-about-symbolic")

        grp = Adw.PreferencesGroup(title="Omen Control Centre")
        page.add(grp)
        grp.add(Adw.ActionRow(
            title="Driver", subtitle="omen-rgb-keyboard (OmenLinux), "
            "controls RGB lighting, fans and thermal profiles"))
        grp.add(Adw.ActionRow(
            title="Changes are saved",
            subtitle="The driver stores your lighting and fan settings "
                     "and restores them at boot"))

        vgrp = Adw.PreferencesGroup(
            title="Not available from Linux on this machine",
            description="Omen Gaming Hub features with no Linux path")
        page.add(vgrp)
        for t, s in [
            ("Network Booster", "Windows-only network prioritization"),
            ("Overclocking / undervolting",
             "Locked on this HX CPU; no driver path from Linux"),
            ("GPU display mode (MUX) switching",
             "Controlled by BIOS on this model"),
        ]:
            vgrp.add(Adw.ActionRow(title=t, subtitle=s))

        fgrp = Adw.PreferencesGroup(
            title="Not yet implemented in this app")
        page.add(fgrp)
        for t, s in [
            ("Game library", "Could list installed games in a future release"),
            ("Omen key shortcut",
             "Bindable today in Settings > Keyboard > Custom Shortcuts; "
             "in-app binding may come later"),
        ]:
            fgrp.add(Adw.ActionRow(title=t, subtitle=s))

    # ---- live refresh --------------------------------------------------

    def _refresh_live(self):
        cpu, gpu = be.read_rpm()
        self.cpu_rpm_label.set_label(
            ("%d RPM" % cpu) if cpu is not None and cpu >= 0 else "n/a")
        self.gpu_rpm_label.set_label(
            ("%d RPM" % gpu) if gpu is not None and gpu >= 0 else "n/a")

        ct = be.read_cpu_temp_mc()
        self.cpu_temp_label.set_label(
            ("%.0f °C" % (ct / 1000.0)) if ct is not None else "n/a")
        gt = be.read_gpu_temp_mc()
        self.gpu_temp_label.set_label(
            ("%.0f °C" % (gt / 1000.0)) if gt is not None else "not exposed")

        load = be.read_cpu_load_pct()
        if load is not None:
            self.cpu_load_bar.set_fraction(load / 100.0)
            self.cpu_load_bar.set_text("%.0f%%" % load)
        total, used = be.read_mem()
        if total:
            frac = used / float(total)
            self.mem_bar.set_fraction(frac)
            self.mem_bar.set_text("%.1f / %.1f GB" % (
                used / 1048576.0, total / 1048576.0))
        return GLib.SOURCE_CONTINUE


class OmenApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID,
                         flags=Gio.ApplicationFlags.FLAGS_NONE)
        if os.environ.get("OMEN_FORCE_DARK") == "1":
            self.props.style_manager.set_color_scheme(
                Adw.ColorScheme.FORCE_DARK)

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
