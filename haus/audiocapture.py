"""Audio capture off Linux: python-sounddevice (PortAudio: CoreAudio on macOS,
WASAPI on Windows) in place of pw-record/parec, for clap detection
(haus/clap.py) and follow-audio (haus/audiofollow.py).

It hands on_samples the same thing the Linux pipe does: mono 16 kHz signed
16-bit samples as float32 −1…1, with the monotonic time of the last sample,
in chunks of 50 ms. The consumers are the same code on every system.

Off Linux:
- Sources are PortAudio's input devices (on Windows those of WASAPI when it
  is there, else the default host API's), named by their device name with
  whitespace as "_" (setting values have none); "" is the default input.
- There is no speaker loopback: the clap detector's speaker check (a hit the
  default output plays at the same time is dropped) is off, and follow-audio
  hears the default input (a loopback device such as BlackHole or Stereo Mix
  is a source like any other), not the default output.
- No mute switch is read (None: unknown).

sounddevice is optional (requirements-clap.txt, sys_platform != "linux");
without it missing() names it and the feature reports itself unavailable.
On Linux this module is not used for capture.
"""

from __future__ import annotations

import asyncio
import importlib.util
import re
import sys
import time
from collections.abc import Callable

NO_LOOPBACK = "no speaker loopback on this system: speaker sounds are not filtered"


class CaptureError(Exception):
    pass


def missing() -> list[str]:
    """["sounddevice"] when the package is not installed, else []."""
    return ["sounddevice"] if importlib.util.find_spec("sounddevice") is None else []


def _sd():
    try:
        import sounddevice as sd
    except (ImportError, OSError) as exc:  # not installed, or no PortAudio library
        raise CaptureError(f"sounddevice unavailable: {exc}") from None
    return sd


def device_name(name: str) -> str:
    """A device's name as a setting value: whitespace runs as "_"."""
    return re.sub(r"\s+", "_", str(name).strip())


def _hostapi(sd) -> int:
    """The host API whose devices are listed: WASAPI on Windows when there,
    else the default one (CoreAudio on macOS)."""
    apis = list(sd.query_hostapis())
    if sys.platform == "win32":
        for i, api in enumerate(apis):
            if "WASAPI" in str(api.get("name", "")):
                return i
    try:
        return int(sd.default.hostapi)
    except (AttributeError, TypeError, ValueError):
        return 0


def input_devices() -> list[dict]:
    """[{index, name, description, default}] of the listed host API's input
    devices, the default first. CaptureError without sounddevice."""
    sd = _sd()
    try:
        api = _hostapi(sd)
        default = int(sd.query_hostapis(api).get("default_input_device", -1))
        devices = list(sd.query_devices())
    except Exception as exc:  # PortAudioError and the like
        raise CaptureError(f"sounddevice: {exc}") from None
    out = []
    for i, dev in enumerate(devices):
        if dev.get("max_input_channels", 0) < 1 or dev.get("hostapi", api) != api:
            continue
        idx = int(dev.get("index", i))
        out.append({"index": idx, "name": device_name(dev.get("name", idx)),
                    "description": str(dev.get("name", idx)), "default": idx == default})
    out.sort(key=lambda d: not d["default"])
    return out


def resolve(source: str):
    """The device index for a source name; None for "" (the default input)."""
    if not source:
        devices = input_devices()
        return next((d["index"] for d in devices if d["default"]), None)
    for d in input_devices():
        if d["name"] == source:
            return d["index"]
    raise CaptureError(f"no input device named {source}")


async def run(source: str, on_samples: Callable, rate: int, block: int) -> None:
    """Until cancelled: blocks of `block` frames from the input `source`
    ("" the default) to on_samples(float32 −1…1, monotonic time); CaptureError
    when the stream cannot open or ends by itself."""
    import numpy as np
    sd = _sd()
    device = resolve(source)
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()

    def callback(indata, frames, _time, status) -> None:  # PortAudio's thread
        loop.call_soon_threadsafe(queue.put_nowait, bytes(indata))

    def finished() -> None:
        loop.call_soon_threadsafe(queue.put_nowait, None)

    extra = None
    if sys.platform == "win32" and hasattr(sd, "WasapiSettings"):
        extra = sd.WasapiSettings(auto_convert=True)  # shared mode resamples to 16 kHz
    try:
        stream = sd.InputStream(samplerate=rate, channels=1, dtype="int16", blocksize=block, device=device,
                                callback=callback, finished_callback=finished, extra_settings=extra)
    except Exception as exc:  # PortAudioError: no device, the rate refused, no permission
        raise CaptureError(f"sounddevice: {exc}") from None
    with stream:
        while True:
            data = await queue.get()
            if data is None:
                raise CaptureError("recorder ended (sounddevice stream finished)")
            cut = len(data) - len(data) % 2
            if cut:
                samples = np.frombuffer(data[:cut], dtype="<i2").astype(np.float32) / 32768.0
                on_samples(samples, time.monotonic())
