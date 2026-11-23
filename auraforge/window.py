"""Main application window."""

from __future__ import annotations

import time
from typing import Callable, Optional

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk, Pango  # noqa: E402

from . import APP_NAME, autostart, backend  # noqa: E402
from .backend import CommandResult, Job  # noqa: E402
from .modes import (BRIGHTNESS_LABELS, MODES, MODES_BY_COMMAND, SPEED_LABELS,  # noqa: E402
                    ZONE_LABELS, LightingState)
from .preview import ColorSwatch, KeyboardPreview  # noqa: E402
from .storage import ProfileStore, Settings, StateStore  # noqa: E402

LIVE_APPLY_DELAY_MS = 300
MAX_LOG = 50


# --------------------------------------------------------------------------
#  Small widgets
# --------------------------------------------------------------------------

class SegmentedControl(Gtk.Box):
    """Linked toggle buttons behaving like a segmented selector."""

    __gsignals__ = {"changed": (GObject.SignalFlags.RUN_FIRST, None, (int,))}

    def __init__(self, options: dict[int, str]):
        super().__init__(valign=Gtk.Align.CENTER)
        self.add_css_class("linked")
        self._buttons: dict[int, Gtk.ToggleButton] = {}
        self._value: Optional[int] = None
        self._silent = False
        group = None
        for value, label in options.items():
            btn = Gtk.ToggleButton(label=label)
            if group is None:
                group = btn
            else:
                btn.set_group(group)
            btn.connect("toggled", self._on_toggled, value)
            self._buttons[value] = btn
            self.append(btn)

    def _on_toggled(self, btn: Gtk.ToggleButton, value: int) -> None:
        if not btn.get_active() or value == self._value:
            return
        self._value = value
        if not self._silent:
            self.emit("changed", value)

    def get_value(self) -> Optional[int]:
        return self._value

    def set_value(self, value: int) -> None:
        """Select *value* without emitting `changed`."""
        self._silent = True
        try:
            self._value = value
            self._buttons[value].set_active(True)
        finally:
            self._silent = False


def rgba_to_hex(rgba: Gdk.RGBA) -> str:
    return "".join(f"{max(0, min(255, round(c * 255))):02x}" for c in (rgba.red, rgba.green, rgba.blue))


def hex_to_rgba(value: str) -> Gdk.RGBA:
    rgba = Gdk.RGBA()
    rgba.parse(f"#{value}")
    return rgba


def make_color_button(title: str) -> Gtk.ColorDialogButton:
    dialog = Gtk.ColorDialog(title=title, with_alpha=False, modal=True)
    btn = Gtk.ColorDialogButton(dialog=dialog, valign=Gtk.Align.CENTER)
    btn.set_tooltip_text(title)
    return btn


# --------------------------------------------------------------------------
#  Window
# --------------------------------------------------------------------------

class MainWindow(Adw.ApplicationWindow):
    def __init__(self, app: Adw.Application):
        super().__init__(application=app, title=APP_NAME)
        self.set_default_size(660, 860)
        self.set_size_request(360, 480)

        self.profiles = ProfileStore()
        self.state_store = StateStore()
        self.settings = Settings()
        self.state: LightingState = self.state_store.state.copy()

        self._syncing = False
        self._live_source = 0
        self._last_error_kind: Optional[str] = None
        self._log: list[CommandResult] = []
        self._profile_rows: list[Gtk.Widget] = []

        self.runner = backend.CommandRunner(
            dispatch=GLib.idle_add, on_result=self._on_command_result, on_busy=self._on_busy)

        self._install_actions()
        self._build_ui()
        self._sync_ui_from_state()
        self._rebuild_profiles()
        GLib.idle_add(self._refresh_diagnostics)

    # ------------------------------------------------------------------ UI --
    def _build_ui(self) -> None:
        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(self._build_header())
        toolbar.add_bottom_bar(self._build_status_bar())

        self.toasts = Adw.ToastOverlay()
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.banner = Adw.Banner(button_label="Details")
        self.banner.connect("button-clicked", lambda *_: self._show_diagnostics())
        content.append(self.banner)

        self.page = Adw.PreferencesPage()
        self.page.set_vexpand(True)
        self.page.add(self._build_preview_group())
        self.page.add(self._build_lighting_group())
        self.page.add(self._build_profiles_group())
        self.page.add(self._build_behaviour_group())
        self.page.add(self._build_diagnostics_group())
        content.append(self.page)

        self.toasts.set_child(content)
        toolbar.set_content(self.toasts)
        self.set_content(toolbar)

    def _build_header(self) -> Adw.HeaderBar:
        header = Adw.HeaderBar()
        self.window_title = Adw.WindowTitle(title=APP_NAME)
        header.set_title_widget(self.window_title)

        init_btn = Gtk.Button(child=Adw.ButtonContent(icon_name="view-refresh-symbolic",
                                                      label="Initialize"))
        init_btn.set_tooltip_text("Initialize / wake keyboard\n(rogauracore initialize_keyboard)")
        init_btn.set_action_name("win.initialize")
        header.pack_start(init_btn)

        menu = Gio.Menu()
        section = Gio.Menu()
        section.append("Save Profile…", "win.save-profile")
        section.append("Re-check Hardware", "win.refresh-diagnostics")
        menu.append_section(None, section)
        section = Gio.Menu()
        section.append(f"About {APP_NAME}", "app.about")
        menu.append_section(None, section)
        menu_btn = Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu,
                                  primary=True, tooltip_text="Main Menu")
        header.pack_end(menu_btn)

        power_box = Gtk.Box(spacing=6, valign=Gtk.Align.CENTER, margin_end=6)
        power_box.append(Gtk.Image(icon_name="system-shutdown-symbolic"))
        self.power_switch = Gtk.Switch(valign=Gtk.Align.CENTER)
        self.power_switch.set_tooltip_text("Master power (brightness 0 / restore)")
        self.power_switch.update_property([Gtk.AccessibleProperty.LABEL], ["Keyboard lighting power"])
        self.power_switch.connect("notify::active", self._on_power_toggled)
        power_box.append(self.power_switch)
        header.pack_end(power_box)
        return header

    def _build_status_bar(self) -> Gtk.Widget:
        bar = Gtk.Box(spacing=8, margin_start=12, margin_end=12, margin_top=6, margin_bottom=6)
        self.status_spinner = Gtk.Spinner(visible=False)
        self.status_icon = Gtk.Image(icon_name="utilities-terminal-symbolic")
        self.status_label = Gtk.Label(label="Ready", xalign=0, hexpand=True,
                                      ellipsize=Pango.EllipsizeMode.END, selectable=True)
        self.status_label.add_css_class("monospace")
        self.status_label.add_css_class("caption")
        self.status_exit = Gtk.Label()
        self.status_exit.add_css_class("caption")
        self.status_exit.add_css_class("dim-label")
        for w in (self.status_spinner, self.status_icon, self.status_label, self.status_exit):
            bar.append(w)
        click = Gtk.GestureClick()
        click.connect("released", lambda *_: self._show_diagnostics())
        self.status_icon.add_controller(click)
        return bar

    def _build_preview_group(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup()
        frame = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        frame.add_css_class("card")
        frame.add_css_class("aura-preview")
        self.preview = KeyboardPreview()
        self.preview.set_margin_top(6)
        self.preview.set_margin_start(6)
        self.preview.set_margin_end(6)
        frame.append(self.preview)
        self.preview_caption = Gtk.Label(margin_bottom=10, wrap=True)
        self.preview_caption.add_css_class("dim-label")
        self.preview_caption.add_css_class("caption")
        frame.append(self.preview_caption)
        group.add(frame)
        return group

    def _build_lighting_group(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(title="Lighting")
        apply_btn = Gtk.Button(label="Apply", valign=Gtk.Align.CENTER, action_name="win.apply")
        apply_btn.add_css_class("suggested-action")
        apply_btn.add_css_class("pill")
        apply_btn.set_tooltip_text("Send the current settings to the keyboard (Ctrl+Enter)")
        group.set_header_suffix(apply_btn)

        self.mode_row = Adw.ComboRow(title="Effect",
                                     model=Gtk.StringList.new([m.label for m in MODES]))
        self.mode_row.connect("notify::selected", self._on_mode_changed)
        group.add(self.mode_row)

        self.color_row = Adw.ActionRow(title="Color")
        self.color_btn = make_color_button("Color")
        self.color_btn.connect("notify::rgba", self._on_color_changed, "color", None)
        self.color_row.add_suffix(self.color_btn)
        self.color_row.set_activatable_widget(self.color_btn)
        group.add(self.color_row)

        self.color2_row = Adw.ActionRow(title="Second Color",
                                        subtitle="Breathing alternates between both colors")
        self.color2_btn = make_color_button("Second Color")
        self.color2_btn.connect("notify::rgba", self._on_color_changed, "color2", None)
        self.color2_row.add_suffix(self.color2_btn)
        self.color2_row.set_activatable_widget(self.color2_btn)
        group.add(self.color2_row)

        self.zone_rows: list[Adw.ActionRow] = []
        self.zone_btns: list[Gtk.ColorDialogButton] = []
        for i, label in enumerate(ZONE_LABELS):
            row = Adw.ActionRow(title=label)
            if i == 0:
                row.set_subtitle("Per-zone colors need a 4-zone RGB keyboard")
            btn = make_color_button(label)
            btn.connect("notify::rgba", self._on_color_changed, "zones", i)
            row.add_suffix(btn)
            row.set_activatable_widget(btn)
            group.add(row)
            self.zone_rows.append(row)
            self.zone_btns.append(btn)

        self.speed_row = Adw.ActionRow(title="Speed")
        self.speed_ctl = SegmentedControl(SPEED_LABELS)
        self.speed_ctl.connect("changed", self._on_speed_changed)
        self.speed_row.add_suffix(self.speed_ctl)
        group.add(self.speed_row)

        self.brightness_row = Adw.ActionRow(title="Brightness",
                                            subtitle="Applied immediately")
        self.brightness_ctl = SegmentedControl(BRIGHTNESS_LABELS)
        self.brightness_ctl.connect("changed", self._on_brightness_changed)
        self.brightness_row.add_suffix(self.brightness_ctl)
        group.add(self.brightness_row)
        return group

    def _build_profiles_group(self) -> Adw.PreferencesGroup:
        self.profiles_group = Adw.PreferencesGroup(
            title="Profiles", description="Click a profile to apply it")
        save_btn = Gtk.Button(child=Adw.ButtonContent(icon_name="list-add-symbolic", label="Save Current"),
                              valign=Gtk.Align.CENTER, action_name="win.save-profile")
        save_btn.add_css_class("flat")
        self.profiles_group.set_header_suffix(save_btn)
        return self.profiles_group

    def _build_behaviour_group(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(title="Behavior")
        self.autostart_row = Adw.SwitchRow(
            title="Restore last lighting profile on login",
            subtitle="Adds an autostart entry that re-applies the last state")
        self.autostart_row.set_active(autostart.is_enabled())
        self.autostart_row.connect("notify::active", self._on_autostart_toggled)
        group.add(self.autostart_row)

        self.live_row = Adw.SwitchRow(title="Apply changes instantly",
                                      subtitle="Otherwise press Apply to send changes")
        self.live_row.set_active(self.settings["live_apply"])
        self.live_row.connect("notify::active", self._on_live_toggled)
        group.add(self.live_row)
        return group

    def _build_diagnostics_group(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(title="Diagnostics")
        self.diag_group = group

        fix_row = Adw.ActionRow(
            title="Fix Brightness Keys",
            subtitle="Fn brightness keys out of sync? Restarts UPower "
                     "(pkexec systemctl restart upower.service)")
        fix_btn = Gtk.Button(label="Fix", valign=Gtk.Align.CENTER, action_name="win.fix-brightness-keys")
        fix_row.add_suffix(fix_btn)
        fix_row.set_activatable_widget(fix_btn)
        group.add(fix_row)

        self.diag_expander = Adw.ExpanderRow(title="Troubleshooting")
        group.add(self.diag_expander)

        def info_row(title: str, mono: bool = False) -> Adw.ActionRow:
            row = Adw.ActionRow(title=title, subtitle_selectable=True, use_markup=False)
            row.add_css_class("property")
            if mono:
                row.add_css_class("monospace")
            self.diag_expander.add_row(row)
            return row

        self.diag_binary = info_row("rogauracore binary", mono=True)
        self.diag_device = info_row("Keyboard device")
        self.diag_access = info_row("USB access")
        self.diag_udev = info_row("udev rule", mono=True)
        self.diag_conflict = info_row("Conflicting services")
        self.diag_cmd = info_row("Last command", mono=True)
        copy_btn = Gtk.Button(icon_name="edit-copy-symbolic", valign=Gtk.Align.CENTER,
                              tooltip_text="Copy command")
        copy_btn.add_css_class("flat")
        copy_btn.connect("clicked", self._copy_last_command)
        self.diag_cmd.add_suffix(copy_btn)
        self.diag_exit = info_row("Exit status")
        self.diag_output = info_row("Output", mono=True)

        log_row = Adw.ActionRow(title="Command log")
        log_row.add_css_class("property")
        self.diag_expander.add_row(log_row)
        self.log_label = Gtk.Label(xalign=0, selectable=True, wrap=True, margin_top=6,
                                   margin_bottom=12, margin_start=12, margin_end=12)
        self.log_label.add_css_class("monospace")
        self.log_label.add_css_class("caption")
        self.diag_expander.add_row(self.log_label)

        recheck = Gtk.Button(label="Re-check Hardware", halign=Gtk.Align.CENTER, margin_top=6,
                             margin_bottom=6, action_name="win.refresh-diagnostics")
        recheck.add_css_class("flat")
        self.diag_expander.add_row(recheck)
        return group

    # ------------------------------------------------------------- actions --
    def _install_actions(self) -> None:
        def add(name: str, cb: Callable, param: Optional[GLib.VariantType] = None):
            action = Gio.SimpleAction.new(name, param)
            action.connect("activate", cb)
            self.add_action(action)

        add("apply", lambda *_: self.apply_state(full=True))
        add("initialize", lambda *_: self._initialize_keyboard())
        add("save-profile", lambda *_: self._prompt_save_profile())
        add("fix-brightness-keys", lambda *_: self._fix_brightness_keys())
        add("refresh-diagnostics", lambda *_: self._refresh_diagnostics(announce=True))
        s = GLib.VariantType.new("s")
        add("profile-apply", lambda _a, v: self._apply_profile(v.get_string()), s)
        add("profile-update", lambda _a, v: self._update_profile(v.get_string()), s)
        add("profile-rename", lambda _a, v: self._prompt_rename_profile(v.get_string()), s)
        add("profile-delete", lambda _a, v: self._confirm_delete_profile(v.get_string()), s)

    # ------------------------------------------------------- state <-> UI --
    def _sync_ui_from_state(self) -> None:
        self._syncing = True
        try:
            s = self.state
            self.mode_row.set_selected(next(i for i, m in enumerate(MODES) if m.command == s.mode))
            self.color_btn.set_rgba(hex_to_rgba(s.color))
            self.color2_btn.set_rgba(hex_to_rgba(s.color2))
            for btn, z in zip(self.zone_btns, s.zones):
                btn.set_rgba(hex_to_rgba(z))
            self.speed_ctl.set_value(s.speed)
            self.brightness_ctl.set_value(s.brightness)
            self.power_switch.set_active(s.brightness > 0)
        finally:
            self._syncing = False
        self._update_visibility()
        self._update_preview()

    def _update_visibility(self) -> None:
        info = self.state.info
        self.color_row.set_visible(info.colors in ("single", "dual"))
        self.color_row.set_title("First Color" if info.colors == "dual" else "Color")
        self.color2_row.set_visible(info.colors == "dual")
        for row in self.zone_rows:
            row.set_visible(info.colors == "zones")
        self.speed_row.set_sensitive(info.speed)
        self.speed_row.set_subtitle("" if info.speed else "Only for animated effects")

    def _update_preview(self) -> None:
        powered = self.state.brightness > 0
        self.preview.set_state(self.state, powered)
        self.preview_caption.set_label(self.state.describe() if powered else "Lighting is off")

    # ------------------------------------------------------------ handlers --
    def _on_mode_changed(self, row: Adw.ComboRow, _pspec) -> None:
        idx = row.get_selected()
        if self._syncing or idx == Gtk.INVALID_LIST_POSITION:
            return
        self.state.mode = MODES[idx].command
        self._update_visibility()
        self._state_edited()

    def _on_color_changed(self, btn: Gtk.ColorDialogButton, _pspec, attr: str, index) -> None:
        if self._syncing:
            return
        value = rgba_to_hex(btn.get_rgba())
        if attr == "zones":
            self.state.zones[index] = value
        else:
            setattr(self.state, attr, value)
        self._state_edited()

    def _on_speed_changed(self, _ctl, value: int) -> None:
        self.state.speed = value
        self._state_edited()

    def _on_brightness_changed(self, _ctl, value: int) -> None:
        self._set_brightness(value)

    def _on_power_toggled(self, switch: Gtk.Switch, _pspec) -> None:
        if self._syncing:
            return
        if switch.get_active():
            self._set_brightness(self.state_store.restore_brightness or 3)
        else:
            self._set_brightness(0)

    def _set_brightness(self, value: int) -> None:
        if value > 0:
            self.state_store.restore_brightness = value
        self.state.brightness = value
        self._syncing = True
        try:
            self.brightness_ctl.set_value(value)
            self.power_switch.set_active(value > 0)
        finally:
            self._syncing = False
        self._update_preview()
        snapshot = self.state.copy()
        self.runner.submit(Job([snapshot.brightness_args()], key="brightness",
                               callback=lambda res: self._after_apply(res, snapshot,
                                                                      brightness_only=True)))

    def _on_live_toggled(self, row: Adw.SwitchRow, _pspec) -> None:
        self.settings["live_apply"] = row.get_active()

    def _on_autostart_toggled(self, row: Adw.SwitchRow, _pspec) -> None:
        try:
            autostart.set_enabled(row.get_active())
        except OSError as exc:
            self._toast(f"Could not update autostart entry: {exc}")
            return
        if row.get_active() and not self.state_store.has_saved_state:
            self.state_store.state = self.state.copy()
            self.state_store.save()
        self._toast("Lighting will be restored at login" if row.get_active()
                    else "Login restore disabled")

    def _state_edited(self) -> None:
        self._update_preview()
        if not self.live_row.get_active():
            return
        if self._live_source:
            GLib.source_remove(self._live_source)
        self._live_source = GLib.timeout_add(LIVE_APPLY_DELAY_MS, self._live_apply_cb)

    def _live_apply_cb(self) -> bool:
        self._live_source = 0
        self.apply_state(full=False)
        return GLib.SOURCE_REMOVE

    # ------------------------------------------------------------ commands --
    def apply_state(self, full: bool) -> None:
        """Send the mode (and, if *full*, the brightness) to the keyboard."""
        snapshot = self.state.copy()
        steps = [snapshot.mode_args()]
        if full:
            steps.append(snapshot.brightness_args())
        self.runner.submit(Job(steps, key="apply",
                               callback=lambda res: self._after_apply(res, snapshot, announce=full)))

    def _after_apply(self, results: list[CommandResult], snapshot: LightingState,
                     announce: bool = False, brightness_only: bool = False) -> bool:
        """Persist the last state that actually reached the hardware."""
        ok = bool(results) and all(r.ok for r in results)
        if ok:
            saved = self.state_store.state
            if not brightness_only:
                saved.mode, saved.color, saved.color2 = snapshot.mode, snapshot.color, snapshot.color2
                saved.zones, saved.speed = list(snapshot.zones), snapshot.speed
            saved.brightness = snapshot.brightness
            try:
                self.state_store.save()
            except OSError as exc:
                self._toast(f"Could not save state: {exc}")
            if announce:
                self._toast("Lighting applied")
        return ok

    def _initialize_keyboard(self) -> None:
        snapshot = self.state.copy()
        steps = [["initialize_keyboard"], snapshot.mode_args(), snapshot.brightness_args()]

        def done(results: list[CommandResult]) -> None:
            if self._after_apply(results, snapshot):
                self._toast("Keyboard initialized")

        self.runner.submit(Job(steps, key="apply", callback=done))

    def _fix_brightness_keys(self) -> None:
        def done(results: list[CommandResult]) -> None:
            res = results[0]
            if res.ok:
                self._toast("UPower restarted - brightness keys should work again")
            elif res.kind == "cancelled":
                self._toast("Authentication cancelled")
        self.runner.submit(Job([backend.FIX_BRIGHTNESS_KEYS_ARGV], raw=True,
                               timeout=backend.POLKIT_TIMEOUT, callback=done))
        self._toast("Requesting administrator permission…")

    # ---------------------------------------------------- command feedback --
    def _on_busy(self, busy: bool) -> None:
        self.status_spinner.set_visible(busy)
        self.status_spinner.set_spinning(busy)
        self.status_icon.set_visible(not busy)

    def _on_command_result(self, res: CommandResult) -> None:
        self._log.append(res)
        del self._log[:-MAX_LOG]
        self.status_label.set_label(f"$ {res.display_line}")
        self.status_label.set_tooltip_text(res.command_line)
        if res.ok:
            self.status_icon.set_from_icon_name("object-select-symbolic")
            self.status_exit.set_label(f"OK (exit {res.returncode}) · {res.duration * 1000:.0f} ms")
        else:
            self.status_icon.set_from_icon_name("dialog-warning-symbolic")
            code = res.returncode if res.returncode is not None else "-"
            self.status_exit.set_label(f"exit {code}")
        self.status_icon.set_tooltip_text(res.message)

        self.diag_cmd.set_subtitle(res.command_line)
        code = "n/a" if res.returncode is None else str(res.returncode)
        self.diag_exit.set_subtitle(f"{code} - {res.message}")
        self.diag_output.set_subtitle(res.output or "(no output)")
        self.log_label.set_label("\n".join(
            f"{time.strftime('%H:%M:%S', time.localtime(r.timestamp))}  "
            f"{'✓' if r.ok else '✗'}  {r.display_line}" for r in reversed(self._log[-12:])))

        if res.ok:
            if res.argv[:1] != ["pkexec"]:
                self.banner.set_revealed(False)  # the keyboard just accepted a command
            if self._last_error_kind in ("missing", "permission", "no_device", "busy"):
                GLib.idle_add(self._refresh_diagnostics)
            self._last_error_kind = None
            return

        if res.kind != self._last_error_kind or res.kind not in ("permission", "no_device", "missing"):
            self._toast(res.message, timeout=5)
        self._last_error_kind = res.kind
        if res.kind in ("missing", "permission", "no_device", "busy"):
            self.banner.set_title(GLib.markup_escape_text(res.message))
            self.banner.set_revealed(True)
            GLib.idle_add(self._refresh_diagnostics)

    def _refresh_diagnostics(self, announce: bool = False) -> bool:
        binary = backend.find_rogauracore()
        self.diag_binary.set_subtitle(binary or "Not installed - run install.sh")

        devices = backend.detect_asus_devices()
        supported = [d for d in devices if d.supported]
        dev = supported[0] if supported else None
        if dev:
            self.diag_device.set_subtitle(f"{dev.name} ({dev.usb_id}) at {dev.node}")
            self.window_title.set_subtitle(f"{dev.name} · {dev.usb_id}")
            self.diag_access.set_subtitle(
                "Read/write OK - no sudo needed" if dev.writable
                else "No write access - install the udev rule (install.sh), then replug/reboot")
        else:
            others = ", ".join(d.usb_id for d in devices) or "none"
            self.diag_device.set_subtitle(f"No supported Aura keyboard found (ASUS USB IDs: {others})")
            self.window_title.set_subtitle("Keyboard not detected")
            self.diag_access.set_subtitle("n/a")

        self.diag_udev.set_subtitle(f"{backend.UDEV_RULE_PATH} "
                                    f"({'present' if backend.UDEV_RULE_PATH.exists() else 'missing'})")
        conflicts = backend.conflicting_services()
        self.diag_conflict.set_subtitle(
            ", ".join(conflicts) + " is running and may override rogauracore" if conflicts else "None")

        problem = None
        if binary is None:
            problem = "rogauracore is not installed. Run install.sh to build it."
        elif dev is None:
            problem = "No supported ROG Aura keyboard detected."
        elif not dev.writable:
            problem = "No permission to access the keyboard. Install the udev rule via install.sh."
        if problem:
            self.banner.set_title(GLib.markup_escape_text(problem))
            self.banner.set_revealed(True)
            self.diag_expander.set_subtitle("Problems detected")
        else:
            self.diag_expander.set_subtitle("Everything looks good")
            if self._last_error_kind is None:
                self.banner.set_revealed(False)
        if announce:
            self._toast(problem or "Hardware check passed")
        return GLib.SOURCE_REMOVE

    def _show_diagnostics(self) -> None:
        self.diag_expander.set_expanded(True)
        GLib.idle_add(lambda: (self.diag_expander.grab_focus(), GLib.SOURCE_REMOVE)[1])

    def _copy_last_command(self, *_):
        if self._log:
            self.get_clipboard().set(self._log[-1].command_line)
            self._toast("Command copied")

    def _toast(self, text: str, timeout: int = 3) -> None:
        toast = Adw.Toast(title=GLib.markup_escape_text(text), timeout=timeout)
        self.toasts.add_toast(toast)

    # ------------------------------------------------------------ profiles --
    def _rebuild_profiles(self) -> None:
        for row in self._profile_rows:
            self.profiles_group.remove(row)
        self._profile_rows.clear()

        if not self.profiles.profiles:
            row = Adw.ActionRow(title="No saved profiles yet",
                                subtitle="Use “Save Current” to store your lighting setup")
            row.add_css_class("dim-label")
            self.profiles_group.add(row)
            self._profile_rows.append(row)
            return

        for profile in self.profiles.profiles:
            row = Adw.ActionRow(title=profile.name, subtitle=profile.state.describe(),
                                activatable=True, use_markup=False)
            row.add_prefix(ColorSwatch(profile.state.preview_colors()))
            row.connect("activated", lambda _r, n=profile.name: self._apply_profile(n))

            menu = Gio.Menu()
            target = GLib.Variant("s", profile.name)
            for label, action in (("Apply", "profile-apply"),
                                  ("Overwrite with Current Settings", "profile-update"),
                                  ("Rename…", "profile-rename"),
                                  ("Delete", "profile-delete")):
                item = Gio.MenuItem.new(label, None)
                item.set_action_and_target_value(f"win.{action}", target)
                menu.append_item(item)
            more = Gtk.MenuButton(icon_name="view-more-symbolic", menu_model=menu,
                                  valign=Gtk.Align.CENTER, tooltip_text="Profile actions")
            more.add_css_class("flat")
            row.add_suffix(more)
            self.profiles_group.add(row)
            self._profile_rows.append(row)

    def _apply_profile(self, name: str) -> None:
        profile = self.profiles.find(name)
        if profile is None:
            return
        self.state = profile.state.copy()
        if self.state.brightness > 0:
            self.state_store.restore_brightness = self.state.brightness
        self._sync_ui_from_state()
        snapshot = self.state.copy()

        def done(results: list[CommandResult]) -> None:
            if self._after_apply(results, snapshot):
                self._toast(f"Applied “{name}”")
        self.runner.submit(Job([snapshot.mode_args(), snapshot.brightness_args()],
                               key="apply", callback=done))

    def _update_profile(self, name: str) -> None:
        self._save_profiles(lambda: self.profiles.upsert(name, self.state), f"Updated “{name}”")

    def _save_profiles(self, op: Callable, success: str) -> None:
        try:
            op()
        except (OSError, ValueError, KeyError) as exc:
            self._toast(str(exc))
            return
        self._rebuild_profiles()
        self._toast(success)

    def _name_dialog(self, heading: str, confirm: str, initial: str,
                     on_done: Callable[[str], None], exclude: Optional[str] = None) -> None:
        dialog = Adw.AlertDialog(heading=heading)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("ok", confirm)
        dialog.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("ok")
        dialog.set_close_response("cancel")

        entry = Adw.EntryRow(title="Profile name", text=initial, activates_default=True)
        box = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        box.add_css_class("boxed-list")
        box.append(entry)
        dialog.set_extra_child(box)

        def validate(*_):
            name = entry.get_text().strip()
            clash = self.profiles.find(name) if name else None
            clash = clash if clash and (exclude is None or clash.name.lower() != exclude.lower()) else None
            dialog.set_response_enabled("ok", bool(name) and not (clash and exclude))
            if clash and exclude is None:
                dialog.set_body(f"“{clash.name}” will be overwritten.")
            elif clash:
                dialog.set_body("A profile with this name already exists.")
            else:
                dialog.set_body("")
        entry.connect("changed", validate)
        validate()

        def on_response(_d, response):
            if response == "ok":
                on_done(entry.get_text().strip())
        dialog.connect("response", on_response)
        dialog.present(self)
        entry.grab_focus()

    def _prompt_save_profile(self) -> None:
        suggestion = f"{self.state.info.label.replace('Preset · ', '')} {len(self.profiles.profiles) + 1}"
        self._name_dialog("Save Profile", "Save", suggestion,
                          lambda name: self._save_profiles(
                              lambda: self.profiles.upsert(name, self.state), f"Saved “{name}”"))

    def _prompt_rename_profile(self, old: str) -> None:
        self._name_dialog("Rename Profile", "Rename", old,
                          lambda new: self._save_profiles(
                              lambda: self.profiles.rename(old, new), f"Renamed to “{new}”"),
                          exclude=old)

    def _confirm_delete_profile(self, name: str) -> None:
        dialog = Adw.AlertDialog(heading="Delete Profile?",
                                 body=f"“{name}” will be permanently removed.")
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("delete", "Delete")
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_close_response("cancel")
        def on_response(_d, response):
            if response == "delete":
                self._save_profiles(lambda: self.profiles.delete(name), f"Deleted “{name}”")
        dialog.connect("response", on_response)
        dialog.present(self)
