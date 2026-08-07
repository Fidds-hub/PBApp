"""Compressor — a core stage, between the gate and the gain.

Evens out the distance between quiet and loud speech. It only ever turns loud
parts down; the Input gain stage that follows is what brings everything back
up, which is what actually lifts the quiet parts. Compression without that
makeup gain just sounds quieter and duller.

Distinct from the Output ceiling, which is the same plugin at an extreme
setting doing a different job: this one shapes, that one protects.
"""

from PySide6.QtCore import QSettings, Qt, Signal
from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QLabel, QSlider, QVBoxLayout, QWidget

from pedalboard import Compressor

SETTING_ENABLED = "comp_enabled"
SETTING_THRESHOLD = "comp_threshold_db"
SETTING_RATIO = "comp_ratio"
SETTING_ATTACK = "comp_attack_ms"
SETTING_RELEASE = "comp_release_ms"

DEFAULT_THRESHOLD_DB = -18
DEFAULT_RATIO = 3
DEFAULT_ATTACK_MS = 10
DEFAULT_RELEASE_MS = 100


class CompressorStage(QWidget):
    """Enable toggle plus threshold, ratio, attack and release."""

    changed = Signal()

    def __init__(self, settings: QSettings):
        super().__init__()
        self.settings = settings
        self.plugin = Compressor(
            threshold_db=float(DEFAULT_THRESHOLD_DB),
            ratio=float(DEFAULT_RATIO),
            attack_ms=float(DEFAULT_ATTACK_MS),
            release_ms=float(DEFAULT_RELEASE_MS),
        )

        self.enabled = QCheckBox("Enabled")
        self.enabled.setChecked(settings.value(SETTING_ENABLED, "true") == "true")
        self.enabled.toggled.connect(self.changed)

        self.threshold_label = QLabel()
        self.threshold = self.build_slider(
            -60, 0, SETTING_THRESHOLD, DEFAULT_THRESHOLD_DB, self.on_threshold
        )

        self.ratio_label = QLabel()
        self.ratio = self.build_slider(1, 20, SETTING_RATIO, DEFAULT_RATIO, self.on_ratio)

        self.attack_label = QLabel()
        self.attack = self.build_slider(1, 100, SETTING_ATTACK, DEFAULT_ATTACK_MS, self.on_attack)

        self.release_label = QLabel()
        self.release = self.build_slider(
            10, 1000, SETTING_RELEASE, DEFAULT_RELEASE_MS, self.on_release
        )

        row = QHBoxLayout()
        row.addWidget(self.enabled)
        row.addStretch()
        row.addWidget(self.threshold_label)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(row)
        layout.addWidget(self.threshold)
        layout.addWidget(self.ratio_label)
        layout.addWidget(self.ratio)
        layout.addWidget(self.attack_label)
        layout.addWidget(self.attack)
        layout.addWidget(self.release_label)
        layout.addWidget(self.release)

    def build_slider(self, low, high, key, default, handler) -> QSlider:
        slider = QSlider(Qt.Horizontal)
        slider.setRange(low, high)
        slider.valueChanged.connect(handler)
        slider.setValue(int(self.settings.value(key, default)))
        handler(slider.value())
        return slider

    def on_threshold(self, db: int) -> None:
        self.plugin.threshold_db = float(db)
        self.threshold_label.setText(f"Threshold:  {db:d} dB")

    def on_ratio(self, ratio: int) -> None:
        self.plugin.ratio = float(ratio)
        self.ratio_label.setText(f"Ratio:  {ratio:d}:1")

    def on_attack(self, ms: int) -> None:
        self.plugin.attack_ms = float(ms)
        self.attack_label.setText(f"Attack:  {ms:d} ms")

    def on_release(self, ms: int) -> None:
        self.plugin.release_ms = float(ms)
        self.release_label.setText(f"Release:  {ms:d} ms")

    def is_enabled(self) -> bool:
        return self.enabled.isChecked()

    def save(self) -> None:
        self.settings.setValue(SETTING_ENABLED, "true" if self.enabled.isChecked() else "false")
        self.settings.setValue(SETTING_THRESHOLD, self.threshold.value())
        self.settings.setValue(SETTING_RATIO, self.ratio.value())
        self.settings.setValue(SETTING_ATTACK, self.attack.value())
        self.settings.setValue(SETTING_RELEASE, self.release.value())
