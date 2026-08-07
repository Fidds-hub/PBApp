"""Highpass filter — a core stage, first in the native chain.

Removes low-frequency energy that carries no speech: room rumble, HVAC, desk
thumps, mic-stand knocks, the low thud of plosives. Runs before the gate so
the gate's threshold sees voice rather than rumble.

Speech fundamentals start around 85 Hz, so a cutoff near 80-100 Hz is
inaudible on the voice while removing a lot of junk beneath it.
"""

from PySide6.QtCore import QSettings, Qt, Signal
from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QLabel, QSlider, QVBoxLayout, QWidget

from pedalboard import HighpassFilter

SETTING_ENABLED = "highpass_enabled"
SETTING_CUTOFF = "highpass_cutoff_hz"

DEFAULT_CUTOFF_HZ = 85
MINIMUM_HZ = 20
MAXIMUM_HZ = 300


class HighpassStage(QWidget):
    """Enable toggle plus a cutoff frequency slider."""

    changed = Signal()

    def __init__(self, settings: QSettings):
        super().__init__()
        self.settings = settings
        self.plugin = HighpassFilter(cutoff_frequency_hz=float(DEFAULT_CUTOFF_HZ))

        self.enabled = QCheckBox("Enabled")
        self.enabled.setChecked(settings.value(SETTING_ENABLED, "true") == "true")
        self.enabled.toggled.connect(self.changed)

        self.label = QLabel()
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(MINIMUM_HZ, MAXIMUM_HZ)
        self.slider.valueChanged.connect(self.on_changed)
        self.slider.setValue(int(settings.value(SETTING_CUTOFF, DEFAULT_CUTOFF_HZ)))
        self.on_changed(self.slider.value())

        row = QHBoxLayout()
        row.addWidget(self.enabled)
        row.addStretch()
        row.addWidget(self.label)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(row)
        layout.addWidget(self.slider)

    def on_changed(self, hz: int) -> None:
        self.plugin.cutoff_frequency_hz = float(hz)
        self.label.setText(f"Cutoff:  {hz:d} Hz")

    def is_enabled(self) -> bool:
        return self.enabled.isChecked()

    def save(self) -> None:
        self.settings.setValue(SETTING_ENABLED, "true" if self.enabled.isChecked() else "false")
        self.settings.setValue(SETTING_CUTOFF, self.slider.value())
