"""The plugin rack: an ordered, collapsible list of toggleable effects.

Rows look like:

    ▸ Delay        [x]  ▲ ▼

Clicking the arrow expands that plugin's settings panel underneath.
The rack owns plugin order; the Gain stage lives outside it, in the
main window, since it is always-on and not reorderable.
"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from src.internal import effects


class PluginRow(QFrame):
    """One plugin: header controls plus a collapsible settings panel."""

    changed = Signal()
    move_requested = Signal(object, int)  # (row, -1 for up / +1 for down)
    remove_requested = Signal(object)

    def __init__(self, name: str, plugin, settings: QWidget | None = None):
        super().__init__()
        self.name = name
        self.plugin = plugin  # a native pedalboard Plugin, or None
        self.stage = None  # the settings widget, set by add_effect()
        self.setFrameShape(QFrame.StyledPanel)

        self.expander = QToolButton()
        self.expander.setText("▸")  # ▸
        self.expander.setAutoRaise(True)
        self.expander.setCheckable(True)
        self.expander.toggled.connect(self.on_expanded)

        self.enabled = QCheckBox()
        self.enabled.setChecked(True)
        self.enabled.toggled.connect(self.changed)

        self.up = QToolButton()
        self.up.setText("▲")  # ▲
        self.up.setAutoRaise(True)
        self.up.clicked.connect(lambda: self.move_requested.emit(self, -1))

        self.down = QToolButton()
        self.down.setText("▼")  # ▼
        self.down.setAutoRaise(True)
        self.down.clicked.connect(lambda: self.move_requested.emit(self, +1))

        self.remove = QToolButton()
        self.remove.setText("✕")
        self.remove.setAutoRaise(True)
        self.remove.setToolTip("Remove this effect")
        self.remove.clicked.connect(lambda: self.remove_requested.emit(self))

        header = QHBoxLayout()
        header.addWidget(self.expander)
        header.addWidget(QLabel(name))
        header.addStretch()
        header.addWidget(self.enabled)
        header.addWidget(self.up)
        header.addWidget(self.down)
        header.addWidget(self.remove)

        # Settings panel, hidden until the arrow is clicked.
        self.settings = settings or QWidget()
        self.settings.setVisible(False)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.addLayout(header)
        layout.addWidget(self.settings)

    def on_expanded(self, expanded: bool) -> None:
        self.expander.setText("▾" if expanded else "▸")  # ▾ / ▸
        self.settings.setVisible(expanded)

    def is_enabled(self) -> bool:
        return self.enabled.isChecked()


class PluginRack(QWidget):
    """An ordered stack of PluginRows."""

    changed = Signal()

    def __init__(self):
        super().__init__()
        self.rows: list[PluginRow] = []

        heading = QLabel("Effects")
        heading.setStyleSheet("font-weight: bold;")

        self.chooser = QComboBox()
        self.chooser.addItems(effects.names())

        add_button = QPushButton("Add")
        add_button.setMaximumWidth(70)
        add_button.clicked.connect(self.on_add_clicked)

        controls = QHBoxLayout()
        controls.addWidget(heading)
        controls.addStretch()
        controls.addWidget(self.chooser)
        controls.addWidget(add_button)

        self.empty = QLabel("No effects yet — pick one above and hit Add.")
        self.empty.setAlignment(Qt.AlignCenter)
        self.empty.setStyleSheet("color: #7a7b7c; padding: 12px;")

        self.stack = QVBoxLayout()
        self.stack.setSpacing(4)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(controls)
        layout.addWidget(self.empty)
        layout.addLayout(self.stack)
        layout.addStretch()

    def on_add_clicked(self) -> None:
        self.add_effect(self.chooser.currentText())

    def add_effect(self, name: str) -> PluginRow | None:
        """Build a registered effect and rack it."""
        name = effects.resolve(name)
        if name not in effects.REGISTRY:
            return None

        stage = effects.create(name)
        row = self.add(name, getattr(stage, "plugin", None), stage)
        row.stage = stage

        # Stages whose plugin appears later (VST3) announce it, so the chain
        # can be rebuilt once it exists.
        if hasattr(stage, "changed"):
            stage.changed.connect(self.changed)

        return row

    def add(self, name: str, plugin, settings: QWidget | None = None) -> PluginRow:
        row = PluginRow(name, plugin, settings)
        row.changed.connect(self.changed)
        row.move_requested.connect(self.move)
        row.remove_requested.connect(self.remove)

        self.rows.append(row)
        self.stack.addWidget(row)
        self.refresh()
        self.changed.emit()
        return row

    def remove(self, row: PluginRow) -> None:
        self.rows.remove(row)
        self.stack.removeWidget(row)
        row.deleteLater()
        self.refresh()
        self.changed.emit()

    def move(self, row: PluginRow, delta: int) -> None:
        old = self.rows.index(row)
        new = old + delta
        if not 0 <= new < len(self.rows):
            return

        self.rows.insert(new, self.rows.pop(old))
        self.stack.removeWidget(row)
        self.stack.insertWidget(new, row)
        self.refresh()
        self.changed.emit()

    def refresh(self) -> None:
        """Keep the empty-state label and the end-of-list arrows honest."""
        self.empty.setVisible(not self.rows)
        for i, row in enumerate(self.rows):
            row.up.setEnabled(i > 0)
            row.down.setEnabled(i < len(self.rows) - 1)

    def state(self) -> list[dict]:
        """Serialisable snapshot: which effects, in what order, how set."""
        snapshot = []
        for row in self.rows:
            entry = {"name": row.name, "enabled": row.is_enabled()}
            if row.stage is not None and hasattr(row.stage, "params"):
                entry["params"] = row.stage.params()
            snapshot.append(entry)
        return snapshot

    def restore(self, snapshot: list[dict]) -> None:
        """Rebuild the rack from a snapshot, skipping anything unrecognised."""
        for entry in snapshot:
            row = self.add_effect(entry.get("name", ""))
            if row is None:
                continue

            if row.stage is not None and hasattr(row.stage, "load"):
                row.stage.load(entry.get("params", {}))

            # Set last: this emits changed, and we want the params in place first.
            row.enabled.setChecked(bool(entry.get("enabled", True)))

    @staticmethod
    def plugin_of(row):
        """The row's native plugin, asked for fresh each time.

        A VST3 row has no plugin until the user picks a file, so this cannot be
        cached at the moment the row is created.
        """
        if row.stage is not None and hasattr(row.stage, "plugin"):
            return row.stage.plugin
        return row.plugin

    def stages_supporting(self, method: str) -> list:
        """Every stage in the rack exposing the given method, in rack order."""
        return [
            row.stage
            for row in self.rows
            if row.stage is not None and hasattr(row.stage, method)
        ]

    def active_plugins(self) -> list:
        """Enabled native plugins, in rack order. Disabled ones are omitted."""
        plugins = [self.plugin_of(row) for row in self.rows if row.is_enabled()]
        return [plugin for plugin in plugins if plugin is not None]

    def active_python_stages(self) -> list:
        """Enabled Python-side stages, in rack order.

        These run before the native board regardless of row position, since
        the engine applies Python stages first.
        """
        return [
            row.stage.process
            for row in self.rows
            if row.is_enabled()
            and self.plugin_of(row) is None
            and row.stage is not None
            and hasattr(row.stage, "process")
        ]
