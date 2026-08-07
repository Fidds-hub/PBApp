"""System tray icon — keeps the app alive when the window is closed.

Qt's own QSystemTrayIcon runs on the GUI thread, so unlike a pystray-based
tray there is no background thread and no signal bridging: menu actions are
ordinary Qt slots.

Closing the window hides it. Quitting is deliberate, from the tray menu.
"""

from pathlib import Path

from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

ICON_PATH = Path(__file__).resolve().parents[2] / "assets" / "icon.ico"


def build_icon() -> QIcon:
    """The bundled icon if there is one, otherwise a simple drawn placeholder."""
    if ICON_PATH.exists():
        return QIcon(str(ICON_PATH))

    pixmap = QPixmap(64, 64)
    pixmap.fill(QColor(0, 0, 0, 0))

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QColor(88, 130, 200))
    painter.setBrush(QColor(88, 130, 200))
    painter.drawRoundedRect(8, 8, 48, 48, 10, 10)

    # Three sliders, echoing the app's layout.
    painter.setPen(QColor(235, 236, 238))
    for index, offset in enumerate((20, 32, 44)):
        painter.drawLine(18, offset, 46, offset)
        knob = 24 + index * 8
        painter.drawEllipse(knob - 3, offset - 3, 6, 6)
    painter.end()

    return QIcon(pixmap)


class Tray(QSystemTrayIcon):
    """Tray icon with Open and Exit, wired to a window."""

    def __init__(self, window):
        super().__init__(build_icon(), window)
        self.window = window
        self.setToolTip("Pedalboard")

        menu = QMenu()

        self.open_action = QAction("Open", menu)
        self.open_action.triggered.connect(self.open_window)
        menu.addAction(self.open_action)

        menu.addSeparator()

        self.exit_action = QAction("Exit", menu)
        self.exit_action.triggered.connect(self.exit_app)
        menu.addAction(self.exit_action)

        # Held as an attribute: a QMenu that goes out of scope stops working.
        self.menu = menu
        self.setContextMenu(menu)

        self.activated.connect(self.on_activated)

    def on_activated(self, reason) -> None:
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self.open_window()

    def open_window(self) -> None:
        self.window.showNormal()
        self.window.raise_()
        self.window.activateWindow()

    def exit_app(self) -> None:
        """The only route to actually quitting."""
        self.window.quitting = True
        self.window.close()
