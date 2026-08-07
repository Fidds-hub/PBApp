"""Stop the scroll wheel from changing control values.

Qt delivers wheel events to whatever sits under the cursor, so scrolling down
a page of sliders silently edits every one you pass over. This filter takes
wheel events away from value controls and hands them to the enclosing scroll
area instead, so the page scrolls and the values stay put.

Controls can still be adjusted by dragging, clicking, or arrow keys.
"""

from PySide6.QtCore import QEvent, QObject
from PySide6.QtWidgets import QAbstractSlider, QAbstractSpinBox, QComboBox, QScrollArea

GUARDED = (QAbstractSlider, QAbstractSpinBox, QComboBox)

# Pixels scrolled per wheel notch. A scroll area's singleStep can be as low as
# one pixel, so this is set explicitly rather than derived from it.
PIXELS_PER_NOTCH = 60


class WheelGuard(QObject):
    """Application-wide event filter redirecting wheel events off controls."""

    def eventFilter(self, target: QObject, event: QEvent) -> bool:
        if event.type() != QEvent.Wheel or not isinstance(target, GUARDED):
            return False

        # Scroll the nearest enclosing scroll area by hand. Re-sending the event
        # to the viewport does not work (it arrives already accepted), so drive
        # the scrollbar directly.
        parent = target.parent()
        while parent is not None:
            if isinstance(parent, QScrollArea):
                bar = parent.verticalScrollBar()
                notches = event.angleDelta().y() / 120.0
                bar.setValue(bar.value() - int(notches * PIXELS_PER_NOTCH))
                return True
            parent = parent.parent()

        # No scroll area above it (e.g. the device pickers): just swallow it.
        return True


def install(app) -> WheelGuard:
    """Install the guard and return it (the caller must keep a reference)."""
    guard = WheelGuard(app)
    app.installEventFilter(guard)
    return guard
