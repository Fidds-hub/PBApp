"""Noise gate — a core stage, after denoise and before gain.

Sits pre-gain deliberately, so the threshold is measured against the raw mic
level rather than the boosted one (matching the old Audio_Layer chain, where
a -48 dB threshold sat ahead of +20 dB of gain).

Attack and ratio are fixed: a gate wants a fast attack so speech onsets aren't
clipped, and a high ratio so it gates rather than expands. Threshold and
release are the two that actually need tuning by ear.
"""

from PySide6.QtCore import QSettings, Qt, Signal
from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QLabel, QSlider, QVBoxLayout, QWidget

from pedalboard import NoiseGate

SETTING_ENABLED = "gate_enabled"
SETTING_THRESHOLD = "gate_threshold_db"
SETTING_RELEASE = "gate_release_ms"

DEFAULT_THRESHOLD_DB = -48
DEFAULT_RELEASE_MS = 150

RATIO = 10.0
ATTACK_MS = 2.0


class GateStage(QWidget):
    """Enable toggle plus threshold and release controls for a NoiseGate."""

    changed = Signal()

    def __init__(self, settings: QSettings):
        super().__init__()
        self.settings = settings
        self.plugin = NoiseGate(
            threshold_db=float(DEFAULT_THRESHOLD_DB),
            ratio=RATIO,
            attack_ms=ATTACK_MS,
            release_ms=float(DEFAULT_RELEASE_MS),
        )

        self.enabled = QCheckBox("Enabled")
        self.enabled.setChecked(settings.value(SETTING_ENABLED, "true") == "true")
        self.enabled.toggled.connect(self.changed)

        self.threshold_label = QLabel()
        self.threshold = QSlider(Qt.Horizontal)
        self.threshold.setRange(-90, 0)  # dB
        self.threshold.valueChanged.connect(self.on_threshold)
        self.threshold.setValue(int(settings.value(SETTING_THRESHOLD, DEFAULT_THRESHOLD_DB)))
        self.on_threshold(self.threshold.value())

        self.release_label = QLabel()
        self.release = QSlider(Qt.Horizontal)
        self.release.setRange(5, 1000)  # ms
        self.release.valueChanged.connect(self.on_release)
        self.release.setValue(int(settings.value(SETTING_RELEASE, DEFAULT_RELEASE_MS)))
        self.on_release(self.release.value())

        row = QHBoxLayout()
        row.addWidget(self.enabled)
        row.addStretch()
        row.addWidget(self.threshold_label)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(row)
        layout.addWidget(self.threshold)
        layout.addWidget(self.release_label)
        layout.addWidget(self.release)

    def on_threshold(self, db: int) -> None:
        self.plugin.threshold_db = float(db)
        self.threshold_label.setText(f"Threshold:  {db:d} dB")

    def on_release(self, ms: int) -> None:
        self.plugin.release_ms = float(ms)
        self.release_label.setText(f"Gate release:  {ms:d} ms")

    def is_enabled(self) -> bool:
        return self.enabled.isChecked()

    def save(self) -> None:
        self.settings.setValue(SETTING_ENABLED, "true" if self.enabled.isChecked() else "false")
        self.settings.setValue(SETTING_THRESHOLD, self.threshold.value())
        self.settings.setValue(SETTING_RELEASE, self.release.value())
