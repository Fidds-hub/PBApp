"""Registry of effects that can be added to the rack.

Each entry maps a display name to a factory returning a stage widget. A stage
widget must expose:

    .plugin      a pedalboard Plugin
    .params()    a JSON-friendly dict of its current settings
    .load(dict)  restore settings from that dict

Adding a new rack effect means writing that widget and adding one line here.

Note on live use: anything with significant priming latency is unsuitable here.
pedalboard's PitchShift (Rubber Band) was tried and removed — it buffers ~1.08
seconds before emitting its first sample, regardless of block size.
"""

from src.plugins_rack.crush import CrusherStage
from src.plugins_rack.soundtouch import SoundTouchStage
from src.plugins_rack.vst import VSTStage

REGISTRY = {
    CrusherStage.name: CrusherStage,
    SoundTouchStage.name: SoundTouchStage,
    VSTStage.name: VSTStage,
}

# Names that saved racks may still refer to, mapped to what they are now.
ALIASES = {
    "Pitch shift (SoundTouch)": SoundTouchStage.name,
}


def names() -> list[str]:
    return sorted(REGISTRY)


def resolve(name: str) -> str:
    """Map a possibly-outdated saved name onto a current one."""
    return ALIASES.get(name, name)


def create(name: str):
    return REGISTRY[resolve(name)]()
