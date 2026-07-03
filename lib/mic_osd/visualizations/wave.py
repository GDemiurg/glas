"""
Wave visualization — braided cream strands over a low-poly fill.

Two smooth curves from the same level history, phase-offset so they weave
around each other along the envelope. Underneath: sharp triangular facets
(low-poly fill) down to the baseline; on top: node dots riding the center
curve and short-lived diamond sparks on loud peaks. Gruvbox cream/gray
monochrome. Breathes gently while silent; collapses to the baseline on
release (the OSD hides ~160ms after begin_collapse()).
"""

import math
import time

import cairo
import numpy as np

from .base import BaseVisualization, StateManager

CREAM = (0.922, 0.859, 0.698)   # gruvbox fg #ebdbb2
GRAY = (0.573, 0.514, 0.455)    # gruvbox gray #928374

NUM_POINTS = 48            # curve resolution (level history is resampled to this)
LIQUID_EASE = 0.22         # per-frame lerp toward target — smooth both directions
BREATH_PERIOD_S = 2.4      # idle breathing cycle
QUIET_LEVEL = 0.04         # below this the wave is considered silent
COLLAPSE_RATE = 0.55       # per-frame shrink factor during release collapse

BRAID_CYCLES = 3.5         # how many strand crossings across the width
BRAID_DRIFT_S = 5.0        # slow phase drift so the braid isn't static
FACET_STEP = 4             # curve points per low-poly facet
DOT_STEP = 6               # curve points per node dot
SPARK_LEVEL = 0.55         # peak level that fires a spark
SPARK_LIFE_S = 0.35
SPARK_COOLDOWN_S = 0.12


class WaveVisualization(BaseVisualization):
    """Braided-strand wave of the real capture level."""

    def __init__(self):
        super().__init__()
        self.display = np.zeros(NUM_POINTS)
        self._breath_phase = 0.0
        self._braid_phase = 0.0
        self._recent_max = 0.0
        self._collapsing = False
        self._sparks = []          # list of (point_index, birth_monotonic)
        self._last_spark_at = 0.0
        self.state_manager = StateManager()

    # ------------------------ Data ------------------------

    def update(self, level: float, samples: np.ndarray = None):
        super().update(level, samples)

        if self._collapsing:
            self.display *= COLLAPSE_RATE
            self.state_manager.update()
            return

        if samples is not None and len(samples) > 1:
            idx = np.linspace(0, len(samples) - 1, NUM_POINTS)
            target = np.interp(idx, np.arange(len(samples)), samples)
        else:
            target = np.zeros(NUM_POINTS)

        # Liquid: eased glide toward the target in both directions
        self.display += (target - self.display) * LIQUID_EASE

        # Peak sparks: newest part of the wave crossing the loud threshold
        now = time.monotonic()
        if (level >= SPARK_LEVEL
                and now - self._last_spark_at >= SPARK_COOLDOWN_S):
            self._sparks.append((NUM_POINTS - 3, now))
            self._last_spark_at = now
        self._sparks = [s for s in self._sparks if now - s[1] < SPARK_LIFE_S]

        # Idle-breathing blend factor + phases
        self._recent_max = max(float(target.max()), self._recent_max * 0.94)
        self._breath_phase = (self._breath_phase +
                              0.016 * (2 * math.pi / BREATH_PERIOD_S)) % (2 * math.pi)
        self._braid_phase = (self._braid_phase +
                             0.016 * (2 * math.pi / BRAID_DRIFT_S)) % (2 * math.pi)

        self.state_manager.update()

    def begin_collapse(self):
        """Start the release animation — the wave sinks into the baseline."""
        self._collapsing = True

    def reset(self):
        """Re-arm for the next show."""
        self._collapsing = False
        self.display[:] = 0.0
        self._recent_max = 0.0
        self._sparks = []

    # ------------------------ Drawing ------------------------

    def draw(self, cr: cairo.Context, width: int, height: int):
        pad_x = 18
        pad_top = 12
        baseline = height - 14
        max_rise = baseline - pad_top

        quiet = max(0.0, 1.0 - self._recent_max / QUIET_LEVEL) if not self._collapsing else 0.0
        breath = (0.5 + 0.5 * math.sin(self._breath_phase)) * 0.045 * quiet

        xs = np.linspace(pad_x, width - pad_x, NUM_POINTS)
        levels = np.clip(self.display + breath, 0.0, 1.0)
        ys = baseline - levels * max_rise

        # Braid offset: strands separate more where the wave is loud,
        # crossing where the sine passes zero. Slow drift keeps it alive.
        t = np.linspace(0, 1, NUM_POINTS)
        weave = np.sin(t * 2 * math.pi * BRAID_CYCLES + self._braid_phase)
        separation = weave * (2.0 + levels * 9.0)
        ys_a = ys + separation
        ys_b = ys - separation

        self._draw_lowpoly_fill(cr, xs, ys, baseline)

        # Back strand (gray) first, cream strand on top — the alpha dip at
        # crossings sells the over/under weave.
        self._draw_strand(cr, xs, ys_b, GRAY, 0.55, 1.8)
        self._draw_strand(cr, xs, ys_a, CREAM, 0.95, 2.2)

        self._draw_node_dots(cr, xs, ys, levels)
        self._draw_sparks(cr, xs, ys)

    def _draw_lowpoly_fill(self, cr: cairo.Context, xs, ys, baseline):
        """Sharp triangular facets between the curve and the baseline."""
        n = len(xs)
        shade_toggle = False
        for i in range(0, n - FACET_STEP, FACET_STEP):
            j = min(i + FACET_STEP, n - 1)
            x0, y0 = xs[i], ys[i]
            x1, y1 = xs[j], ys[j]

            # Facet pair: upper triangle (curve edge) + lower (baseline edge),
            # alternating shades for the crystalline look.
            for tri, alpha in (
                (((x0, y0), (x1, y1), (x0, baseline)), 0.10 if shade_toggle else 0.06),
                (((x1, y1), (x1, baseline), (x0, baseline)), 0.05 if shade_toggle else 0.09),
            ):
                cr.move_to(*tri[0])
                cr.line_to(*tri[1])
                cr.line_to(*tri[2])
                cr.close_path()
                cr.set_source_rgba(*CREAM, alpha)
                cr.fill()
            shade_toggle = not shade_toggle

    def _draw_strand(self, cr: cairo.Context, xs, ys, color, alpha, line_width):
        self._curve_path(cr, xs, ys)
        cr.set_source_rgba(*color, alpha)
        cr.set_line_width(line_width)
        cr.set_line_cap(cairo.LINE_CAP_ROUND)
        cr.set_line_join(cairo.LINE_JOIN_ROUND)
        cr.stroke()

    def _draw_node_dots(self, cr: cairo.Context, xs, ys, levels):
        for i in range(0, len(xs), DOT_STEP):
            r = 1.4 + levels[i] * 1.6
            cr.arc(xs[i], ys[i], r, 0, 2 * math.pi)
            cr.set_source_rgba(*CREAM, 0.35 + levels[i] * 0.55)
            cr.fill()

    def _draw_sparks(self, cr: cairo.Context, xs, ys):
        now = time.monotonic()
        for idx, birth in self._sparks:
            age = (now - birth) / SPARK_LIFE_S
            if age >= 1.0:
                continue
            # Rise and fade
            x = xs[idx]
            y = ys[idx] - 6 - age * 10
            size = 3.0 * (1.0 - age * 0.4)
            cr.move_to(x, y - size)
            cr.line_to(x + size, y)
            cr.line_to(x, y + size)
            cr.line_to(x - size, y)
            cr.close_path()
            cr.set_source_rgba(*CREAM, 0.9 * (1.0 - age))
            cr.fill()

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
