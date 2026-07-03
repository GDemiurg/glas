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
PIXEL_CELL = 5             # pixel-fill cell size (px, includes 1px gap)
EDGE_FADE_FRAC = 0.16      # strands/fill fade in/out over this fraction of width

# Pitch → pixel tint ramp (low → mid → high voice)
ORANGE = (0.996, 0.502, 0.098)  # gruvbox orange #fe8019 — low pitch
YELLOW = (0.980, 0.741, 0.184)  # gruvbox yellow #fabd2f — mid pitch
AQUA = (0.557, 0.753, 0.486)    # gruvbox aqua #8ec07c — high pitch


class WaveVisualization(BaseVisualization):
    """Braided-strand wave of the real capture level."""

    def __init__(self):
        super().__init__()
        self.display = np.zeros(NUM_POINTS)
        self.pitch_display = np.full(NUM_POINTS, 0.5)
        # Adaptive pitch range: one speaker uses a narrow slice of the
        # 70–450Hz band, so stretch the observed range over the full palette.
        self._pitch_lo = 0.45
        self._pitch_hi = 0.55
        self._breath_phase = 0.0
        self._braid_phase = 0.0
        self._recent_max = 0.0
        self._collapsing = False
        self.state_manager = StateManager()

    # ------------------------ Data ------------------------

    def update(self, level: float, samples: np.ndarray = None,
               pitches: np.ndarray = None):
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

        if pitches is not None and len(pitches) > 1:
            idx = np.linspace(0, len(pitches) - 1, NUM_POINTS)
            pitch_target = np.interp(idx, np.arange(len(pitches)), pitches)

            # Widen the tracked range instantly, shrink it slowly — after a
            # few seconds of speech the palette spans the speaker's range.
            p_min, p_max = float(pitch_target.min()), float(pitch_target.max())
            self._pitch_lo = min(self._pitch_lo * 0.995 + p_min * 0.005, p_min)
            self._pitch_hi = max(self._pitch_hi * 0.995 + p_max * 0.005, p_max)
            span = max(self._pitch_hi - self._pitch_lo, 0.08)
            stretched = np.clip((pitch_target - self._pitch_lo) / span, 0.0, 1.0)

            self.pitch_display += (stretched - self.pitch_display) * LIQUID_EASE

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
        self.pitch_display[:] = 0.5
        self._pitch_lo, self._pitch_hi = 0.45, 0.55
        self._recent_max = 0.0

    # ------------------------ Drawing ------------------------

    def draw_background(self, cr: cairo.Context, width: int, height: int):
        """Feathered scrim: layered inset pills build alpha toward the
        center, so the edge fades out instead of showing a hard corner."""
        layers = 14
        step = 2
        # Layers composite multiplicatively: pick the per-layer alpha so the
        # stack reaches the theme's background alpha at the center.
        total = self.background_color[3] if len(self.background_color) == 4 else 0.24
        alpha = 1.0 - (1.0 - total) ** (1.0 / layers)
        rgb = self.background_color[:3]
        for i in range(layers):
            inset = i * step
            w = width - inset * 2
            h = height - inset * 2
            if w <= 0 or h <= 0:
                break
            r = h / 2  # full pill — no visible corners at any inset
            self._rounded_rect(cr, inset, inset, w, h, r)
            cr.set_source_rgba(*rgb, alpha)
            cr.fill()

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

        self._draw_pixel_fill(cr, xs, ys, levels, self.pitch_display, baseline, width)

        # Back strand (gray) first, cream strand on top; both fade out at
        # the left/right edges to match the feathered scrim.
        self._draw_strand(cr, xs, ys_b, GRAY, 0.55, 1.8, width)
        self._draw_strand(cr, xs, ys_a, CREAM, 0.95, 2.2, width)

    @staticmethod
    def _edge_fade(x, width):
        """0→1 ramp near the left/right edges, 1 in the middle."""
        fade_px = width * EDGE_FADE_FRAC
        return max(0.0, min(1.0, x / fade_px, (width - x) / fade_px))

    @staticmethod
    def _pitch_color(pitch):
        """Two-segment ramp: orange (low) → yellow (mid) → aqua (high)."""
        if pitch <= 0.5:
            t = pitch * 2.0
            lo, hi = ORANGE, YELLOW
        else:
            t = (pitch - 0.5) * 2.0
            lo, hi = YELLOW, AQUA
        return (lo[0] + (hi[0] - lo[0]) * t,
                lo[1] + (hi[1] - lo[1]) * t,
                lo[2] + (hi[2] - lo[2]) * t)

    def _draw_pixel_fill(self, cr: cairo.Context, xs, ys, levels, pitches,
                         baseline, width):
        """Pixel-grid fill under the curve: retro checker-dithered cells.
        Volume drives cell size/alpha (loud = fuller, brighter); pitch
        drives the tint — orange for a low voice, yellow mid, aqua high.
        Quiet columns stay near cream. Spans the full curve, edge to edge."""
        x_left, x_right = xs[0], xs[-1]
        cell = PIXEL_CELL
        col = 0
        x = x_left
        while x < x_right:
            cx = x + cell / 2
            # Curve height + local volume/pitch at this column
            y_curve = float(np.interp(cx, xs, ys))
            lvl = float(np.interp(cx, xs, levels))
            pitch = float(np.interp(cx, xs, pitches))
            fade_x = self._edge_fade(cx, width)

            # Volume → cell inflation (gap closes when loud)
            inset = 1.0 - min(lvl * 1.6, 1.0)
            # Pitch → hue; full saturation already at normal speech volume,
            # only near-silence stays cream
            pr, pg, pb = self._pitch_color(pitch)
            t = min(lvl * 3.5, 1.0)
            r = CREAM[0] + (pr - CREAM[0]) * t
            g = CREAM[1] + (pg - CREAM[1]) * t
            b = CREAM[2] + (pb - CREAM[2]) * t

            row = 0
            y = baseline - cell
            while y + cell > y_curve:
                # Fade toward the curve top; checker dither for texture
                depth = (baseline - y) / max(baseline - y_curve, 1e-6)
                a = (0.34 + lvl * 0.25) * (1.0 - depth * 0.55)
                if (col + row) % 2:
                    a *= 0.6
                cr.rectangle(x, max(y, y_curve), cell - inset, cell - inset)
                cr.set_source_rgba(r, g, b, a * fade_x)
                cr.fill()
                y -= cell
                row += 1
            x += cell
            col += 1

    def _draw_strand(self, cr: cairo.Context, xs, ys, color, alpha, line_width, width):
        # Horizontal alpha ramp so the strand dissolves at the edges,
        # mirroring the feathered scrim.
        grad = cairo.LinearGradient(0, 0, width, 0)
        grad.add_color_stop_rgba(0.0, *color, 0.0)
        grad.add_color_stop_rgba(EDGE_FADE_FRAC, *color, alpha)
        grad.add_color_stop_rgba(1.0 - EDGE_FADE_FRAC, *color, alpha)
        grad.add_color_stop_rgba(1.0, *color, 0.0)
        self._curve_path(cr, xs, ys)
        cr.set_source(grad)
        cr.set_line_width(line_width)
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
