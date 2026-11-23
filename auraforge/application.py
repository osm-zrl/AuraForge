"""Application entry point: GUI plus a headless CLI used by the login autostart."""

from __future__ import annotations

import argparse
import sys
import time

from . import APP_ID, APP_NAME, VERSION


# --------------------------------------------------------------------------
#  Headless mode (no GTK needed)
# --------------------------------------------------------------------------

def _apply_headless(state, retries: int = 5, delay: float = 2.0) -> int:
    """Apply *state* via rogauracore, retrying while the USB device settles."""
    from . import backend

    for attempt in range(1, retries + 1):
        ok = True
        for args in (state.mode_args(), state.brightness_args()):
            res = backend.run_rogauracore(args)
            print(f"$ {res.command_line}  -> {res.returncode} {res.message}")
            if res.output:
                print(res.output, file=sys.stderr)
            if not res.ok:
                ok = False
                if res.kind in ("missing", "failed"):
                    return 1  # retrying will not help
                break
        if ok:
            return 0
        if attempt < retries:
            time.sleep(delay)
    return 1


def restore_last_state() -> int:
    from .storage import StateStore

    store = StateStore()
    if not store.has_saved_state:
        print("No saved lighting state yet; nothing to restore.")
        return 0
    return _apply_headless(store.state)


def apply_profile(name: str) -> int:
    from .storage import ProfileStore, StateStore

    profile = ProfileStore().find(name)
    if profile is None:
        print(f"No profile named {name!r}.", file=sys.stderr)
        return 2
    code = _apply_headless(profile.state, retries=1)
    if code == 0:
        store = StateStore()
        store.state = profile.state.copy()
        store.save()
    return code


def list_profiles() -> int:
    from .storage import ProfileStore

    for p in ProfileStore().profiles:
        print(f"{p.name}\t{p.state.describe()}")
    return 0


# --------------------------------------------------------------------------
#  GUI
# --------------------------------------------------------------------------

def run_gui(argv: list[str]) -> int:
    import gi

    try:
        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
    except ValueError as exc:
        print(f"{APP_NAME} needs GTK 4 and libadwaita: {exc}\n"
              "Install them with: sudo dnf install gtk4 libadwaita python3-gobject",
              file=sys.stderr)
        return 1
    from gi.repository import Adw, Gio, Gtk

    if (Adw.get_major_version(), Adw.get_minor_version()) < (1, 5):
        print(f"{APP_NAME} needs libadwaita >= 1.5 (Fedora 40 or newer).", file=sys.stderr)
        return 1

    from .window import MainWindow

    class AuraApplication(Adw.Application):
        def __init__(self):
            super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
            self.window = None

        def do_startup(self):
            Adw.Application.do_startup(self)
            for name, cb in (("quit", lambda *_: self.quit()), ("about", self._on_about)):
                action = Gio.SimpleAction.new(name, None)
                action.connect("activate", cb)
                self.add_action(action)
            self.set_accels_for_action("app.quit", ["<Ctrl>q"])
            self.set_accels_for_action("window.close", ["<Ctrl>w"])
            self.set_accels_for_action("win.apply", ["<Ctrl>Return"])
            self.set_accels_for_action("win.save-profile", ["<Ctrl>s"])
            self.set_accels_for_action("win.initialize", ["<Ctrl>i"])

        def do_activate(self):
            if self.window is None:
                self.window = MainWindow(self)
            self.window.present()

        def _on_about(self, *_):
            about = Adw.AboutDialog(
                application_name=APP_NAME,
                application_icon=APP_ID,
                version=VERSION,
                developer_name="Osmz",
                comments="Keyboard and light bar RGB control for ASUS ROG laptops.\n"
                         "Powered by rogauracore.",
                website="https://github.com/osm-zrl",
                developers=["Osmz https://github.com/osm-zrl",
                            "Claude (Anthropic) https://claude.com/claude-code"],
                copyright="© 2026 Osmz",
                license_type=Gtk.License.MIT_X11,
            )
            about.add_credit_section("Backend", ["rogauracore by Will Roberts https://github.com/wroberts/rogauracore"])
            about.present(self.window)

    app = AuraApplication()
    return app.run(argv)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    parser = argparse.ArgumentParser(
        prog="auraforge",
        description=f"{APP_NAME} - RGB keyboard control for ASUS ROG laptops (rogauracore GUI).")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--restore", action="store_true",
                       help="apply the last saved lighting state without opening a window")
    group.add_argument("--apply-profile", metavar="NAME", help="apply a saved profile and exit")
    group.add_argument("--list-profiles", action="store_true", help="list saved profiles and exit")
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    opts, _unknown = parser.parse_known_args(argv[1:])

    if opts.restore:
        return restore_last_state()
    if opts.apply_profile:
        return apply_profile(opts.apply_profile)
    if opts.list_profiles:
        return list_profiles()
    return run_gui(argv[:1])
