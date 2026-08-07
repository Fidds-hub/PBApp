"""Vertical level meter — input beside output, so filtering is visible.

Each side draws two bars: RMS (dimmer, average loudness) and peak (brighter,
instantaneous maximum). Comparing IN against OUT shows what the chain did:
gain lifts OUT, the gate drops it to nothing in silence, the ceiling flattens
its top while IN keeps climbing.
"""

import math

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

DB_FLOOR = -60.0
DB_CEILING = 0.0
SCALE_MARKS = [-48, -36, -24, -12, -6, -3, 0]
REFRESH_MS = 50


def to_db(linear: float) -> float:
    if linear <= 0.0:
        return DB_FLOOR
    return max(DB_FLOOR, 20.0 * math.log10(linear))


def db_to_offset(db: float, height: int) -> int:
    """Pixels down from the top of a bar for a given dB value."""
    fraction = (db - DB_FLOOR) / (DB_CEILING - DB_FLOOR)
    return int(height * (1.0 - max(0.0, min(1.0, fraction))))


class LevelMeter(QWidget):
    """Draws input and output levels from an engine's MeterState."""

    def __init__(self, meter, parent=None):
        super().__init__(parent)
        self.meter = meter
        self.levels = [DB_FLOOR] * 4  # in_peak, in_rms, out_peak, out_rms

        self.setMinimumWidth(96)
        self.setMaximumWidth(120)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(REFRESH_MS)

    def tick(self) -> None:
        if self.meter is None:
            return
        in_peak, in_rms, out_peak, out_rms = self.meter.read()
        self.levels = [to_db(in_peak), to_db(in_rms), to_db(out_peak), to_db(out_rms)]
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        width, height = self.width(), self.height()
        painter.fillRect(0, 0, width, height, QColor(24, 25, 27))

        label_space = 18
        scale_space = 22
        bar_height = height - label_space - 6
        if bar_height < 20:
            painter.end()
            return

        bar_top = label_space
        bar_width = 12
        gap = 3
        group_gap = 14

        group_width = bar_width * 2 + gap
        total = group_width * 2 + group_gap
        left = scale_space + max(0, (width - scale_space - total) // 2)

        font = painter.font()
        font.setPointSize(8)
        painter.setFont(font)

        # dB scale down the left edge.
        for db in SCALE_MARKS:
            y = bar_top + db_to_offset(db, bar_height)
            painter.setPen(QPen(QColor(200, 200, 200, 40), 1))
            painter.drawLine(scale_space, y, width, y)
            painter.setPen(QColor(150, 152, 155))
            painter.drawText(1, y + 3, f"{db}")

        groups = [("IN", self.levels[1], self.levels[0]), ("OUT", self.levels[3], self.levels[2])]
        for index, (title, rms_db, peak_db) in enumerate(groups):
            x = left + index * (group_width + group_gap)

            painter.setPen(QColor(200, 202, 205))
            painter.drawText(x, label_space - 5, title)

            self.draw_bar(painter, x, bar_top, bar_width, bar_height, rms_db, 170)
            self.draw_bar(painter, x + bar_width + gap, bar_top, bar_width, bar_height, peak_db, 255)

        painter.end()

    def draw_bar(self, painter, x, top, width, height, db, alpha) -> None:
        painter.fillRect(x, top, width, height, QColor(38, 39, 42))

        offset = db_to_offset(db, height)
        filled = height - offset
        if filled <= 0:
            return

        # Gradient spans the whole bar, so colour tracks absolute level.
        gradient = QLinearGradient(0, top + height, 0, top)
        gradient.setColorAt(0.0, QColor(50, 200, 50, alpha))
        gradient.setColorAt(0.6, QColor(70, 205, 60, alpha))
        gradient.setColorAt(0.8, QColor(230, 190, 50, alpha))
        gradient.setColorAt(1.0, QColor(230, 60, 50, alpha))

        painter.fillRect(x, top + offset, width, filled, gradient)
