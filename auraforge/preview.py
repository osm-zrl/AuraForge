"""Animated on-screen preview of the keyboard + light bar lighting."""

from __future__ import annotations

import colorsys
import math

import gi

gi.require_version("Gtk", "4.0")
gi.require_foreign("cairo")
from gi.repository import Gtk  # noqa: E402

from .modes import LightingState  # noqa: E402

# Key widths in key units; every row is 15 units wide.
ROWS = [
    [1.0] * 15,
    [1.0] * 13 + [2.0],
    [1.5] + [1.0] * 12 + [1.5],
    [1.75] + [1.0] * 11 + [2.25],
    [2.25] + [1.0] * 10 + [2.75],
    [1.25, 1.25, 1.25, 6.25, 1.25, 1.25, 1.25, 1.25],
]
ROW_UNITS = 15.0
BRIGHTNESS_FACTOR = {0: 0.0, 1: 0.35, 2: 0.68, 3: 1.0}
BREATH_PERIOD = {1: 6.0, 2: 4.0, 3: 2.2}
CYCLE_PERIOD = {1: 14.0, 2: 8.0, 3: 3.5}


def hex_to_rgb(value: str) -> tuple[float, float, float]:
    return tuple(int(value[i:i + 2], 16) / 255.0 for i in (0, 2, 4))  # type: ignore[return-value]


def rounded_rect(cr, x, y, w, h, r):
    r = min(r, w / 2, h / 2)
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
    cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    cr.arc(x + r, y + r, r, math.pi, 1.5 * math.pi)
    cr.close_path()


class KeyboardPreview(Gtk.DrawingArea):
    def __init__(self):
        super().__init__()
        self.set_content_height(210)
        self.set_hexpand(True)
        self.set_draw_func(self._draw)
        self._state = LightingState()
        self._powered = True
        self._tick_id = 0
        self._t = 0.0
        self.update_property([Gtk.AccessibleProperty.LABEL], ["Keyboard lighting preview"])

    # -- public -------------------------------------------------------------
    def set_state(self, state: LightingState, powered: bool) -> None:
        self._state = state.copy()
        self._powered = powered
        animate = state.info.animated and powered and state.brightness > 0
        if animate and not self._tick_id:
            self._tick_id = self.add_tick_callback(self._on_tick)
        elif not animate and self._tick_id:
            self.remove_tick_callback(self._tick_id)
            self._tick_id = 0
        self.queue_draw()

    # -- animation ------------------------------------------------------------
    def _on_tick(self, _widget, clock) -> bool:
        self._t = clock.get_frame_time() / 1_000_000.0
        self.queue_draw()
        return True

    def _led_color(self, x_frac: float, zone: int) -> tuple[tuple[float, float, float], float]:
        """Return (rgb, intensity) for a key at horizontal position x_frac."""
        s = self._state
        t = self._t
        intensity = 1.0
        mode = s.mode
        if mode in ("single_static",):
            rgb = hex_to_rgb(s.color)
        elif mode == "single_breathing":
            period = BREATH_PERIOD[s.speed]
            cycle = int(t // period)
            phase = (t % period) / period
            rgb = hex_to_rgb(s.color if cycle % 2 == 0 else s.color2)
            intensity = math.sin(math.pi * phase) ** 2
        elif mode == "single_colorcycle":
            hue = (t / CYCLE_PERIOD[s.speed]) % 1.0
            rgb = colorsys.hsv_to_rgb(hue, 1.0, 1.0)
        elif mode == "multi_static":
            rgb = hex_to_rgb(s.zones[zone])
        elif mode == "multi_breathing":
            period = BREATH_PERIOD[s.speed]
            rgb = hex_to_rgb(s.zones[zone])
            intensity = math.sin(math.pi * ((t % period) / period)) ** 2
        elif mode == "rainbow_cycle":
            hue = (x_frac * 0.9 - t / CYCLE_PERIOD[s.speed]) % 1.0
            rgb = colorsys.hsv_to_rgb(hue, 1.0, 1.0)
        else:
            fixed = s.info.fixed_colors
            rgb = hex_to_rgb(fixed[zone] if len(fixed) == 4 else fixed[0])
        factor = BRIGHTNESS_FACTOR[s.brightness] if self._powered else 0.0
        return rgb, intensity * factor

    # -- drawing ----------------------------------------------------------------
    def _draw(self, _area, cr, width, height):
        fg = self.get_color()
        dark = (fg.red + fg.green + fg.blue) / 3 > 0.5  # light text => dark theme

        pad = 14
        bar_h = 10
        gap = 14
        kb_w = width - 2 * pad
        kb_h = height - 2 * pad - bar_h - gap
        if kb_w <= 40 or kb_h <= 40:
            return

        # Chassis / deck
        rounded_rect(cr, pad, pad, kb_w, kb_h, 14)
        cr.set_source_rgba(0.09, 0.09, 0.10, 1.0) if dark else cr.set_source_rgba(0.16, 0.16, 0.18, 1.0)
        cr.fill()

        inner = 10
        ux = (kb_w - 2 * inner) / ROW_UNITS
        row_factors = [0.7] + [1.0] * (len(ROWS) - 1)   # function row is shorter
        unit_h = (kb_h - 2 * inner) / sum(row_factors)
        key_gap = max(2.0, ux * 0.12)
        base = (0.20, 0.20, 0.22)

        y = pad + inner
        for r, row in enumerate(ROWS):
            x = pad + inner
            row_h = unit_h * row_factors[r]
            h = row_h - key_gap
            for units in row:
                w = units * ux - key_gap
                cx_frac = ((x - pad - inner) + w / 2) / (kb_w - 2 * inner)
                zone = min(3, int(cx_frac * 4))
                rgb, k = self._led_color(cx_frac, zone)
                # glow
                if k > 0.02:
                    rounded_rect(cr, x - 1.5, y - 1.5, w + 3, h + 3, 5)
                    cr.set_source_rgba(*rgb, 0.30 * k)
                    cr.fill()
                rounded_rect(cr, x, y, w, h, 4)
                col = tuple(b * (1 - 0.85 * k) + c * 0.85 * k for b, c in zip(base, rgb))
                cr.set_source_rgb(*col)
                cr.fill()
                x += units * ux
            y += row_h

        # Front light bar
        by = pad + kb_h + gap
        bx = pad + kb_w * 0.08
        bw = kb_w * 0.84
        segments = 48
        for i in range(segments):
            frac = (i + 0.5) / segments
            zone = min(3, int(frac * 4))
            rgb, k = self._led_color(frac, zone)
            sx = bx + bw * i / segments
            cr.rectangle(sx, by, bw / segments + 0.5, bar_h)
            cr.set_source_rgb(*(0.18 * (1 - k) + c * k for c in rgb))
            cr.fill()
        # Rounded mask edge (cosmetic)
        cr.set_line_width(1.0)
        rounded_rect(cr, bx, by, bw, bar_h, bar_h / 2)
        cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.15)
        cr.stroke()


class ColorSwatch(Gtk.DrawingArea):
    """Small striped swatch used as a prefix in profile rows."""

    def __init__(self, colors: list[str], size: int = 28):
        super().__init__()
        self.set_content_width(size)
        self.set_content_height(size)
        self.set_valign(Gtk.Align.CENTER)
        self._colors = colors or ["000000"]
        self.set_draw_func(self._draw)

    def _draw(self, _area, cr, width, height):
        rounded_rect(cr, 0, 0, width, height, min(width, height) / 2)
        cr.clip()
        n = len(self._colors)
        for i, c in enumerate(self._colors):
            cr.rectangle(width * i / n, 0, width / n + 0.5, height)
            cr.set_source_rgb(*hex_to_rgb(c))
            cr.fill()
