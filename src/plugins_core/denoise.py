"""RNNoise denoiser — a core stage, first in the chain, before gain.

Wraps the bundled rnnoise.dll via ctypes. RNNoise is fixed at 48 kHz mono
with 480-sample frames, so this buffers whatever block size the stream
hands us and carries the remainder across calls.
"""

import ctypes
from pathlib import Path

import numpy as np
from PySide6.QtCore import QSettings, Qt, Signal
from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QLabel, QSlider, QVBoxLayout, QWidget

FRAME = 480
SAMPLE_RATE = 48000
DLL_PATH = Path(__file__).resolve().parents[2] / "assets" / "rnnoise.dll"

SETTING_MIX = "denoise_mix"
SETTING_ENABLED = "denoise_enabled"
DEFAULT_MIX = 100  # percent


class RNNoise:
    """One RNNoise instance, with its own state and buffers.

    Deliberately not a pedalboard.Plugin — that class is abstract and cannot be
    subclassed from Python, so this runs in the engine's Python loop instead.
    """

    def __init__(self, dll_path: Path = DLL_PATH):
        self.lib = ctypes.cdll.LoadLibrary(str(dll_path))

        self.lib.rnnoise_create.restype = ctypes.c_void_p
        try:
            self.lib.rnnoise_create.argtypes = [ctypes.c_void_p]
            self.state = self.lib.rnnoise_create(None)
        except Exception:
            self.lib.rnnoise_create.argtypes = []
            self.state = self.lib.rnnoise_create()

        if hasattr(self.lib, "rnnoise_process_frame"):
            self.proc = self.lib.rnnoise_process_frame
        elif hasattr(self.lib, "rnnoise_process"):
            self.proc = self.lib.rnnoise_process
        else:
            raise RuntimeError("rnnoise.dll missing rnnoise_process[_frame]")

        self.proc.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_float),
            ctypes.POINTER(ctypes.c_float),
        ]
        self.proc.restype = ctypes.c_float

        self.lib.rnnoise_destroy.argtypes = [ctypes.c_void_p]
        self.lib.rnnoise_destroy.restype = None

        self.mix = 1.0
        self.reset()

    def reset(self) -> None:
        self.pending = np.zeros(0, dtype=np.float32)
        self.ready = np.zeros(0, dtype=np.float32)

    def process(self, audio: np.ndarray, sample_rate: float) -> np.ndarray:
        """Denoise a (frames, channels) block. Pass through if not 48 kHz."""
        if sample_rate != SAMPLE_RATE:
            return audio

        n, channels = audio.shape
        mono = audio[:, 0].astype(np.float32, copy=False)

        self.pending = np.concatenate([self.pending, mono]) if self.pending.size else mono.copy()

        # RNNoise works on int16-range floats, 480 samples at a time.
        frames = self.pending.shape[0] // FRAME
        if frames:
            usable = frames * FRAME
            work = (self.pending[:usable] * 32768.0).astype(np.float32, copy=True)

            for i in range(frames):
                start = i * FRAME
                frame = np.ascontiguousarray(work[start : start + FRAME])
                ptr = frame.ctypes.data_as(ctypes.POINTER(ctypes.c_float))
                self.proc(self.state, ptr, ptr)
                work[start : start + FRAME] = frame

            work /= 32768.0
            self.ready = np.concatenate([self.ready, work]) if self.ready.size else work
            self.pending = self.pending[usable:].copy()

        # Hand back n samples; pad with silence until the first frame fills.
        if self.ready.shape[0] >= n:
            out = self.ready[:n]
            self.ready = self.ready[n:].copy()
        else:
            out = np.zeros(n, dtype=np.float32)
            out[: self.ready.shape[0]] = self.ready
            self.ready = np.zeros(0, dtype=np.float32)

        if self.mix < 1.0:
            out = mono[:n] * (1.0 - self.mix) + out * self.mix

        return np.repeat(out.reshape(-1, 1), channels, axis=1)

    def __del__(self):
        try:
            if getattr(self, "state", None):
                self.lib.rnnoise_destroy(self.state)
        except Exception:
            pass


class DenoiseStage(QWidget):
    """Enable toggle plus a dry/wet mix slider for RNNoise."""

    changed = Signal()

    def __init__(self, settings: QSettings):
        super().__init__()
        self.settings = settings

        # A missing or unloadable DLL disables this stage rather than taking
        # the whole app down at startup.
        try:
            self.plugin = RNNoise()
            self.error = None
        except Exception as e:
            self.plugin = None
            self.error = e

        self.enabled = QCheckBox("Enabled")
        self.enabled.setChecked(
            self.plugin is not None and settings.value(SETTING_ENABLED, "true") == "true"
        )
        self.enabled.setEnabled(self.plugin is not None)
        self.enabled.toggled.connect(self.changed)

        if self.plugin is None:
            self.enabled.setText("Unavailable")
            self.enabled.setToolTip(f"{DLL_PATH} could not be loaded:\n{self.error}")

        self.label = QLabel()
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, 100)  # percent wet
        self.slider.valueChanged.connect(self.on_changed)
        self.slider.setValue(int(settings.value(SETTING_MIX, DEFAULT_MIX)))
        self.on_changed(self.slider.value())

        row = QHBoxLayout()
        row.addWidget(self.enabled)
        row.addStretch()
        row.addWidget(self.label)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(row)
        layout.addWidget(self.slider)

    def on_changed(self, percent: int) -> None:
        if self.plugin is not None:
            self.plugin.mix = percent / 100.0
        self.label.setText(f"Dry/wet:  {percent:d}%")

    def is_enabled(self) -> bool:
        return self.plugin is not None and self.enabled.isChecked()

    def process(self, audio: np.ndarray, sample_rate: float) -> np.ndarray:
        """Called by the audio thread every block."""
        if self.plugin is None or not self.enabled.isChecked():
            return audio
        return self.plugin.process(audio, sample_rate)

    def save(self) -> None:
        self.settings.setValue(SETTING_MIX, self.slider.value())
        self.settings.setValue(SETTING_ENABLED, "true" if self.enabled.isChecked() else "false")
