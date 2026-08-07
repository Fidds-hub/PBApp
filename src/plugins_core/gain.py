"""Input gain — a core stage, always on, always first in the chain."""

from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QLabel, QSlider, QVBoxLayout, QWidget

from pedalboard import Gain

SETTING = "gain_db"
MINIMUM_DB = -20
MAXIMUM_DB = 40
DEFAULT_DB = 0


class GainStage(QWidget):
    """A labelled dB slider driving a Gain plugin."""

    def __init__(self, settings: QSettings):
        super().__init__()
        self.settings = settings
        self.plugin = Gain(gain_db=0.0)

        self.label = QLabel()

        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(MINIMUM_DB, MAXIMUM_DB)
        self.slider.setTickPosition(QSlider.TicksBelow)
        self.slider.setTickInterval(10)
        self.slider.valueChanged.connect(self.on_changed)
        self.slider.setValue(int(settings.value(SETTING, DEFAULT_DB)))
        self.on_changed(self.slider.value())

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.label)
        layout.addWidget(self.slider)

    def on_changed(self, db: int) -> None:
        # Safe to change while the stream is running.
        self.plugin.gain_db = float(db)
        self.label.setText(f"Input gain:  {db:+d} dB")

    def save(self) -> None:
        self.settings.setValue(SETTING, self.slider.value())
