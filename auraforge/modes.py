"""Lighting modes supported by rogauracore and the state model built on them."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Optional

HEX_RE = re.compile(r"^[0-9a-f]{6}$")


@dataclass(frozen=True)
class ModeInfo:
    command: str          # rogauracore sub-command
    label: str            # human readable name
    colors: str = "none"  # "none" | "single" | "dual" | "zones"
    speed: bool = False   # takes a SPEED argument (1-3)
    animated: bool = False
    fixed_colors: tuple[str, ...] = ()  # colors baked into the command (presets)


# Order matters: this is the order shown in the mode drop-down.
MODES: list[ModeInfo] = [
    ModeInfo("single_static", "Static", colors="single"),
    ModeInfo("single_breathing", "Breathing", colors="dual", speed=True, animated=True),
    ModeInfo("single_colorcycle", "Color Cycle", speed=True, animated=True),
    ModeInfo("multi_static", "4-Zone Static", colors="zones"),
    ModeInfo("multi_breathing", "4-Zone Breathing", colors="zones", speed=True, animated=True),
    ModeInfo("rainbow_cycle", "Rainbow Wave", speed=True, animated=True),
    ModeInfo("rainbow", "Rainbow (static zones)",
             fixed_colors=("ff0000", "ffff00", "00ffff", "ff00ff")),
    ModeInfo("red", "Preset · Red", fixed_colors=("ff0000",)),
    ModeInfo("green", "Preset · Green", fixed_colors=("00ff00",)),
    ModeInfo("blue", "Preset · Blue", fixed_colors=("0000ff",)),
    ModeInfo("yellow", "Preset · Yellow", fixed_colors=("ffff00",)),
    ModeInfo("gold", "Preset · Gold", fixed_colors=("ff8c00",)),
    ModeInfo("cyan", "Preset · Cyan", fixed_colors=("00ffff",)),
    ModeInfo("magenta", "Preset · Magenta", fixed_colors=("ff00ff",)),
    ModeInfo("white", "Preset · White", fixed_colors=("ffffff",)),
    ModeInfo("black", "Preset · Black (off)", fixed_colors=("000000",)),
]

MODES_BY_COMMAND = {m.command: m for m in MODES}

SPEED_LABELS = {1: "Slow", 2: "Medium", 3: "Fast"}
BRIGHTNESS_LABELS = {0: "Off", 1: "Low", 2: "Medium", 3: "High"}
ZONE_LABELS = ["Zone 1 · Left", "Zone 2 · Center-left", "Zone 3 · Center-right", "Zone 4 · Right"]


def normalize_hex(value: str, default: str = "ffffff") -> str:
    """Return a 6-character lowercase hex string or *default*."""
    if not isinstance(value, str):
        return default
    v = value.strip().lstrip("#").lower()
    if len(v) == 3:
        v = "".join(c * 2 for c in v)
    return v if HEX_RE.match(v) else default


def clamp_int(value, lo: int, hi: int, default: int) -> int:
    try:
        return max(lo, min(hi, int(value)))
    except (TypeError, ValueError):
        return default


@dataclass
class LightingState:
    mode: str = "single_static"
    color: str = "ff0000"            # primary colour (static / breathing)
    color2: str = "0000ff"           # secondary breathing colour
    zones: list[str] = field(default_factory=lambda: ["ff0000", "ffff00", "00ffff", "ff00ff"])
    speed: int = 2
    brightness: int = 3

    # -- serialisation ---------------------------------------------------
    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Optional[dict]) -> "LightingState":
        s = cls()
        if not isinstance(data, dict):
            return s
        mode = data.get("mode")
        if mode in MODES_BY_COMMAND:
            s.mode = mode
        s.color = normalize_hex(data.get("color", s.color), s.color)
        s.color2 = normalize_hex(data.get("color2", s.color2), s.color2)
        zones = data.get("zones")
        if isinstance(zones, list) and len(zones) == 4:
            s.zones = [normalize_hex(z, d) for z, d in zip(zones, s.zones)]
        s.speed = clamp_int(data.get("speed"), 1, 3, s.speed)
        s.brightness = clamp_int(data.get("brightness"), 0, 3, s.brightness)
        return s

    def copy(self) -> "LightingState":
        return LightingState.from_dict(self.to_dict())

    # -- command construction ---------------------------------------------
    @property
    def info(self) -> ModeInfo:
        return MODES_BY_COMMAND[self.mode]

    def mode_args(self) -> list[str]:
        """Arguments for rogauracore that set the lighting mode."""
        info = self.info
        args = [info.command]
        if info.colors == "single":
            args.append(self.color)
        elif info.colors == "dual":
            args += [self.color, self.color2]
        elif info.colors == "zones":
            args += list(self.zones)
        if info.speed:
            args.append(str(self.speed))
        return args

    def brightness_args(self) -> list[str]:
        return ["brightness", str(self.brightness)]

    def preview_colors(self) -> list[str]:
        """The 1-4 colours that best describe this state (for swatches)."""
        info = self.info
        if info.fixed_colors:
            return list(info.fixed_colors)
        if info.colors == "single":
            return [self.color]
        if info.colors == "dual":
            return [self.color, self.color2]
        if info.colors == "zones":
            return list(self.zones)
        if self.mode == "rainbow_cycle":
            return ["ff0000", "ffff00", "00ff00", "00ffff", "0000ff", "ff00ff"]
        return ["ff0000", "00ff00", "0000ff"]  # colour cycle

    def describe(self) -> str:
        info = self.info
        parts = [info.label]
        if info.speed:
            parts.append(f"{SPEED_LABELS[self.speed]} speed")
        parts.append(f"Brightness {BRIGHTNESS_LABELS[self.brightness]}")
        return " · ".join(parts)
