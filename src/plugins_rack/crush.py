"""Lo-fi crusher — a rack effect, built piece by piece.

Target sound: late-80s/early-90s digitised game speech (Street Fighter,
Duke Nukem). That character comes mostly from playback at a low sample rate,
not from bit depth: at 8 kHz everything above 4 kHz is gone, and anything that
was up there folds back down as aliasing, which is the "crunch".

Stage one is sample rate reduction only. Bit depth, saturation and band
limiting come later.

The reduction is sample-and-hold with an integer divisor, which is what the
hardware of that era actually did: hold each sample for N output samples so
the effective rate is 48000 / N. No anti-alias filtering, deliberately -- the
aliasing is the sound.
"""

import numpy as np
from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QLabel, QSlider, QVBoxLayout, QWidget

NAME = "Crusher"

SOURCE_RATE = 48000
MINIMUM_DIVISOR = 1  # 48 kHz, untouched
MAXIMUM_DIVISOR = 12  # 4 kHz; past this speech stops being intelligible
DEFAULT_DIVISOR = 10  # 4.8 kHz, chosen by ear against 80s arcade speech


class Crusher:
    """Sample-and-hold rate reduction with state carried across blocks."""

    def __init__(self):
        self.divisor = DEFAULT_DIVISOR
        self.reset()

    def reset(self) -> None:
        self.position = 0  # absolute sample count, for continuous grouping
        self.held = 0.0  # value carried over when a block starts mid-hold

    def process(self, audio: np.ndarray, sample_rate: float) -> np.ndarray:
        """Crush a (frames, channels) block. Returns the same shape."""
        divisor = int(self.divisor)
        if divisor <= 1:
            return audio

        n, channels = audio.shape
        mono = audio[:, 0].astype(np.float32, copy=False)

        # Which hold-group each sample belongs to. Using an absolute position
        # keeps groups continuous across block boundaries.
        positions = self.position + np.arange(n, dtype=np.int64)
        groups = positions // divisor

        # A new group starts wherever the group index changes.
        starts = np.empty(n, dtype=bool)
        starts[0] = positions[0] % divisor == 0
        starts[1:] = groups[1:] != groups[:-1]

        # Forward-fill: every sample takes the value from its group's start.
        sources = np.where(starts, np.arange(n), -1)
        sources = np.maximum.accumulate(sources)

        out = np.where(sources >= 0, mono[np.clip(sources, 0, None)], self.held)

        self.position += n
        self.held = float(out[-1])

        return np.repeat(out.astype(np.float32).reshape(-1, 1), channels, axis=1)


class CrusherStage(QWidget):
    """Rate slider. Enable/disable and ordering come from the rack row."""

    name = NAME

    def __init__(self, settings: QSettings | None = None):
        super().__init__()
        self.crusher = Crusher()

        self.rate_label = QLabel()
        self.rate_slider = QSlider(Qt.Horizontal)
        self.rate_slider.setRange(MINIMUM_DIVISOR, MAXIMUM_DIVISOR)
        self.rate_slider.setTickPosition(QSlider.TicksBelow)
        self.rate_slider.setTickInterval(1)
        self.rate_slider.setPageStep(1)
        self.rate_slider.valueChanged.connect(self.on_rate_changed)
        self.rate_slider.setValue(DEFAULT_DIVISOR)
        self.on_rate_changed(DEFAULT_DIVISOR)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.rate_label)
        layout.addWidget(self.rate_slider)

    def on_rate_changed(self, divisor: int) -> None:
        self.crusher.divisor = divisor
        rate = SOURCE_RATE / divisor
        self.rate_label.setText(f"Sample rate:  {rate:,.0f} Hz   (÷{divisor})")

    def process(self, audio: np.ndarray, sample_rate: float) -> np.ndarray:
        return self.crusher.process(audio, sample_rate)

    def params(self) -> dict:
        return {"divisor": self.rate_slider.value()}

    def load(self, params: dict) -> None:
        if "divisor" in params:
            self.rate_slider.setValue(int(params["divisor"]))
