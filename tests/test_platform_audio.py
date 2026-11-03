"""Audio capture off Linux (haus/audiocapture.py): clap detection and
follow-audio read python-sounddevice instead of pw-record/parec. sounddevice
is not installed here: a stand-in module in sys.modules plays its part, and
tripwires make any recorder subprocess (the real microphone) fail the test."""

import asyncio
import sys
import threading
import types

import numpy as np
import pytest

from haus import _platform
from haus import audiocapture
from haus import audiofollow as audiomod
from haus import clap as clapmod

DEVICES = [
    {"index": 0, "name": "Built-in Microphone", "hostapi": 0, "max_input_channels": 1},
    {"index": 1, "name": "Built-in Output", "hostapi": 0, "max_input_channels": 0},
    {"index": 2, "name": "USB Mic", "hostapi": 0, "max_input_channels": 2},
]


def fake_sounddevice(blocks):
    """A sounddevice stand-in: InputStream plays `blocks` (int16 arrays of
    shape (frames, 1)) through its callback from a thread, then finishes."""
    sd = types.ModuleType("sounddevice")
    sd.opened = []

    class PortAudioError(Exception):
        pass

    class InputStream:
        def __init__(self, **kw):
            sd.opened.append(kw)
            self.kw = kw

        def __enter__(self):
            def play():
                for b in blocks:
                    self.kw["callback"](b, len(b), None, None)
                self.kw["finished_callback"]()
            threading.Thread(target=play, daemon=True).start()
            return self

        def __exit__(self, *exc):
            return False

    sd.PortAudioError = PortAudioError
    sd.InputStream = InputStream
    sd.default = types.SimpleNamespace(hostapi=0, device=(0, 1))
    sd.query_devices = lambda: [dict(d) for d in DEVICES]
    sd.query_hostapis = lambda i=None: ([{"name": "Core Audio", "default_input_device": 2}]
                                        if i is None else {"name": "Core Audio", "default_input_device": 2})
    return sd


@pytest.fixture
def off_linux(monkeypatch):
    monkeypatch.setattr(_platform, "IS_LINUX", False)

    def trip(*a, **k):
        raise AssertionError("a recorder subprocess was started off Linux")
    monkeypatch.setattr(clapmod.asyncio, "create_subprocess_exec", trip)
    monkeypatch.setattr(clapmod.shutil, "which", lambda name: None)


def blocks_of(n=3, frames=800):
    rng = np.random.default_rng(7)
    return [rng.integers(-32768, 32767, size=(frames, 1), dtype=np.int16) for _ in range(n)]


def install(monkeypatch, blocks):
    sd = fake_sounddevice(blocks)
    monkeypatch.setitem(sys.modules, "sounddevice", sd)
    monkeypatch.setattr(audiocapture.importlib.util, "find_spec",
                        lambda name, *a: object() if name == "sounddevice" else _find(name))
    return sd


_find = audiocapture.importlib.util.find_spec


def expected(blocks):
    return np.concatenate([b[:, 0].astype(np.float32) / 32768.0 for b in blocks])


def collect(capture_cls, *args, **kw):
    got = []
    cap = capture_cls(lambda samples, now: got.append(samples.copy()), *args, **kw)
    with pytest.raises(clapmod.CaptureError, match="finished"):
        asyncio.run(asyncio.wait_for(cap.run(), 5))
    return got


def test_clap_capture_hands_the_detector_the_same_blocks(monkeypatch, off_linux):
    blocks = blocks_of()
    sd = install(monkeypatch, blocks)
    fed = []

    class Detector:
        def feed(self, samples, now):
            fed.append(samples.copy())
            return []
    svc = clapmod.ClapService(lambda ev: None)
    svc.detector = Detector()
    cap = clapmod.Capture(svc._mic, "USB_Mic")
    with pytest.raises(clapmod.CaptureError):
        asyncio.run(asyncio.wait_for(cap.run(), 5))
    assert len(fed) == 3 and all(f.dtype == np.float32 and len(f) == 800 for f in fed)
    np.testing.assert_array_equal(np.concatenate(fed), expected(blocks))
    kw = sd.opened[0]
    assert (kw["samplerate"], kw["channels"], kw["dtype"], kw["blocksize"], kw["device"]) == (
        clapmod.RATE, 1, "int16", 800, 2)


def test_clap_speaker_check_is_off_without_loopback(monkeypatch, off_linux):
    sd = install(monkeypatch, blocks_of())
    mon = clapmod.Capture(lambda *_: None, monitor=True, name="clap detection (speaker check)")
    with pytest.raises(clapmod.CaptureError, match="loopback"):
        asyncio.run(mon.run())
    assert sd.opened == []


def test_audio_follow_hears_the_default_input(monkeypatch, off_linux):
    blocks = blocks_of()
    sd = install(monkeypatch, blocks)
    got = collect(audiomod.AudioCapture, "")
    np.testing.assert_array_equal(np.concatenate(got), expected(blocks))
    assert sd.opened[0]["device"] == 2  # the host API's default input, not a monitor

    failures = []
    svc = audiomod.AudioFollowService(lambda: None, failures.append)
    fed = []
    monkeypatch.setattr(audiomod.Analyser, "feed", lambda self, samples: fed.append(samples.copy()))

    async def main():
        svc.apply(True, "Built-in_Microphone")
        await asyncio.wait_for(svc.task, 5)
    asyncio.run(main())
    np.testing.assert_array_equal(np.concatenate(fed), expected(blocks))
    assert sd.opened[1]["device"] == 0 and failures and "finished" in failures[0]


def test_sources_are_the_input_devices(monkeypatch, off_linux):
    install(monkeypatch, [])
    mics = asyncio.run(clapmod.list_sources())
    assert mics == [{"name": "USB_Mic", "description": "USB Mic", "default": True, "muted": False},
                    {"name": "Built-in_Microphone", "description": "Built-in Microphone", "default": False,
                     "muted": False}]
    assert all(clapmod.check_source(m["name"]) == m["name"] for m in mics)
    srcs = asyncio.run(audiomod.list_sources())
    assert [(s["name"], s["monitor"], s["default"]) for s in srcs] == [
        ("USB_Mic", False, True), ("Built-in_Microphone", False, False)]
    assert asyncio.run(clapmod.source_muted("USB_Mic")) is None


def test_without_sounddevice_it_is_unavailable_not_an_exception(monkeypatch, off_linux):
    monkeypatch.setitem(sys.modules, "sounddevice", None)  # import fails
    monkeypatch.setattr(audiocapture.importlib.util, "find_spec",
                        lambda name, *a: None if name == "sounddevice" else _find(name))
    assert "sounddevice" in clapmod.missing() and "pw-record" not in clapmod.missing()
    assert audiomod.missing() == ["sounddevice"]

    async def main():
        svc = clapmod.ClapService(lambda ev: None)
        svc.apply("both", "", 50)
        await asyncio.sleep(0.05)
        out = svc.describe()
        svc.stop()
        failures = []
        follow = audiomod.AudioFollowService(lambda: None, failures.append)
        follow.apply(True, "")
        await asyncio.wait_for(follow.task, 5)
        return out, failures, follow.describe()
    st, failures, follow = asyncio.run(main())
    assert st["state"] == "unavailable" and "sounddevice" in st["missing"]
    assert failures and "sounddevice" in failures[0] and follow["available"] is False
    with pytest.raises(clapmod.CaptureError, match="sounddevice"):
        asyncio.run(clapmod.list_sources())
