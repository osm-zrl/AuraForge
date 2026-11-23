"""Thin wrapper around the `rogauracore` command-line tool.

Nothing here talks to USB directly: every hardware action is a `rogauracore`
invocation.  Commands are executed on a single background worker thread so the
GTK main loop never blocks and commands never interleave on the USB device.
"""

from __future__ import annotations

import os
import queue
import shlex
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

ASUS_VENDOR_ID = "0b05"
# Product IDs hard-coded in rogauracore (src/rogauracore.c).
SUPPORTED_PRODUCT_IDS = {"1854", "1866", "1869", "19b6", "1a30"}
UDEV_RULE_PATH = Path("/etc/udev/rules.d/99-rogauracore.rules")
FALLBACK_PATHS = ("/usr/local/bin/rogauracore", "/usr/bin/rogauracore")

DEFAULT_TIMEOUT = 15
POLKIT_TIMEOUT = 180


def find_rogauracore() -> Optional[str]:
    """Locate the rogauracore binary (env override, $PATH, common prefixes)."""
    override = os.environ.get("ROGAURACORE_BIN")
    if override and os.access(override, os.X_OK):
        return override
    found = shutil.which("rogauracore")
    if found:
        return found
    for path in FALLBACK_PATHS:
        if os.access(path, os.X_OK):
            return path
    return None


# --------------------------------------------------------------------------
#  Results
# --------------------------------------------------------------------------

@dataclass
class CommandResult:
    argv: list[str]
    returncode: Optional[int]
    stdout: str = ""
    stderr: str = ""
    duration: float = 0.0
    kind: str = "ok"     # ok | missing | permission | no_device | busy | timeout | cancelled | failed
    message: str = ""    # short human-friendly explanation
    timestamp: float = field(default_factory=time.time)

    @property
    def ok(self) -> bool:
        return self.kind == "ok"

    @property
    def command_line(self) -> str:
        return shlex.join(self.argv)

    @property
    def display_line(self) -> str:
        """Command line with the binary shown by name only (for the status bar)."""
        return shlex.join([os.path.basename(self.argv[0]), *self.argv[1:]]) if self.argv else ""

    @property
    def output(self) -> str:
        return "\n".join(s for s in (self.stdout.strip(), self.stderr.strip()) if s)


def _rogauracore_succeeded(result: CommandResult) -> bool:
    """rogauracore's main() returns the result of its last libusb control
    transfer, i.e. the number of bytes sent (17 = MESSAGE_LENGTH) on success.
    Every failure path prints to stderr and returns a negative value, which
    the shell sees as 128-255.  So a small positive code with no error text is
    a success, not a failure."""
    rc = result.returncode
    if rc == 0:
        return True
    return rc is not None and 0 < rc < 128 and not result.stderr.strip()


def _classify(result: CommandResult, is_rogauracore: bool) -> None:
    if result.returncode == 0 or (is_rogauracore and _rogauracore_succeeded(result)):
        result.kind, result.message = "ok", "Success"
        return
    text = f"{result.stdout}\n{result.stderr}"
    if not is_rogauracore:
        if result.returncode in (126, 127) and result.argv[:1] == ["pkexec"]:
            result.kind, result.message = "cancelled", "Authentication was cancelled or denied"
        else:
            result.kind, result.message = "failed", f"Command failed (exit {result.returncode})"
        return
    if "LIBUSB_ERROR_ACCESS" in text or "Access denied" in text:
        result.kind = "permission"
        result.message = "Permission denied - run install.sh (udev rule), then reboot"
    elif "Could not find ROG Aura Core keyboard" in text:
        result.kind = "no_device"
        result.message = "No supported ROG Aura keyboard was found on the USB bus."
    elif "LIBUSB_ERROR_BUSY" in text:
        result.kind = "busy"
        result.message = ("The keyboard is busy - another tool (e.g. asusd/asusctl) may be "
                          "holding it.")
    elif "Could not interpret" in text:
        result.kind = "failed"
        result.message = "rogauracore rejected an argument."
    else:
        result.kind = "failed"
        first = (result.stderr.strip() or result.stdout.strip()).splitlines()
        result.message = first[-1] if first else f"rogauracore exited with status {result.returncode}"


def run_argv(argv: list[str], timeout: int = DEFAULT_TIMEOUT,
             is_rogauracore: bool = False) -> CommandResult:
    """Run *argv* synchronously and return a classified result."""
    start = time.monotonic()
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                              stdin=subprocess.DEVNULL)
        result = CommandResult(argv, proc.returncode, proc.stdout, proc.stderr)
        _classify(result, is_rogauracore)
    except FileNotFoundError:
        result = CommandResult(argv, None, kind="missing",
                               message=f"'{argv[0]}' was not found. Run install.sh first.")
    except PermissionError as exc:
        result = CommandResult(argv, None, stderr=str(exc), kind="permission",
                               message=f"Cannot execute '{argv[0]}': {exc}")
    except subprocess.TimeoutExpired as exc:
        result = CommandResult(argv, None, stdout=exc.stdout or "", stderr=exc.stderr or "",
                               kind="timeout", message=f"Timed out after {timeout}s")
    except OSError as exc:
        result = CommandResult(argv, None, stderr=str(exc), kind="failed", message=str(exc))
    result.duration = time.monotonic() - start
    return result


def run_rogauracore(args: list[str], timeout: int = DEFAULT_TIMEOUT) -> CommandResult:
    binary = find_rogauracore()
    if binary is None:
        return CommandResult(["rogauracore", *args], None, kind="missing",
                             message="rogauracore is not installed. Run install.sh first.")
    return run_argv([binary, *args], timeout=timeout, is_rogauracore=True)


# --------------------------------------------------------------------------
#  Diagnostics helpers
# --------------------------------------------------------------------------

@dataclass
class UsbDevice:
    vendor: str
    product: str
    name: str
    node: Optional[str]
    writable: bool

    @property
    def supported(self) -> bool:
        return self.product in SUPPORTED_PRODUCT_IDS

    @property
    def usb_id(self) -> str:
        return f"{self.vendor}:{self.product}"


def _read(path: Path) -> str:
    try:
        return path.read_text().strip()
    except OSError:
        return ""


def detect_asus_devices() -> list[UsbDevice]:
    """List ASUS (0b05) USB devices via sysfs, flagging whether we can open them."""
    devices: list[UsbDevice] = []
    root = Path("/sys/bus/usb/devices")
    if not root.is_dir():
        return devices
    for dev in sorted(root.iterdir()):
        if _read(dev / "idVendor").lower() != ASUS_VENDOR_ID:
            continue
        product = _read(dev / "idProduct").lower()
        name = _read(dev / "product") or "ASUS device"
        busnum, devnum = _read(dev / "busnum"), _read(dev / "devnum")
        node = None
        writable = False
        if busnum.isdigit() and devnum.isdigit():
            node = f"/dev/bus/usb/{int(busnum):03d}/{int(devnum):03d}"
            writable = os.access(node, os.R_OK | os.W_OK)
        devices.append(UsbDevice(ASUS_VENDOR_ID, product, name, node, writable))
    return devices


def conflicting_services() -> list[str]:
    """Return running services known to fight over the Aura keyboard."""
    running = []
    for unit in ("asusd.service",):
        try:
            proc = subprocess.run(["systemctl", "is-active", "--quiet", unit], timeout=3)
            if proc.returncode == 0:
                running.append(unit)
        except (OSError, subprocess.TimeoutExpired):
            pass
    return running


# --------------------------------------------------------------------------
#  Background runner
# --------------------------------------------------------------------------

@dataclass
class Job:
    steps: list[list[str]]                    # each step: argv (rogauracore args or full argv)
    callback: Optional[Callable[[list[CommandResult]], None]] = None
    key: Optional[str] = None                 # jobs sharing a key coalesce (latest wins)
    raw: bool = False                         # steps are full argv, not rogauracore args
    timeout: int = DEFAULT_TIMEOUT


class CommandRunner:
    """Serialises commands onto one worker thread.

    `dispatch` marshals callbacks back to the UI thread (GLib.idle_add in the
    app).  `on_result` is called for every executed step (used for logging).
    """

    def __init__(self, dispatch: Callable, on_result: Optional[Callable[[CommandResult], None]] = None,
                 on_busy: Optional[Callable[[bool], None]] = None):
        self._dispatch = dispatch
        self._on_result = on_result
        self._on_busy = on_busy
        self._pending: list[Job] = []
        self._cv = threading.Condition()
        self._thread = threading.Thread(target=self._loop, name="rogauracore-runner", daemon=True)
        self._thread.start()

    def submit(self, job: Job) -> None:
        with self._cv:
            if job.key is not None:
                self._pending = [j for j in self._pending if j.key != job.key]
            self._pending.append(job)
            self._cv.notify()

    def _emit(self, fn, *args):
        if fn is not None:
            self._dispatch(lambda: (fn(*args), False)[1])

    def _loop(self) -> None:
        while True:
            with self._cv:
                while not self._pending:
                    self._cv.wait()
                job = self._pending.pop(0)
            self._emit(self._on_busy, True)
            results: list[CommandResult] = []
            for step in job.steps:
                if job.raw:
                    res = run_argv(step, timeout=job.timeout)
                else:
                    res = run_rogauracore(step, timeout=job.timeout)
                results.append(res)
                self._emit(self._on_result, res)
                if not res.ok:
                    break
            self._emit(job.callback, results)
            with self._cv:
                idle = not self._pending
            if idle:
                self._emit(self._on_busy, False)


FIX_BRIGHTNESS_KEYS_ARGV = ["pkexec", "systemctl", "restart", "upower.service"]
