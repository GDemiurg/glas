"""
Visualization modules for mic-osd.
"""

from .base import BaseVisualization
from .vu_meter import VUMeterVisualization
from .waveform import WaveformVisualization
from .wave import WaveVisualization

VISUALIZATIONS = {
    "vu_meter": VUMeterVisualization,
    "waveform": WaveformVisualization,
    "wave": WaveVisualization,
}

__all__ = [
    "BaseVisualization",
    "VUMeterVisualization",
    "WaveformVisualization",
    "WaveVisualization",
    "VISUALIZATIONS",
]
