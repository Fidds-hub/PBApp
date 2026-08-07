"""Pitch shift via SoundTouch — a rack effect.

Unlike the hand-written delay-line shifter, SoundTouch keeps continuous
internal state across calls, so there are no grain wraps to hide and no chunk
boundaries to splice. That removes the whole class of artifacts we were
tuning around: doubling, robotic buzzing, and the pops from switching grain
lengths mid-stream.

The one thing it does not promise is symmetry: putSamples() and
receiveSamples() are decoupled, and it hands back whatever it happens to have
ready. A small output FIFO absorbs that, the same way the RNNoise wrapper
handles its fixed 480-sample frames.
"""

import ctypes
from pathlib import Path

import numpy as np
from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import (
    QDoubleSpinBox,
    QGridLayout,
    QLabel,
    QSlider,
    QVBoxLayout,
    QWidget,
)

NAME = "Pitch shift"

DLL_PATH = Path(__file__).resolve().parents[2] / "assets" / "soundtouchdll_x64.dll"

STEPS_PER_SEMITONE = 10
RANGE_SEMITONES = 12

PRESETS = {"low": -5.0, "default": 0.0, "high": 5.0}

# SoundTouch setting ids, from SoundTouch.h.
SETTING_USE_AA_FILTER = 0
SETTING_AA_FILTER_LENGTH = 1
SETTING_USE_QUICKSEEK = 2
SETTING_SEQUENCE_MS = 3
SETTING_SEEKWINDOW_MS = 4
SETTING_OVERLAP_MS = 5

# Tuned for speech at low latency rather than SoundTouch's music-oriented
# defaults: shorter sequences track a moving voice better.
SEQUENCE_MS = 40
SEEKWINDOW_MS = 15
OVERLAP_MS = 8

RECEIVE_CAPACITY = 8192  # scratch buffer for one receiveSamples() call


class SoundTouchShifter:
    """One SoundTouch instance with an output FIFO."""

    def __init__(self, sample_rate: int = 48000, dll_path: Path = DLL_PATH):
        self.lib = ctypes.cdll.LoadLibrary(str(dll_path))
        self.bind()

        self.handle = self.lib.soundtouch_createInstance()
        if not self.handle:
            raise RuntimeError("soundtouch_createInstance returned null")

        self.lib.soundtouch_setSampleRate(self.handle, int(sample_rate))
        self.lib.soundtouch_setChannels(self.handle, 1)
        self.lib.soundtouch_setSetting(self.handle, SETTING_SEQUENCE_MS, SEQUENCE_MS)
        self.lib.soundtouch_setSetting(self.handle, SETTING_SEEKWINDOW_MS, SEEKWINDOW_MS)
        self.lib.soundtouch_setSetting(self.handle, SETTING_OVERLAP_MS, OVERLAP_MS)
        self.lib.soundtouch_setSetting(self.handle, SETTING_USE_AA_FILTER, 1)

        self.scratch = np.zeros(RECEIVE_CAPACITY, dtype=np.float32)
        self.semitones = 0.0
        self.reset()

    def bind(self) -> None:
        """Declare argument and return types; ctypes assumes int otherwise."""
        handle = ctypes.c_void_p
        floats = ctypes.POINTER(ctypes.c_float)

        self.lib.soundtouch_createInstance.restype = handle
        self.lib.soundtouch_createInstance.argtypes = []

        self.lib.soundtouch_destroyInstance.argtypes = [handle]
        self.lib.soundtouch_destroyInstance.restype = None

        self.lib.soundtouch_setSampleRate.argtypes = [handle, ctypes.c_uint]
        self.lib.soundtouch_setChannels.argtypes = [handle, ctypes.c_uint]
        self.lib.soundtouch_setSetting.argtypes = [handle, ctypes.c_int, ctypes.c_int]
        self.lib.soundtouch_setPitchSemiTones.argtypes = [handle, ctypes.c_float]

        self.lib.soundtouch_putSamples.argtypes = [handle, floats, ctypes.c_uint]
        self.lib.soundtouch_putSamples.restype = None

        self.lib.soundtouch_receiveSamples.argtypes = [handle, floats, ctypes.c_uint]
        self.lib.soundtouch_receiveSamples.restype = ctypes.c_uint

        self.lib.soundtouch_numSamples.argtypes = [handle]
        self.lib.soundtouch_numSamples.restype = ctypes.c_uint

        self.lib.soundtouch_clear.argtypes = [handle]
        self.lib.soundtouch_clear.restype = None

    @property
    def semitones(self) -> float:
        return self._semitones

    @semitones.setter
    def semitones(self, value: float) -> None:
        self._semitones = float(value)
        self.lib.soundtouch_setPitchSemiTones(self.handle, ctypes.c_float(float(value)))

    def reset(self) -> None:
        self.lib.soundtouch_clear(self.handle)
        self.ready = np.zeros(0, dtype=np.float32)

    def process(self, audio: np.ndarray, sample_rate: float) -> np.ndarray:
        """Shift a (frames, channels) block. Returns the same shape."""
        if self._semitones == 0.0:
            return audio

        n, channels = audio.shape
        mono = np.ascontiguousarray(audio[:, 0], dtype=np.float32)

        self.lib.soundtouch_putSamples(
            self.handle, mono.ctypes.data_as(ctypes.POINTER(ctypes.c_float)), n
        )

        # Drain everything available; it rarely matches what we put in.
        while True:
            got = self.lib.soundtouch_receiveSamples(
                self.handle,
                self.scratch.ctypes.data_as(ctypes.POINTER(ctypes.c_float)),
                RECEIVE_CAPACITY,
            )
            if got == 0:
                break
            chunk = self.scratch[:got].copy()
            self.ready = np.concatenate([self.ready, chunk]) if self.ready.size else chunk

        if self.ready.shape[0] >= n:
            out = self.ready[:n]
            self.ready = self.ready[n:].copy()
        else:
            # Still filling: pad with silence rather than returning short.
            out = np.zeros(n, dtype=np.float32)
            out[: self.ready.shape[0]] = self.ready
            self.ready = np.zeros(0, dtype=np.float32)

        return np.repeat(out.reshape(-1, 1), channels, axis=1)

    def __del__(self):
        try:
            if getattr(self, "handle", None):
                self.lib.soundtouch_destroyInstance(self.handle)
        except Exception:
            pass


class SoundTouchStage(QWidget):
    """A semitone slider. Enable/disable and ordering come from the rack row."""

    name = NAME

    def __init__(self, settings: QSettings | None = None):
        super().__init__()
        self.shifter = SoundTouchShifter()

        self.label = QLabel()
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(
            -RANGE_SEMITONES * STEPS_PER_SEMITONE, RANGE_SEMITONES * STEPS_PER_SEMITONE
        )
        self.slider.setTickPosition(QSlider.TicksBelow)
        self.slider.setTickInterval(STEPS_PER_SEMITONE * 6)
        self.slider.valueChanged.connect(self.on_changed)
        self.slider.setValue(0)
        self.on_changed(0)

        self.presets: dict[str, QDoubleSpinBox] = {}
        presets = QGridLayout()
        presets.setContentsMargins(0, 4, 0, 0)
        for column, (key, default) in enumerate(PRESETS.items()):
            box = QDoubleSpinBox()
            box.setRange(-RANGE_SEMITONES, RANGE_SEMITONES)
            box.setSingleStep(0.1)
            box.setDecimals(1)
            box.setSuffix(" st")
            box.setValue(default)

            presets.addWidget(QLabel(key.capitalize()), 0, column)
            presets.addWidget(box, 1, column)
            self.presets[key] = box

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.label)
        layout.addWidget(self.slider)
        layout.addLayout(presets)

    def apply_preset(self, key: str) -> None:
        box = self.presets.get(key)
        if box is not None:
            self.slider.setValue(int(round(box.value() * STEPS_PER_SEMITONE)))

    def on_changed(self, steps: int) -> None:
        semitones = steps / STEPS_PER_SEMITONE
        self.shifter.semitones = float(semitones)
        self.label.setText(f"Pitch:  {semitones:+.1f} semitones")

    def process(self, audio: np.ndarray, sample_rate: float) -> np.ndarray:
        return self.shifter.process(audio, sample_rate)

    def params(self) -> dict:
        return {
            "semitones": self.slider.value(),
            "presets": {key: box.value() for key, box in self.presets.items()},
        }

    def load(self, params: dict) -> None:
        for key, value in params.get("presets", {}).items():
            if key in self.presets:
                self.presets[key].setValue(float(value))

        if "semitones" in params:
            self.slider.setValue(int(params["semitones"]))
