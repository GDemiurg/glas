"""
Wave visualization — a smooth liquid curve through the mic level history.

Gruvbox-rainbow gradient stroke with a translucent fade-fill below the
line. Breathes gently while the mic is silent; collapses to the baseline
on release (the OSD hides ~150ms after begin_collapse()).
"""

import math
import time

import cairo
import numpy as np

from .base import BaseVisualization, StateManager, VisualizerState

# Gruvbox brights, left → right along the line
RAINBOW = [
    (0.984, 0.286, 0.204),  # red    #fb4934
    (0.996, 0.502, 0.098),  # orange #fe8019
    (0.980, 0.741, 0.184),  # yellow #fabd2f
    (0.722, 0.733, 0.149),  # green  #b8bb26
    (0.557, 0.753, 0.486),  # aqua   #8ec07c
    (0.514, 0.647, 0.596),  # blue   #83a598
    (0.827, 0.525, 0.608),  # purple #d3869b
]

NUM_POINTS = 48            # curve resolution (level history is resampled to this)
LIQUID_EASE = 0.22         # per-frame lerp toward target — smooth both directions
BREATH_PERIOD_S = 2.4      # idle breathing cycle
QUIET_LEVEL = 0.04         # below this the wave is considered silent
COLLAPSE_RATE = 0.55       # per-frame shrink factor during release collapse


class WaveVisualization(BaseVisualization):
    """Scrolling area-chart wave of the real capture level."""

    def __init__(self):
        super().__init__()
        self.display = np.zeros(NUM_POINTS)
        self._breath_phase = 0.0
        self._recent_max = 0.0
        self._collapsing = False
        self.state_manager = StateManager()

    # ------------------------ Data ------------------------

    def update(self, level: float, samples: np.ndarray = None):
        super().update(level, samples)

        if self._collapsing:
            self.display *= COLLAPSE_RATE
            self.state_manager.update()
            return

        if samples is not None and len(samples) > 1:
            # Resample the level history onto the curve's points
            idx = np.linspace(0, len(samples) - 1, NUM_POINTS)
            target = np.interp(idx, np.arange(len(samples)), samples)
        else:
            target = np.zeros(NUM_POINTS)

        # Liquid: eased glide toward the target in both directions
        self.display += (target - self.display) * LIQUID_EASE

        # Track how loud it's been recently to blend the idle breathing in/out
        self._recent_max = max(float(target.max()), self._recent_max * 0.94)
        self._breath_phase = (self._breath_phase +
                              0.016 * (2 * math.pi / BREATH_PERIOD_S)) % (2 * math.pi)

        self.state_manager.update()

    def begin_collapse(self):
        """Start the release animation — the wave sinks into the baseline."""
        self._collapsing = True

    def reset(self):
        """Re-arm for the next show."""
        self._collapsing = False
        self.display[:] = 0.0
        self._recent_max = 0.0

    # ------------------------ Drawing ------------------------

    def draw(self, cr: cairo.Context, width: int, height: int):
        pad_x = 18
        pad_top = 12
        baseline = height - 14
        max_rise = baseline - pad_top

        # Idle breathing: when silent, a soft synchronized swell keeps the
        # line alive. Blended out as soon as real signal arrives.
        quiet = max(0.0, 1.0 - self._recent_max / QUIET_LEVEL) if not self._collapsing else 0.0
        breath = (0.5 + 0.5 * math.sin(self._breath_phase)) * 0.045 * quiet

        xs = np.linspace(pad_x, width - pad_x, NUM_POINTS)
        levels = np.clip(self.display + breath, 0.0, 1.0)
        ys = baseline - levels * max_rise

        # Rainbow gradient along the line
        rainbow = cairo.LinearGradient(pad_x, 0, width - pad_x, 0)
        for i, (r, g, b) in enumerate(RAINBOW):
            rainbow.add_color_stop_rgb(i / (len(RAINBOW) - 1), r, g, b)

        # --- Fade-fill below the curve ---
        # cairo can't multiply a horizontal rainbow by a vertical alpha ramp
        # in one pattern: paint the rainbow into a group, then mask it with
        # a vertical transparent-to-clear gradient.
        cr.save()
        self._curve_path(cr, xs, ys)
        cr.line_to(xs[-1], baseline)
        cr.line_to(xs[0], baseline)
        cr.close_path()
        cr.clip()
        cr.push_group()
        cr.set_source(rainbow)
        cr.paint()
        cr.pop_group_to_source()
        fade = cairo.LinearGradient(0, pad_top, 0, baseline)
        fade.add_color_stop_rgba(0, 0, 0, 0, 0.45)
        fade.add_color_stop_rgba(1, 0, 0, 0, 0.04)
        cr.mask(fade)
        cr.restore()

        # --- The line itself ---
        self._curve_path(cr, xs, ys)
        cr.set_source(rainbow)
        cr.set_line_width(2.5)
        cr.set_line_cap(cairo.LINE_CAP_ROUND)
        cr.set_line_join(cairo.LINE_JOIN_ROUND)
        cr.stroke()

    @staticmethod
    def _curve_path(cr: cairo.Context, xs: np.ndarray, ys: np.ndarray):
        """Smooth Catmull-Rom-style curve through the points (as béziers)."""
        cr.new_path()
        cr.move_to(xs[0], ys[0])
        n = len(xs)
        for i in range(n - 1):
            x0, y0 = (xs[i - 1], ys[i - 1]) if i > 0 else (xs[0], ys[0])
            x1, y1 = xs[i], ys[i]
            x2, y2 = xs[i + 1], ys[i + 1]
            x3, y3 = (xs[i + 2], ys[i + 2]) if i + 2 < n else (x2, y2)
            c1x = x1 + (x2 - x0) / 6.0
            c1y = y1 + (y2 - y0) / 6.0
            c2x = x2 - (x3 - x1) / 6.0
            c2y = y2 - (y3 - y1) / 6.0
            cr.curve_to(c1x, c1y, c2x, c2y, x2, y2)

    # ------------------------ State ------------------------

    def set_state(self, state_str: str):
        self.state_manager.set_state_from_string(state_str)

    def set_elapsed_time(self, seconds: float):
        pass  # wave mode shows no timer
