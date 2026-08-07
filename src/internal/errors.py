"""Crash logging — errors only, nothing else.

Running as a .pyw means no console, so an unhandled exception would otherwise
vanish silently and the app would simply fail to appear. This writes those to
a log file and shows a message box, so a failure is visible and diagnosable.

Only uncaught exceptions are recorded. There is no verbose or informational
logging, and nothing is written during normal operation.
"""

import sys
import traceback
from datetime import datetime
from pathlib import Path

LOG_PATH = Path(__file__).resolve().parents[2] / "assets" / "error.log"
MAX_BYTES = 256 * 1024  # trim rather than grow without limit


def write(kind: str, text: str) -> None:
    """Append one timestamped entry, trimming the file if it has grown large."""
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

        if LOG_PATH.exists() and LOG_PATH.stat().st_size > MAX_BYTES:
            tail = LOG_PATH.read_text(encoding="utf-8", errors="replace")[-MAX_BYTES // 2 :]
            LOG_PATH.write_text(f"[log trimmed]\n{tail}", encoding="utf-8")

        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with LOG_PATH.open("a", encoding="utf-8") as log:
            log.write(f"\n{'-' * 70}\n{stamp}  {kind}\n{text}\n")
    except Exception:
        # Logging must never itself take the app down.
        pass


def install() -> None:
    """Route uncaught exceptions to the log, then to a message box."""
    previous = sys.excepthook

    def hook(exc_type, exc_value, exc_traceback):
        if issubclass(exc_type, KeyboardInterrupt):
            previous(exc_type, exc_value, exc_traceback)
            return

        detail = "".join(traceback.format_exception(exc_type, exc_value, exc_traceback))
        write("UNCAUGHT", detail)
        show(exc_type.__name__, str(exc_value))

    sys.excepthook = hook


def show(title: str, message: str) -> None:
    """Best-effort message box; does nothing if Qt is not up yet."""
    try:
        from PySide6.QtWidgets import QApplication, QMessageBox

        if QApplication.instance() is None:
            return

        box = QMessageBox()
        box.setIcon(QMessageBox.Critical)
        box.setWindowTitle("Pedalboard — error")
        box.setText(f"{title}: {message}")
        box.setInformativeText(f"Details written to:\n{LOG_PATH}")
        box.exec()
    except Exception:
        pass
