"""VST3 host — a rack effect that loads any third-party VST3 effect plugin.

pedalboard's load_plugin() returns an object with the same interface as its
built-ins, so a VST3 drops straight into the native chain. What it does not
provide is a user interface: pedalboard is headless, so the plugin's own GUI
is unavailable and controls are built here from whatever parameters the plugin
reports.

Parameters arrive as named properties with metadata attached (range, step,
units, or a list of valid strings). Each is mapped onto a slider or a dropdown
as appropriate, so any plugin gets a usable UI without per-plugin code.
"""

import ctypes
import math
import threading
from pathlib import Path

from PySide6.QtCore import QSettings, Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from pedalboard import load_plugin

NAME = "VST3 plugin"

# Where Windows keeps VST3s, used as the file dialog's starting point.
DEFAULT_VST3_DIR = Path("C:/Program Files/Common Files/VST3")

SLIDER_STEPS = 200  # resolution for continuous parameters

# Plugins report `valid_values` for continuous parameters too, as a list of
# every representable step -- Airwindows returns 1001 floats for a 0..1 knob.
# Only treat it as a genuine choice list when it is short.
MAX_CHOICES = 32

# Substituted when a plugin reports an unbounded range (Airwindows uses
# -inf for a level control's minimum).
FALLBACK_MINIMUM = -60.0
FALLBACK_MAXIMUM = 60.0

# Some plugin editors open without a close button of their own, and our window
# is blocked while one is up, so it cannot offer one either. A watcher thread
# polls for Escape and closes the editor through show_editor's close_event.
VK_ESCAPE = 0x1B
ESCAPE_POLL_SECONDS = 0.1


class ParameterControl(QWidget):
    """One plugin parameter, as a slider or a dropdown."""

    def __init__(self, plugin, key: str):
        super().__init__()
        self.plugin = plugin
        self.key = key
        self.parameter = plugin.parameters[key]
        self.mode = "range"  # set by build(): "range", "index" or "choice"
        self.choices: list = []

        self.value_label = QLabel()
        self.widget = self.build()

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.widget, stretch=1)
        layout.addWidget(self.value_label)

    def build(self) -> QWidget:
        choices = list(getattr(self.parameter, "valid_values", None) or [])
        current = getattr(self.plugin, self.key)

        # A genuinely short list of options: a dropdown.
        if choices and len(choices) <= MAX_CHOICES:
            self.mode = "choice"
            combo = QComboBox()
            combo.addItems([str(choice) for choice in choices])
            combo.setCurrentText(str(current))
            combo.currentTextChanged.connect(self.on_choice)
            self.value_label.setText("")
            return combo

        slider = QSlider(Qt.Horizontal)
        slider.valueChanged.connect(self.on_slide)

        # Numeric with usable bounds: a slider over the real range.
        if isinstance(current, (int, float)) and not isinstance(current, bool):
            self.mode = "range"
            slider.setRange(0, SLIDER_STEPS)
            slider.setValue(self.to_slider(float(current)))
            self.refresh_label()
            return slider

        # Otherwise the value is a formatted string (kHs reports decay as
        # "3.00 s"). There is nothing to compute with, but the plugin does
        # enumerate every valid value, so index into that list instead.
        self.mode = "index"
        self.choices = choices
        slider.setRange(0, max(0, len(choices) - 1))
        slider.setValue(choices.index(current) if current in choices else len(choices) // 2)
        self.refresh_label()
        return slider

    @property
    def minimum(self) -> float:
        return self.finite(getattr(self.parameter, "min_value", None), FALLBACK_MINIMUM)

    @property
    def maximum(self) -> float:
        return self.finite(getattr(self.parameter, "max_value", None), FALLBACK_MAXIMUM)

    @staticmethod
    def finite(value, fallback: float) -> float:
        """Plugins may report None or an infinite bound; neither is usable."""
        try:
            value = float(value)
        except (TypeError, ValueError):
            return fallback
        return value if math.isfinite(value) else fallback

    def to_slider(self, value: float) -> int:
        span = self.maximum - self.minimum
        if span <= 0:
            return 0
        return int(round((value - self.minimum) / span * SLIDER_STEPS))

    def from_slider(self, position: int) -> float:
        return self.minimum + (position / SLIDER_STEPS) * (self.maximum - self.minimum)

    def on_slide(self, position: int) -> None:
        if self.mode == "index":
            value = self.choices[position] if 0 <= position < len(self.choices) else None
        else:
            value = self.from_slider(position)

        if value is not None:
            try:
                setattr(self.plugin, self.key, value)
            except Exception:
                pass  # some plugins reject values mid-range; leave the old one

        self.refresh_label()

    def on_choice(self, text: str) -> None:
        try:
            setattr(self.plugin, self.key, text)
        except Exception:
            pass

    def refresh_label(self) -> None:
        value = getattr(self.plugin, self.key)
        units = getattr(self.parameter, "units", "") or ""
        text = f"{value:.2f}" if isinstance(value, float) else str(value)
        self.value_label.setText(f"{text} {units}".strip())

    def value(self):
        """The current value, as something JSON can store.

        pedalboard hands back its own wrapper types for some parameters (a
        string-valued one arrives as WeakTypeWrapper, not str), which json
        refuses. Anything that is not a plain primitive becomes text.
        """
        raw = getattr(self.plugin, self.key)
        if isinstance(raw, bool) or isinstance(raw, (int, float)) or raw is None:
            return raw
        return str(raw)

    def restore(self, value) -> None:
        # Index parameters were saved as text, so find the matching entry by
        # its text rather than by identity.
        if self.mode == "index":
            position = self.index_of(value)
            if position is None:
                return
            self.widget.setValue(position)  # drives on_slide, which sets the plugin
            return

        try:
            setattr(self.plugin, self.key, value)
        except Exception:
            return

        if self.mode == "choice":
            self.widget.setCurrentText(str(value))
            return

        self.widget.blockSignals(True)
        self.widget.setValue(self.to_slider(float(value)))
        self.widget.blockSignals(False)
        self.refresh_label()

    def index_of(self, value) -> int | None:
        target = str(value)
        for position, choice in enumerate(self.choices):
            if str(choice) == target:
                return position
        return None


class VSTStage(QWidget):
    """Loads a .vst3 and exposes its parameters. Native, so it runs in-chain."""

    name = NAME
    changed = Signal()  # emitted once a plugin is loaded, so the chain rebuilds

    def __init__(self, settings: QSettings | None = None):
        super().__init__()
        self.plugin = None
        self.path: str | None = None
        self.controls: dict[str, ParameterControl] = {}

        self.status = QLabel("No plugin loaded.")
        self.status.setWordWrap(True)

        browse = QPushButton("Load VST3…")
        browse.clicked.connect(self.on_browse)

        self.editor_button = QPushButton("Open editor")
        self.editor_button.setToolTip(
            "Open the plugin's own interface.\n"
            "Press Esc to close it. This window is frozen meanwhile;\n"
            "audio keeps running."
        )
        self.editor_button.clicked.connect(self.on_editor)
        self.editor_button.setEnabled(False)

        header = QHBoxLayout()
        header.addWidget(browse)
        header.addWidget(self.editor_button)
        header.addWidget(self.status, stretch=1)

        self.form = QFormLayout()
        self.form.setContentsMargins(0, 6, 0, 0)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(header)
        layout.addLayout(self.form)

    def on_browse(self) -> None:
        start = str(DEFAULT_VST3_DIR if DEFAULT_VST3_DIR.exists() else Path.home())
        path, _ = QFileDialog.getOpenFileName(self, "Select a VST3 plugin", start, "VST3 (*.vst3)")
        if path:
            self.load_plugin_at(path)

    def on_editor(self) -> None:
        """Show the plugin's native GUI.

        show_editor() blocks until the window is closed, so our window is
        unresponsive meanwhile. Audio is unaffected: it runs on the audio
        thread. Afterwards the controls are refreshed, since the plugin's own
        interface will have moved its parameters.
        """
        if self.plugin is None:
            return

        close_event = threading.Event()
        watcher = threading.Thread(target=self.watch_for_escape, args=(close_event,), daemon=True)
        watcher.start()

        try:
            self.plugin.show_editor(close_event)
        except Exception as e:
            self.status.setText(f"Editor unavailable:  {e}")
            return
        finally:
            close_event.set()  # stop the watcher however we got here

        self.build_controls()

    @staticmethod
    def watch_for_escape(close_event: threading.Event) -> None:
        """Set close_event when Escape is pressed, so the editor can be closed
        even if the plugin's window has no close button."""
        try:
            user32 = ctypes.windll.user32
        except AttributeError:
            return  # not Windows; rely on the window's own controls

        while not close_event.is_set():
            if user32.GetAsyncKeyState(VK_ESCAPE) & 0x8000:
                close_event.set()
                return
            close_event.wait(ESCAPE_POLL_SECONDS)

    def load_plugin_at(self, path: str) -> None:
        try:
            plugin = load_plugin(path)
        except Exception as e:
            self.status.setText(f"Could not load:  {e}")
            return

        if getattr(plugin, "is_instrument", False):
            self.status.setText("That is an instrument plugin — effects only.")
            return

        self.plugin = plugin
        self.path = path
        self.editor_button.setEnabled(True)

        latency = getattr(plugin, "reported_latency_samples", 0) or 0
        summary = Path(path).stem
        if latency:
            summary += f"   (+{1000 * latency / 48000:.0f} ms latency)"
        self.status.setText(summary)

        self.build_controls()
        self.changed.emit()

    def build_controls(self) -> None:
        while self.form.count():
            item = self.form.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        self.controls.clear()

        if self.plugin is None:
            return

        for key in self.plugin.parameters:
            try:
                control = ParameterControl(self.plugin, key)
            except Exception:
                continue  # skip anything that will not map cleanly
            self.controls[key] = control
            self.form.addRow(QLabel(key.replace("_", " ")), control)

    def params(self) -> dict:
        return {
            "path": self.path,
            "values": {key: control.value() for key, control in self.controls.items()},
        }

    def load(self, params: dict) -> None:
        path = params.get("path")
        if not path or not Path(path).exists():
            return

        self.load_plugin_at(path)
        for key, value in params.get("values", {}).items():
            control = self.controls.get(key)
            if control is not None:
                control.restore(value)
