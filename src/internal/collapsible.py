"""A titled box whose contents fold away, for keeping the window compact.

Same interaction as the plugin rack rows: click the header to expand or
collapse. Expanded state is remembered between runs.
"""

from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QFrame, QToolButton, QVBoxLayout, QWidget


class CollapsibleBox(QFrame):
    """A header button plus a body widget that shows and hides."""

    def __init__(
        self,
        title: str,
        body: QWidget,
        settings: QSettings | None = None,
        key: str | None = None,
        expanded: bool = True,
    ):
        super().__init__()
        self.setObjectName("stageBox")
        self.settings = settings
        self.key = key or f"expanded_{title.lower().replace(' ', '_')}"
        self.body = body

        if settings is not None:
            expanded = settings.value(self.key, "true" if expanded else "false") == "true"

        self.header = QToolButton()
        self.header.setText(title)
        self.header.setCheckable(True)
        self.header.setChecked(expanded)
        self.header.setAutoRaise(True)
        self.header.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.header.setArrowType(Qt.DownArrow if expanded else Qt.RightArrow)
        self.header.setStyleSheet("font-weight: bold; border: none;")
        self.header.toggled.connect(self.on_toggled)

        self.body.setVisible(expanded)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 6)
        layout.setSpacing(4)
        layout.addWidget(self.header)
        layout.addWidget(self.body)

    def on_toggled(self, expanded: bool) -> None:
        self.header.setArrowType(Qt.DownArrow if expanded else Qt.RightArrow)
        self.body.setVisible(expanded)

    def save(self) -> None:
        if self.settings is not None:
            self.settings.setValue(self.key, "true" if self.header.isChecked() else "false")
