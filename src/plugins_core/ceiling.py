"""Output ceiling — a core stage, always on, always last in the chain.

A high-ratio Compressor rather than a Limiter: pedalboard's Limiter applies
makeup gain equal to -threshold, so lowering its threshold makes audio louder.
Compressor has no makeup stage, so this only ever turns things down.
"""

from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QLabel, QSlider, QVBoxLayout, QWidget

from pedalboard import Compressor

SETTING = "ceiling_db"
MINIMUM_DB = -24
MAXIMUM_DB = 0
DEFAULT_DB = -3

RATIO = 20.0
ATTACK_MS = 1.0
RELEASE_MS = 100.0


class CeilingStage(QWidget):
    """A labelled dBFS slider driving a brickwall-ish Compressor."""

    def __init__(self, settings: QSettings):
        super().__init__()
        self.settings = settings
        self.plugin = Compressor(
            threshold_db=float(DEFAULT_DB),
            ratio=RATIO,
            attack_ms=ATTACK_MS,
            release_ms=RELEASE_MS,
        )

        self.label = QLabel()

        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(MINIMUM_DB, MAXIMUM_DB)
        self.slider.valueChanged.connect(self.on_changed)
        self.slider.setValue(int(settings.value(SETTING, DEFAULT_DB)))
        self.on_changed(self.slider.value())

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.label)
        layout.addWidget(self.slider)

    def on_changed(self, db: int) -> None:
        self.plugin.threshold_db = float(db)
        self.label.setText(f"Output ceiling:  {db:d} dBFS")

    def save(self) -> None:
        self.settings.setValue(SETTING, self.slider.value())
