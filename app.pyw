"""Live audio processing: pick an input and output device, then start.

Signal path:

    input -> [core: denoise, highpass, gate, compressor, gain]
          -> [rack: user-added effects]
          -> [output: ceiling]
          -> output

Core stages are fixed and always present; rack effects are added, reordered
and removed at runtime. Python-side stages (denoise, pitch) run before the
native pedalboard chain, since the two cannot be interleaved.

Layout:
    src/internal/      engine, rack, meter, and other plumbing
    src/plugins_core/  the fixed core stages
    src/plugins_rack/  effects that can be added to the rack
"""

import json
import sys

from PySide6.QtCore import QSettings, Qt, Signal
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from pedalboard import Pedalboard

from src.internal import engine as devices
from src.internal import errors, nowheel
from src.internal.collapsible import CollapsibleBox
from src.internal.engine import AudioEngine
from src.internal.meter import LevelMeter
from src.internal.hotkeys import ACTION_PRESETS, Hotkeys
from src.internal.rack import PluginRack
from src.internal.tray import Tray
from src.plugins_core.ceiling import CeilingStage
from src.plugins_core.compressor import CompressorStage
from src.plugins_core.denoise import DenoiseStage
from src.plugins_core.gain import GainStage
from src.plugins_core.gate import GateStage
from src.plugins_core.highpass import HighpassStage


def apply_dark_theme(app: QApplication) -> None:
    """Fusion + a dark palette, so every widget (including popups) follows."""
    app.setStyle("Fusion")

    base = QColor(30, 31, 34)
    surface = QColor(43, 45, 49)
    text = QColor(220, 221, 222)
    accent = QColor(88, 130, 200)

    palette = QPalette()
    palette.setColor(QPalette.Window, base)
    palette.setColor(QPalette.WindowText, text)
    palette.setColor(QPalette.Base, surface)
    palette.setColor(QPalette.AlternateBase, base)
    palette.setColor(QPalette.ToolTipBase, surface)
    palette.setColor(QPalette.ToolTipText, text)
    palette.setColor(QPalette.Text, text)
    palette.setColor(QPalette.Button, surface)
    palette.setColor(QPalette.ButtonText, text)
    palette.setColor(QPalette.Highlight, accent)
    palette.setColor(QPalette.HighlightedText, Qt.black)
    palette.setColor(QPalette.Disabled, QPalette.Text, QColor(120, 121, 122))
    palette.setColor(QPalette.Disabled, QPalette.ButtonText, QColor(120, 121, 122))
    app.setPalette(palette)

    # Give each stage box a visible border and a title that sits on it.
    app.setStyleSheet(
        """
        QFrame#stageBox {
            border: 1px solid #4a4c50;
            border-radius: 4px;
        }
        QFrame#stageBox QToolButton {
            color: #9aa0a6;
            padding: 2px;
        }
        """
    )


class DevicePicker(QWidget):
    """A labelled dropdown of device names, defaulting to the system default."""

    def __init__(self, title: str, names: list[str], default: str | None):
        super().__init__()
        heading = QLabel(title)
        heading.setStyleSheet("font-weight: bold;")

        self.combo = QComboBox()
        for name in names:
            self.combo.addItem(name)
            # Device names can be long; show the full string on hover.
            self.combo.setItemData(self.combo.count() - 1, f"{name}  ({len(name)} chars)", 3)

        if default in names:
            self.combo.setCurrentText(default)

        layout = QVBoxLayout(self)
        layout.addWidget(heading)
        layout.addWidget(self.combo)
        layout.addStretch()

    def selected(self) -> str:
        return self.combo.currentText()

    def repopulate(self, names: list[str], fallback: str | None) -> None:
        """Re-read the device list, keeping the current pick if it still exists."""
        current = self.selected()
        self.combo.clear()
        for name in names:
            self.combo.addItem(name)
            self.combo.setItemData(self.combo.count() - 1, f"{name}  ({len(name)} chars)", 3)

        self.combo.setCurrentText(current if current in names else (fallback or ""))

    def set_enabled(self, enabled: bool) -> None:
        self.combo.setEnabled(enabled)


class MainWindow(QMainWindow):
    # Emitted from the audio thread; Qt queues it onto the GUI thread for us.
    audio_error = Signal(str)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Pedalboard — Passthrough")
        self.resize(760, 620)

        self.quitting = False  # set by the tray's Exit action

        self.settings = QSettings("PBoard", "Passthrough")
        self.engine = AudioEngine()
        self.engine.on_error = self.audio_error.emit
        self.audio_error.connect(self.on_audio_error)

        # Core stages: always on, never reorderable. Each is grouped in its own
        # Pedalboard so it can be reset independently of the rack.
        # Denoise before gain, so hiss isn't amplified along with the signal.
        self.denoise_stage = DenoiseStage(self.settings)
        self.denoise_stage.changed.connect(self.rebuild_chain)
        self.highpass_stage = HighpassStage(self.settings)
        self.highpass_stage.changed.connect(self.rebuild_chain)
        self.gate_stage = GateStage(self.settings)
        self.gate_stage.changed.connect(self.rebuild_chain)
        self.compressor_stage = CompressorStage(self.settings)
        self.compressor_stage.changed.connect(self.rebuild_chain)
        self.gain_stage = GainStage(self.settings)
        self.ceiling_stage = CeilingStage(self.settings)

        self.output = Pedalboard([self.ceiling_stage.plugin])

        inputs = devices.list_inputs()
        outputs = devices.list_outputs()

        self.input_picker = DevicePicker(
            "Input (microphone)",
            inputs,
            self.remembered("input_device", inputs, devices.default_input()),
        )
        self.output_picker = DevicePicker(
            "Output (speakers)",
            outputs,
            self.remembered("output_device", outputs, devices.default_output()),
        )

        self.rack = PluginRack()
        self.rack.restore(self.load_rack_state())
        self.rack.changed.connect(self.rebuild_chain)
        # Persist immediately: the window can be hidden to the tray for days,
        # so waiting until exit risks losing changes if the process is killed.
        self.rack.changed.connect(self.save_rack_state)

        self.hotkeys = Hotkeys()
        self.hotkeys.triggered.connect(self.on_hotkey)

        self.toggle = QPushButton("Start")
        self.toggle.setMinimumHeight(40)
        self.toggle.clicked.connect(self.on_toggle)

        self.reload = QPushButton("Reload")
        self.reload.setMinimumHeight(40)
        self.reload.setMaximumWidth(100)
        self.reload.setToolTip("Re-scan devices and restart the audio stream")
        self.reload.clicked.connect(self.on_reload)

        buttons = QHBoxLayout()
        buttons.addWidget(self.toggle)
        buttons.addWidget(self.reload)

        pickers = QHBoxLayout()
        pickers.addWidget(self.input_picker)
        pickers.addWidget(self.output_picker)

        root = QVBoxLayout()
        root.addLayout(pickers)
        self.boxes = [
            CollapsibleBox("Noise suppression", self.denoise_stage, self.settings),
            CollapsibleBox("Highpass filter", self.highpass_stage, self.settings),
            CollapsibleBox("Noise gate", self.gate_stage, self.settings),
            CollapsibleBox("Compressor", self.compressor_stage, self.settings),
            CollapsibleBox("Input gain", self.gain_stage, self.settings),
            CollapsibleBox("Output ceiling", self.ceiling_stage, self.settings),
        ]
        # Everything below the device pickers scrolls, so the window can be
        # shrunk without losing the Start button or the meter.
        scrolling = QVBoxLayout()
        scrolling.setContentsMargins(0, 0, 6, 0)
        for box in self.boxes:
            scrolling.addWidget(box)
        scrolling.addWidget(self.rack)
        scrolling.addStretch()

        scroll_body = QWidget()
        scroll_body.setLayout(scrolling)

        scroll = QScrollArea()
        scroll.setWidget(scroll_body)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        root.addWidget(scroll, stretch=1)
        root.addLayout(buttons)

        self.meter = LevelMeter(self.engine.meter)

        columns = QHBoxLayout()
        columns.addLayout(root, stretch=1)
        columns.addWidget(self.meter)

        central = QWidget()
        central.setLayout(columns)
        self.setCentralWidget(central)

        self.statusBar().showMessage("Stopped")

        geometry = self.settings.value("geometry")
        if geometry:
            self.restoreGeometry(geometry)

    def on_audio_error(self, message: str) -> None:
        """A stage threw in the audio callback; audio is passing through dry."""
        errors.write("AUDIO CALLBACK", message)
        self.statusBar().showMessage(f"Effect error — passing audio through dry:  {message}")

    def on_hotkey(self, action: str) -> None:
        """A global hotkey fired. Runs on the GUI thread via the signal."""
        preset = ACTION_PRESETS.get(action)
        if preset is None:
            return

        stages = self.rack.stages_supporting("apply_preset")
        if not stages:
            self.statusBar().showMessage("No pitch shift in the rack.")
            return

        for stage in stages:
            stage.apply_preset(preset)

        self.statusBar().showMessage(f"Pitch preset:  {preset}")

    def save_all(self) -> None:
        """Write every persisted setting. Called on hide and on exit."""
        self.settings.setValue("input_device", self.input_picker.selected())
        self.settings.setValue("output_device", self.output_picker.selected())
        self.settings.setValue("geometry", self.saveGeometry())

        for box in self.boxes:
            box.save()

        for stage in (
            self.denoise_stage,
            self.highpass_stage,
            self.gate_stage,
            self.compressor_stage,
            self.gain_stage,
            self.ceiling_stage,
        ):
            stage.save()

        self.save_rack_state()

    def save_rack_state(self) -> None:
        self.settings.setValue("rack", json.dumps(self.rack.state()))
        self.settings.sync()

    def load_rack_state(self) -> list[dict]:
        """Rack contents are stored as a JSON string, since QSettings on Windows
        mangles nested lists of dicts."""
        raw = self.settings.value("rack", "")
        if not raw:
            return []
        try:
            state = json.loads(raw)
            return state if isinstance(state, list) else []
        except (json.JSONDecodeError, TypeError):
            return []

    def remembered(self, key: str, names: list[str], fallback: str | None) -> str | None:
        """The saved device, unless it has been unplugged since the last run."""
        saved = self.settings.value(key)
        return saved if saved in names else fallback

    def rebuild_chain(self) -> None:
        """Gain, then the rack, then the output ceiling — always last.

        Denoise isn't here: it runs in Python, ahead of this native board.
        """
        # Python stages first (denoise, then any rack effects that aren't
        # native plugins), then the native board.
        self.engine.python_stages = [
            self.denoise_stage.process
        ] + self.rack.active_python_stages()

        core = []
        if self.highpass_stage.is_enabled():
            core.append(self.highpass_stage.plugin)
        if self.gate_stage.is_enabled():
            core.append(self.gate_stage.plugin)
        if self.compressor_stage.is_enabled():
            core.append(self.compressor_stage.plugin)
        core.append(self.gain_stage.plugin)

        self.engine.board = Pedalboard(core + self.rack.active_plugins() + [self.output])

    def on_reload(self) -> None:
        """Tear the stream down, re-scan devices, and bring it back if it was running."""
        was_running = self.engine.running
        self.stop()

        devices.rescan()
        self.input_picker.repopulate(devices.list_inputs(), devices.default_input())
        self.output_picker.repopulate(devices.list_outputs(), devices.default_output())

        if was_running:
            self.start()
        else:
            self.statusBar().showMessage("Devices re-scanned")

    def on_toggle(self) -> None:
        if self.engine.running:
            self.stop()
        else:
            self.start()

    def start(self) -> None:
        self.rebuild_chain()

        try:
            self.engine.start(self.input_picker.selected(), self.output_picker.selected())
        except Exception as e:
            self.engine.stop()
            QMessageBox.critical(self, "Could not start audio", str(e))
            self.statusBar().showMessage("Stopped")
            return

        self.toggle.setText("Stop")
        self.input_picker.set_enabled(False)
        self.output_picker.set_enabled(False)
        latency = self.engine.stream.latency  # (input, output) in seconds
        self.statusBar().showMessage(
            f"Running — {self.engine.sample_rate} Hz, "
            f"{self.engine.block_size}-sample blocks, "
            f"latency in {1000 * latency[0]:.0f} ms / out {1000 * latency[1]:.0f} ms"
        )

    def stop(self) -> None:
        self.engine.stop()

        self.toggle.setText("Start")
        self.input_picker.set_enabled(True)
        self.output_picker.set_enabled(True)
        self.statusBar().showMessage("Stopped")

    def closeEvent(self, event) -> None:
        # Closing the window hides it to the tray; audio keeps running. Only
        # Exit from the tray menu sets `quitting` and actually shuts down.
        if not self.quitting:
            self.save_all()
            event.ignore()
            self.hide()
            return

        self.stop()
        self.save_all()
        self.hotkeys.unregister()

        super().closeEvent(event)
        QApplication.quit()


if __name__ == "__main__":
    errors.install()

    app = QApplication(sys.argv)
    apply_dark_theme(app)
    wheel_guard = nowheel.install(app)  # noqa: F841 - must outlive the app

    # Hiding the window must not end the app; only the tray's Exit does that.
    app.setQuitOnLastWindowClosed(False)

    window = MainWindow()
    window.tray = Tray(window)
    window.tray.show()
    app.setWindowIcon(window.tray.icon())

    window.show()
    sys.exit(app.exec())
