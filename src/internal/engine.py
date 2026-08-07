"""The audio engine: one duplex sounddevice stream, callback-driven.

sounddevice handles I/O, pedalboard handles DSP. This split matters:
pedalboard's AudioStream has no callback hook and cannot host Python DSP,
so driving two of its streams from a Python loop leaves input and output on
separate clocks — which drift apart and click. A single duplex sd.Stream
puts both endpoints on one hardware clock, and PortAudio calls us when it
needs samples rather than us guessing.

Signal path, per callback:

    indata -> denoise (Python) -> Pedalboard (native) -> outdata
"""

import threading

import numpy as np
import sounddevice as sd

# Windows exposes the same device under several host APIs, but a duplex stream
# needs both endpoints on ONE of them. Preference order, best first.
HOST_API_PREFERENCE = {
    "Windows WASAPI": 0,
    "Windows DirectSound": 1,
    "MME": 2,
    "Windows WDM-KS": 3,
}


def rescan() -> None:
    """Force PortAudio to re-enumerate; it caches the device list at init."""
    try:
        sd._terminate()
        sd._initialize()
    except Exception:
        pass


def list_inputs() -> list[str]:
    seen = {}
    for device in sd.query_devices():
        if device["max_input_channels"] > 0:
            seen[device["name"]] = True
    return list(seen)


def list_outputs() -> list[str]:
    seen = {}
    for device in sd.query_devices():
        if device["max_output_channels"] > 0:
            seen[device["name"]] = True
    return list(seen)


def default_input() -> str | None:
    try:
        return sd.query_devices(kind="input")["name"]
    except Exception:
        return None


def default_output() -> str | None:
    try:
        return sd.query_devices(kind="output")["name"]
    except Exception:
        return None


class MeterState:
    """Thread-safe peak/RMS levels, written by the audio thread, read by the GUI."""

    def __init__(self):
        self.lock = threading.Lock()
        self.in_peak = 0.0
        self.in_rms = 0.0
        self.out_peak = 0.0
        self.out_rms = 0.0

    def update(self, in_audio: np.ndarray, out_audio: np.ndarray) -> None:
        in_mono = in_audio[:, 0] if in_audio.ndim > 1 else in_audio
        out_mono = out_audio[:, 0] if out_audio.ndim > 1 else out_audio
        with self.lock:
            self.in_peak = float(np.max(np.abs(in_mono)))
            self.in_rms = float(np.sqrt(np.mean(in_mono**2)))
            self.out_peak = float(np.max(np.abs(out_mono)))
            self.out_rms = float(np.sqrt(np.mean(out_mono**2)))

    def read(self) -> tuple[float, float, float, float]:
        with self.lock:
            return self.in_peak, self.in_rms, self.out_peak, self.out_rms

    def clear(self) -> None:
        with self.lock:
            self.in_peak = self.in_rms = self.out_peak = self.out_rms = 0.0


class AudioEngine:
    """A duplex stream running a denoise callable plus a native board."""

    def __init__(self, sample_rate: int = 48000, block_size: int = 480, channels: int = 1):
        self.sample_rate = sample_rate
        self.block_size = block_size
        self.channels = channels

        self.stream: sd.Stream | None = None
        self.meter = MeterState()

        # Set by the owner; read by the audio thread every callback.
        # Python-side stages run in order, before the native board.
        self.python_stages: list = []  # callables (audio, sample_rate) -> audio
        self.board = None  # a pedalboard.Pedalboard, or None

        # Called with a message string when the callback fails. Set by the
        # owner; invoked from the audio thread, so it must be thread-safe.
        self.on_error = None
        self.last_error: str | None = None
        self.error_count = 0

    @property
    def running(self) -> bool:
        return self.stream is not None and self.stream.active

    def candidate_pairs(self, input_name: str, output_name: str) -> list[tuple[int, int]]:
        """(input_index, output_index) pairs sharing a host API, best first."""
        devices = sd.query_devices()

        def matching(name: str, want_input: bool) -> list[int]:
            key = "max_input_channels" if want_input else "max_output_channels"
            return [i for i, d in enumerate(devices) if d["name"] == name and d[key] > 0]

        pairs = []
        for i in matching(input_name, True):
            for o in matching(output_name, False):
                if devices[i]["hostapi"] == devices[o]["hostapi"]:
                    api = sd.query_hostapis(devices[i]["hostapi"])["name"]
                    pairs.append((HOST_API_PREFERENCE.get(api, 9), i, o))

        pairs.sort()
        return [(i, o) for _, i, o in pairs]

    def callback(self, indata, outdata, frames, time, status) -> None:
        audio = indata.copy()  # (frames, channels)

        try:
            for stage in self.python_stages:
                audio = stage(audio, self.sample_rate)

            board = self.board
            if board is not None:
                # pedalboard wants (channels, frames); reset=False because this
                # is one continuous stream, not an independent buffer.
                processed = board(
                    np.ascontiguousarray(audio.T), float(self.sample_rate), reset=False
                )
                audio = np.ascontiguousarray(processed.T)
        except Exception as e:
            # Never raise out of the audio callback — that kills the stream.
            # Fall back to dry input and report, but only once per failure run:
            # a broken plugin throws every block, and that would flood the GUI.
            audio = indata
            self.error_count += 1
            message = f"{type(e).__name__}: {e}"
            if message != self.last_error and self.on_error is not None:
                self.last_error = message
                self.on_error(message)

        # A plugin may buffer internally and hand back fewer samples than asked.
        if audio.shape[0] < frames:
            padded = np.zeros((frames, audio.shape[1]), dtype=np.float32)
            padded[: audio.shape[0]] = audio
            audio = padded
        elif audio.shape[0] > frames:
            audio = audio[:frames]

        # Match the output device's channel count.
        out_channels = outdata.shape[1]
        if audio.shape[1] < out_channels:
            audio = np.repeat(audio[:, :1], out_channels, axis=1)
        elif audio.shape[1] > out_channels:
            audio = audio[:, :out_channels]

        outdata[:] = audio
        self.meter.update(indata, audio)

    def start(self, input_name: str, output_name: str) -> None:
        pairs = self.candidate_pairs(input_name, output_name)
        if not pairs:
            raise RuntimeError(
                "No host API exposes both of those devices. Pick a different pair."
            )

        self.last_error = None
        self.error_count = 0

        last_error = None
        for input_index, output_index in pairs:
            try:
                self.stream = sd.Stream(
                    device=(input_index, output_index),
                    samplerate=self.sample_rate,
                    blocksize=self.block_size,
                    channels=self.channels,
                    dtype="float32",
                    # Drop this to let PortAudio pick its (safer, slower) default.
                    latency="low",
                    callback=self.callback,
                )
                self.stream.start()
                return
            except sd.PortAudioError as e:
                self.stream = None
                last_error = e

        raise last_error

    def stop(self) -> None:
        if self.stream is not None:
            self.stream.stop()
            self.stream.close()
            self.stream = None

        self.meter.clear()
