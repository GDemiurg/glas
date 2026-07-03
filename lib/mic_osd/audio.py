"""
Audio level source for mic-osd.

Reads the live level that the main hyprwhspr daemon already writes from its
capture stream (AUDIO_LEVEL_FILE) instead of opening a second microphone
stream. Keeps the original AudioMonitor API (start/stop/get_level/
get_samples) so the rest of mic-osd is unchanged; get_samples() returns a
rolling history of recent levels, which the waveform visualization renders
as a scrolling amplitude graph.
"""

import threading
from collections import deque
from pathlib import Path

import numpy as np

# Import paths with fallback for daemon context (same pattern as main.py)
try:
    from ..src.paths import AUDIO_LEVEL_FILE
except ImportError:
    try:
        from src.paths import AUDIO_LEVEL_FILE
    except ImportError:
        import os
        _runtime = Path(os.environ.get('XDG_RUNTIME_DIR', '/tmp'))
        AUDIO_LEVEL_FILE = _runtime / 'hyprwhspr' / 'audio_level'

# One appended sample per poll tick → constant scroll speed. 25 ms ticks with
# a 64-sample history ≈ 1.6 s of visible amplitude history.
POLL_INTERVAL_S = 0.025
HISTORY_LEN = 64


class AudioMonitor:
    """
    Level-file audio monitor (no second mic stream).

    The daemon writes a 0.0–1.0 level (already 10x-scaled RMS of the real
    capture) to AUDIO_LEVEL_FILE while recording. This class polls that file
    and keeps a rolling history for visualization.
    """

    def __init__(self, callback=None, samplerate=None, blocksize=None):
        # samplerate/blocksize accepted for API compatibility; unused.
        self.callback = callback
        self.running = False

        self._levels = deque([0.0] * HISTORY_LEN, maxlen=HISTORY_LEN)
        self._pitches = deque([0.5] * HISTORY_LEN, maxlen=HISTORY_LEN)
        self._current = 0.0
        self._thread = None
        self._stop = threading.Event()
        self._lock = threading.Lock()

    def _read_level(self):
        """Read 'level [pitch]' from the daemon's file; pitch defaults to 0.5
        (mid) for older single-value writers."""
        try:
            parts = AUDIO_LEVEL_FILE.read_text().split()
            level = max(0.0, min(1.0, float(parts[0])))
            pitch = max(0.0, min(1.0, float(parts[1]))) if len(parts) > 1 else 0.5
            return level, pitch
        except (FileNotFoundError, ValueError, OSError, IndexError):
            return 0.0, 0.5

    def _poll_loop(self):
        while not self._stop.is_set():
            level, pitch = self._read_level()
            with self._lock:
                self._current = level
                self._levels.append(level)
                self._pitches.append(pitch)
            if self.callback:
                try:
                    self.callback(level, self.get_samples())
                except Exception:
                    pass
            self._stop.wait(POLL_INTERVAL_S)

    def start(self, device=None):
        """Start polling the daemon's level file."""
        if self.running:
            return
        self._stop.clear()
        with self._lock:
            self._levels.extend([0.0] * HISTORY_LEN)
            self._current = 0.0
        self._thread = threading.Thread(
            target=self._poll_loop, daemon=True, name='mic-osd-level-poll')
        self._thread.start()
        self.running = True

    def stop(self):
        """Stop polling."""
        if not self.running:
            return
        self.running = False
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=0.2)
            self._thread = None
        with self._lock:
            self._current = 0.0
            self._levels.extend([0.0] * HISTORY_LEN)

    def get_level(self) -> float:
        """Current level, 0.0–1.0 (thread-safe)."""
        with self._lock:
            return self._current

    def get_samples(self) -> np.ndarray:
        """Rolling level history, oldest→newest (thread-safe)."""
        with self._lock:
            return np.array(self._levels)

    def get_pitch_samples(self) -> np.ndarray:
        """Rolling pitch history (0=low voice, 1=high), oldest→newest."""
        with self._lock:
            return np.array(self._pitches)

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop()
        return False
