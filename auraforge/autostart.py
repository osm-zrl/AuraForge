"""'Restore last lighting profile on login' via an XDG autostart entry.

GNOME runs ~/.config/autostart/*.desktop at login inside the graphical session,
which is exactly when the keyboard is reachable and the user's session is set up.
The entry runs `auraforge --restore`, which applies state.json headlessly
(no window) and exits.
"""

from __future__ import annotations

import os
import shlex
import shutil
import sys
from pathlib import Path

from . import APP_ID

AUTOSTART_NAME = f"{APP_ID}.restore.desktop"


def autostart_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return Path(base) / "autostart" / AUTOSTART_NAME


def _exec_line() -> str:
    launcher = shutil.which("auraforge")
    if launcher:
        return f"{shlex.quote(launcher)} --restore"
    # Running from a source checkout: call the package with this interpreter.
    pkg_parent = Path(__file__).resolve().parent.parent
    return (f"env PYTHONPATH={shlex.quote(str(pkg_parent))} "
            f"{shlex.quote(sys.executable)} -m auraforge --restore")


def is_enabled() -> bool:
    path = autostart_path()
    if not path.exists():
        return False
    try:
        return "X-GNOME-Autostart-enabled=false" not in path.read_text()
    except OSError:
        return False


def set_enabled(enabled: bool) -> None:
    path = autostart_path()
    if not enabled:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=AuraForge (restore lighting)\n"
        "Comment=Re-apply the last keyboard lighting profile at login\n"
        f"Exec={_exec_line()}\n"
        f"Icon={APP_ID}\n"
        "Terminal=false\n"
        "NoDisplay=true\n"
        "X-GNOME-Autostart-enabled=true\n"
        "X-GNOME-Autostart-Delay=3\n"
    )
