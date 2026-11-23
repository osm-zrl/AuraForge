"""Persistence for profiles, last-applied state and app settings.

Everything lives in ~/.config/auraforge/ (honours $XDG_CONFIG_HOME):
  profiles.json  - {"profiles": [{"name": str, "state": {...}}, ...]}
  state.json     - last applied LightingState + remembered brightness
  settings.json  - UI preferences
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Optional

from .modes import LightingState


LEGACY_DIR_NAME = "rog-aura-control"   # the app's name before it became AuraForge


def config_dir() -> Path:
    base = Path(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"))
    new = base / "auraforge"
    old = base / LEGACY_DIR_NAME
    if not new.exists() and old.is_dir():
        # Carry profiles and the last state over from the old name.
        try:
            old.rename(new)
        except OSError as exc:
            print(f"auraforge: could not migrate {old} -> {new}: {exc}", file=sys.stderr)
            return old
    return new


def _load_json(path: Path, default: Any) -> Any:
    try:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return default
    except (OSError, ValueError) as exc:
        # Keep a copy of the broken file instead of silently discarding it.
        print(f"auraforge: could not read {path}: {exc}", file=sys.stderr)
        try:
            path.replace(path.with_suffix(path.suffix + ".broken"))
        except OSError:
            pass
        return default


def _save_json(path: Path, data: Any) -> None:
    """Atomic write: temp file in the same dir, then rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
            fh.write("\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


class Profile:
    __slots__ = ("name", "state")

    def __init__(self, name: str, state: LightingState):
        self.name = name
        self.state = state


class ProfileStore:
    def __init__(self, directory: Optional[Path] = None):
        self.path = (directory or config_dir()) / "profiles.json"
        self.profiles: list[Profile] = []
        self.load()

    def load(self) -> None:
        data = _load_json(self.path, {"profiles": []})
        items = data.get("profiles", []) if isinstance(data, dict) else []
        self.profiles = []
        seen = set()
        for item in items:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", "")).strip()
            if not name or name.lower() in seen:
                continue
            seen.add(name.lower())
            self.profiles.append(Profile(name, LightingState.from_dict(item.get("state"))))

    def save(self) -> None:
        _save_json(self.path, {"version": 1, "profiles": [
            {"name": p.name, "state": p.state.to_dict()} for p in self.profiles]})

    def find(self, name: str) -> Optional[Profile]:
        key = name.strip().lower()
        return next((p for p in self.profiles if p.name.lower() == key), None)

    def upsert(self, name: str, state: LightingState) -> Profile:
        name = name.strip()
        existing = self.find(name)
        if existing:
            existing.state = state.copy()
            profile = existing
        else:
            profile = Profile(name, state.copy())
            self.profiles.append(profile)
        self.save()
        return profile

    def rename(self, old: str, new: str) -> None:
        profile = self.find(old)
        new = new.strip()
        if profile is None:
            raise KeyError(old)
        clash = self.find(new)
        if clash is not None and clash is not profile:
            raise ValueError(f"A profile named “{new}” already exists")
        profile.name = new
        self.save()

    def delete(self, name: str) -> None:
        profile = self.find(name)
        if profile is not None:
            self.profiles.remove(profile)
            self.save()


class StateStore:
    """Last applied lighting state, plus the brightness to restore on power-on."""

    def __init__(self, directory: Optional[Path] = None):
        self.path = (directory or config_dir()) / "state.json"
        data = _load_json(self.path, {})
        if not isinstance(data, dict):
            data = {}
        self.state = LightingState.from_dict(data.get("state"))
        restore = data.get("restore_brightness", 3)
        self.restore_brightness = restore if restore in (1, 2, 3) else 3
        self.has_saved_state = "state" in data

    def save(self) -> None:
        _save_json(self.path, {"version": 1, "state": self.state.to_dict(),
                               "restore_brightness": self.restore_brightness})
        self.has_saved_state = True


class Settings:
    DEFAULTS = {"live_apply": True}

    def __init__(self, directory: Optional[Path] = None):
        self.path = (directory or config_dir()) / "settings.json"
        data = _load_json(self.path, {})
        self.values = dict(self.DEFAULTS)
        if isinstance(data, dict):
            for k, default in self.DEFAULTS.items():
                if isinstance(data.get(k), type(default)):
                    self.values[k] = data[k]

    def __getitem__(self, key):
        return self.values[key]

    def __setitem__(self, key, value):
        self.values[key] = value
        _save_json(self.path, self.values)
