"""Global hotkeys — work whether or not the window is focused.

Qt shortcuts only fire when the app has focus, which is useless here: the
point is to change pitch while in a game or a call. The `keyboard` library
hooks the OS instead, so the bindings work from anywhere.

Its callbacks arrive on its own thread, so nothing here touches Qt directly.
Each binding emits a signal and Qt delivers it on the GUI thread.
"""

import keyboard
from PySide6.QtCore import QObject, Signal

# Action name -> default key combination.
DEFAULT_BINDINGS = {
    "pitch_low": "ctrl+1",
    "pitch_default": "ctrl+2",
    "pitch_high": "ctrl+3",
}

# Which preset each action selects on the pitch stage.
ACTION_PRESETS = {
    "pitch_low": "low",
    "pitch_default": "default",
    "pitch_high": "high",
}


class Hotkeys(QObject):
    """Registers global bindings and re-emits them on the GUI thread."""

    triggered = Signal(str)  # action name

    def __init__(self, bindings: dict[str, str] | None = None):
        super().__init__()
        self.bindings = dict(bindings or DEFAULT_BINDINGS)
        self.hooks: list = []
        self.error: str | None = None
        self.register()

    def register(self) -> None:
        self.unregister()

        for action, combination in self.bindings.items():
            if not combination:
                continue
            try:
                # suppress=False: the keystroke still reaches whatever app has
                # focus, so binding a common combination does not break it.
                hook = keyboard.add_hotkey(
                    combination, self.triggered.emit, args=(action,), suppress=False
                )
                self.hooks.append(hook)
            except Exception as e:
                # A bad combination should not take the others down with it.
                self.error = f"{combination}: {e}"

    def unregister(self) -> None:
        for hook in self.hooks:
            try:
                keyboard.remove_hotkey(hook)
            except Exception:
                pass
        self.hooks.clear()

    def rebind(self, bindings: dict[str, str]) -> None:
        self.bindings = dict(bindings)
        self.register()
