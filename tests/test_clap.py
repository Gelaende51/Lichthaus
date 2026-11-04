"""Double clap / snap: the transient gate, the double-hit timing, the speaker
check (synthetic signals, the classifier mocked), settings and ctl commands."""

import asyncio
import copy
import sys

import pytest

np = pytest.importorskip("numpy")

from haus import clap as clapmod  # noqa: E402
from haus.clap import CLAP, RATE, SNAP, DcBlocker, HitSequenceDetector, TransientGate  # noqa: E402
from haus.common import UserError  # noqa: E402
from haus.profiles import migrate, new_state  # noqa: E402
from test_core import cmd, mock_state, run, settle  # noqa: E402

RNG = np.random.default_rng(7)


def room(seconds: float, rms: float = 0.002):
    return (RNG.standard_normal(int(seconds * RATE)) * rms).astype(np.float32)


def add_clap(sig, at: float, amp: float = 0.5, tau: float = 0.008, reverb: float = 0.0, rt60: float = 0.5):
    """A clap: a noise burst decaying with time constant tau (8 ms), plus a
    room's reverberation `reverb` (amplitude relative to the clap) that
    falls 60 dB in rt60."""
    n = int(max(0.06, 6 * tau, rt60 if reverb else 0.0) * RATE)
    t = np.arange(n) / RATE
    start = int(at * RATE)
    burst = RNG.standard_normal(n) * amp * np.exp(-t / tau)
    if reverb:
        burst += RNG.standard_normal(n) * amp * reverb * np.exp(-t * 6.9 / rt60)
    end = min(len(sig), start + n)
    sig[start:end] += burst[:end - start].astype(np.float32)
    return sig


class FakeSequence:
    """Scores per model run, in order (the last one repeats): hits that do
    not share a window can differ."""

    def __init__(self, *scores) -> None:
        self.runs = [FakeYamnet(s) for s in scores]
        self.calls = 0

    def __call__(self, window):
        out = self.runs[min(self.calls, len(self.runs) - 1)](window)
        self.calls += 1
        return out


class FakeYamnet:
    def __init__(self, scores=None) -> None:
        self.calls = 0
        self.scores = scores or {CLAP: 0.8}

    def __call__(self, window):
        assert window.shape == (clapmod.WINDOW,)
        self.calls += 1
        out = np.zeros(521, dtype=np.float32)
        for i, v in self.scores.items():
            out[i] = v
        return out


def detect(sig, clf=None, mode="both", sensitivity=50, monitor=(), chunk=800, count=2):
    det = HitSequenceDetector(clf or FakeYamnet(), mode, sensitivity, count=count)
    for t in monitor:
        det.monitor_onset(t)
    events = []
    for i in range(0, len(sig), chunk):
        events += det.feed(sig[i:i + chunk])
    return det, events


def doubles(events):
    """The toggles (a double, or a triple under count 3)."""
    return [e for e in events if e["event"] == "toggle"]


# ------------------------------------------------------------------ gate

def gate_onsets(sig, sensitivity=50):
    gate = TransientGate(sensitivity)
    blocks = DcBlocker()(sig[:len(sig) // 160 * 160].reshape(-1, 160).astype(np.float64))
    return [o for i, p in enumerate((blocks ** 2).mean(axis=1)) if (o := gate.feed(float(p), i)) is not None]


def noisy_room(seconds: float, floor_db: float, dc: float = 0.0):
    """Room noise at floor_db dBFS (per-block power), plus a DC offset."""
    return (RNG.standard_normal(int(seconds * RATE)) * 10 ** (floor_db / 20) + dc).astype(np.float32)


def mic(sig):
    return np.clip(sig, -1.0, 1.0)  # what a 16-bit capture can hold


# The live laptop microphone read level −16.8 dB with no clap: a DC offset of
# +0.144 (−16.9 dBFS) over −61 dBFS of room noise. Counted as level, it kept
# the gate shut (busy, the floor never learned). Then louder rooms, up to a
# −15 dBFS floor (only a near-clipping clap rises 8 dB over that).
@pytest.mark.parametrize("floor_db, dc, amp, sensitivity", [
    (-61, 0.1436, 0.05, 50),
    (-61, -0.17, 0.05, 50),
    (-60, 0.0, 0.05, 50),
    (-40, 0.0, 0.2, 50),
    (-25, 0.0, 0.6, 50),
    (-15, 0.0, 1.5, 100),
    (-15, 0.1, 1.5, 100),
])
def test_gate_adapts_to_the_noise_floor(floor_db, dc, amp, sensitivity):
    assert gate_onsets(mic(noisy_room(4.0, floor_db, dc)), sensitivity) == []
    onsets = gate_onsets(mic(add_clap(noisy_room(4.0, floor_db, dc), 2.0, amp)), sensitivity)
    assert len(onsets) == 1 and abs(onsets[0] * 160 / RATE - 2.0) <= 0.011


def test_gate_learns_a_room_that_got_louder():
    """A fan starts (−50 → −25 dBFS) and stays: a sustained sound at first,
    the floor a few seconds later; a clap after that counts."""
    sig = noisy_room(10.0, -50)
    sig[RATE // 2:] += noisy_room(9.5, -25)
    assert gate_onsets(mic(sig)) == []
    add_clap(sig, 8.0, 0.6)
    onsets = gate_onsets(mic(sig))
    assert len(onsets) == 1 and abs(onsets[0] * 160 / RATE - 8.0) <= 0.011


def test_the_detector_hears_a_double_clap_through_a_dc_offset():
    sig = noisy_room(3.0, -61, dc=0.1436)
    add_clap(add_clap(sig, 1.0, 0.05), 1.3, 0.05)
    det, events = detect(mic(sig))
    (d,) = doubles(events)
    assert d["label"] == "Clap"
    assert det.level_db < -40  # the meter shows the room, not the offset
    assert abs(float(det.ring[:RATE].mean())) < 0.01  # the model hears it without the offset


def test_gate_fires_once_per_clap_and_never_on_room_noise():
    assert gate_onsets(room(3.0)) == []
    onsets = gate_onsets(add_clap(room(3.0), 1.0))
    assert len(onsets) == 1 and abs(onsets[0] * 160 / RATE - 1.0) <= 0.011


def test_gate_ignores_a_sustained_sound():
    sig = room(3.0)
    t = np.arange(int(0.4 * RATE)) / RATE
    sig[RATE:RATE + len(t)] += (0.3 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)  # a vowel, a note
    assert gate_onsets(sig) == []


def test_gate_sensitivity_decides_a_soft_snap():
    sig = add_clap(room(3.0), 1.0, amp=0.02)
    assert gate_onsets(sig, sensitivity=0) == []
    assert len(gate_onsets(sig, sensitivity=100)) == 1


# ------------------------------------------------------------------ double-hit timing

def test_a_single_hit_is_no_double():
    det, events = detect(add_clap(room(3.0), 1.0))
    assert doubles(events) == []
    assert [e["label"] for e in events if e["event"] == "hit"] == ["Clap"]


def test_two_hits_300_ms_apart_are_a_double_classified_once():
    clf = FakeYamnet()
    sig = add_clap(add_clap(room(3.0), 1.0), 1.3)
    det, events = detect(sig, clf)
    (d,) = doubles(events)
    assert d["label"] == "Clap" and abs(d["gap"] - 0.3) < 0.02
    assert clf.calls == 1  # the second hit lies in the first one's window


def test_two_hits_900_ms_apart_are_not():
    det, events = detect(add_clap(add_clap(room(3.5), 1.0), 1.9))
    assert doubles(events) == []
    assert len([e for e in events if e["event"] == "hit"]) == 2


def test_a_hit_mirrored_in_the_monitor_is_dropped():
    sig = add_clap(add_clap(room(3.0), 1.0), 1.3)
    det, events = detect(sig, monitor=[1.31])  # the speakers played the second one
    assert doubles(events) == []
    assert any(e["event"] == "hit" and e["mirrored"] for e in events)


def test_a_third_hit_soon_after_is_applause_not_a_double():
    sig = room(3.5)
    for at in (1.0, 1.25, 1.5):
        add_clap(sig, at)
    assert doubles(detect(sig)[1]) == []


def test_applause_stays_no_double_once_its_first_hit_ages_out():
    """The hits list keeps 3 s; the first clap of a burst dropped out of it
    must still count as "not quiet" before the second (it used to make the
    last two a double 3 s late)."""
    sig = room(6.0)
    for at in (1.0, 1.25, 1.5):
        add_clap(sig, at)
    assert doubles(detect(sig)[1]) == []


def test_cooldown_after_a_double():
    sig = room(4.0)
    for at in (1.0, 1.3, 2.05, 2.35):  # the second pair starts 750 ms after the first ended
        add_clap(sig, at)
    assert len(doubles(detect(sig)[1])) == 1


@pytest.mark.parametrize("scores, mode", [
    ({353: 0.6}, "both"),              # a knock alone (family 0.15) stays below a clap-like bar of 0.18
    ({378: 0.6, CLAP: 0.3}, "both"),   # Typing wins
    ({0: 0.6, CLAP: 0.5}, "both"),     # Speech wins
    ({132: 0.9, SNAP: 0.3}, "both"),   # Music wins
    ({CLAP: 0.1}, "both"),             # below the bar at sensitivity 50
    ({CLAP: 0.8}, "snap"),             # a clap while only snaps count
])
def test_classifier_vetoes(scores, mode):
    sig = add_clap(add_clap(room(3.0), 1.0), 1.3)
    assert doubles(detect(sig, FakeYamnet(scores), mode=mode)[1]) == []


def test_snaps_count_in_snap_and_both():
    sig = add_clap(add_clap(room(3.0), 1.0, 0.1), 1.35, 0.1)
    for mode in ("snap", "both"):
        (d,) = doubles(detect(sig, FakeYamnet({SNAP: 0.6}), mode=mode)[1])
        assert d["label"] == "Snap"


# ------------------------------------------------------------------ triple (clap_count 3)
# Fewer false positives: three hits in a row, each gap the same as a double's,
# the same quiet before and after; a double alone does nothing.

def claps(*at, seconds=4.5, **kw):
    sig = room(seconds)
    for t in at:
        add_clap(sig, t, **kw)
    return sig


def test_three_hits_make_a_triple_under_count_3():
    clf = FakeYamnet()
    det, events = detect(claps(1.0, 1.3, 1.6), clf, count=3)
    (d,) = doubles(events)
    assert d["label"] == "Clap" and d["count"] == 3 and abs(d["gap"] - 0.6) < 0.02
    assert [round(g, 1) for g in d["gaps"]] == [0.3, 0.3]
    assert [(h["paired"], h["sequence"]) for h in det.history] == [(True, 3)] * 3


def test_a_double_alone_does_not_toggle_under_count_3():
    det, events = detect(claps(1.0, 1.3), count=3)
    assert doubles(events) == []
    assert [h["paired"] for h in det.history] == [False, False]


def test_count_2_still_toggles_on_the_double():
    (d,) = doubles(detect(claps(1.0, 1.3))[1])
    assert d["count"] == 2 and d["gaps"] == [d["gap"]]


def test_a_gap_too_long_starts_the_triple_again():
    assert doubles(detect(claps(1.0, 1.3, 2.2), count=3)[1]) == []  # 900 ms: no third hit
    (d,) = doubles(detect(claps(1.0, 1.3, 2.2, 2.5, 2.8), count=3)[1])  # the last three count
    assert abs(d["at"] - 2.8) < 0.05 and abs(d["gap"] - 0.6) < 0.02


def test_a_fourth_hit_soon_after_is_applause_not_a_triple():
    assert doubles(detect(claps(1.0, 1.25, 1.5, 1.75), count=3)[1]) == []


def test_cooldown_after_a_triple():
    assert len(doubles(detect(claps(1.0, 1.3, 1.6, 2.3, 2.6, 2.9), count=3)[1])) == 1  # 700 ms after
    assert len(doubles(detect(claps(1.0, 1.3, 1.6, 2.8, 3.1, 3.4), count=3)[1])) == 2  # 1.2 s after


@pytest.mark.parametrize("mode, toggles", [("both", 1), ("clap", 0), ("snap", 0)])
def test_a_mixed_triple_counts_only_for_both(mode, toggles):
    """Clap, snap, clap, far enough apart for windows of their own: a
    Clap+Snap triple, like a mixed double."""
    clf = FakeSequence({CLAP: 0.6}, {SNAP: 0.6}, {CLAP: 0.6})
    det, events = detect(claps(1.0, 1.45, 1.9), clf, mode=mode, count=3)
    assert clf.calls == 3 and [h["kind"] for h in det.history] == ["Clap", "Snap", "Clap"]
    found = doubles(events)
    assert len(found) == toggles and all(d["label"] == "Clap+Snap" for d in found)


def test_a_triple_of_snaps_is_a_snap():
    (d,) = doubles(detect(claps(1.0, 1.35, 1.7, amp=0.1, tau=0.003), FakeYamnet({SNAP: 0.6}), mode="snap",
                          count=3)[1])
    assert d["label"] == "Snap"


def test_a_triple_needs_partners_throughout():
    """The middle hit clearly typing: no triple, though the outer two pass."""
    clf = FakeSequence({CLAP: 0.6}, {378: 0.5, CLAP: 0.05}, {CLAP: 0.6})
    assert doubles(detect(claps(1.0, 1.45, 1.9), clf, count=3)[1]) == []


def test_a_mirrored_hit_breaks_a_triple():
    assert doubles(detect(claps(1.0, 1.3, 1.6), monitor=[1.31], count=3)[1]) == []


def test_sequence_label():
    assert clapmod.sequence_label(["Clap", "", "Clap"]) == "Clap"
    assert clapmod.sequence_label(["", "Snap", ""]) == "Snap"
    assert clapmod.sequence_label(["", "", ""]) == "Clap"
    assert clapmod.sequence_label(["Snap", "Snap", "Clap"]) == "Clap+Snap"


@pytest.mark.parametrize("value, want", [(2, 2), (3, 3), ("3", 3), (" 2 ", 2)])
def test_check_count(value, want):
    assert clapmod.check_count(value) == want


@pytest.mark.parametrize("value", [1, 4, "2.5", "two", None, True])
def test_check_count_refuses(value):
    with pytest.raises(ValueError, match="clap_count"):
        clapmod.check_count(value)


# ------------------------------------------------------------------ model file

def test_a_model_with_the_wrong_checksum_is_not_kept(tmp_path):
    src = tmp_path / "src.tflite"
    src.write_bytes(b"not the model")
    dest = tmp_path / "cache" / "yamnet.tflite"
    with pytest.raises(clapmod.CaptureError):
        clapmod.fetch_model(dest, url=src.as_uri())
    assert not dest.exists() and not dest.with_suffix(".part").exists()


# ------------------------------------------------------------------ settings

def test_settings_migration_fills_and_checks_the_clap_keys():
    state = new_state()
    s = state["settings"]
    assert (s["widget_clap"], s["clap_source"], s["clap_sensitivity"], s["clap_count"]) == ({"1": "off"}, "", 50, 2)
    assert "clap" not in s and "clap_last" not in s
    s.update(widget_clap={"1": "loud", "2": "snap", "9": "both"}, widgets=2, clap_source="a b",
             clap_sensitivity=250, clap_count=5)
    migrate(state)
    assert (s["widget_clap"], s["clap_source"], s["clap_sensitivity"]) == ({"1": "off", "2": "snap"}, "", 100)
    assert s["clap_count"] == 2  # only 2 or 3
    again = copy.deepcopy(state)
    assert migrate(again) is False
    for key in ("widget_clap", "clap_source", "clap_sensitivity", "clap_count"):  # an old file without them
        del state["settings"][key]
    migrate(state)
    assert state["settings"]["widget_clap"] == {"1": "off", "2": "off"}
    assert state["settings"]["clap_count"] == 2


@pytest.mark.parametrize("old, want", [("snap", "snap"), ("both", "both"), ("off", "off"), ("loud", "off")])
def test_the_global_clap_mode_becomes_widget_1s(old, want):
    """Before the popup chose it per widget, settings.clap was global (and
    clap_last the popup switch's memory): widget 1 had it."""
    state = new_state()
    s = state["settings"]
    del s["widget_clap"]
    s.update(clap=old, clap_last="clap", widgets=2)
    assert migrate(state) is True
    assert s["widget_clap"] == {"1": want, "2": "off"}
    assert "clap" not in s and "clap_last" not in s


def test_union_mode_and_matching():
    assert clapmod.union_mode(["off", "off"]) == "off"
    assert clapmod.union_mode(["clap", "off"]) == "clap"
    assert clapmod.union_mode(["clap", "snap"]) == "both"
    assert clapmod.union_mode(["snap", "both"]) == "both"
    assert clapmod.mode_matches("clap", "Clap") and not clapmod.mode_matches("clap", "Snap")
    assert clapmod.mode_matches("snap", "Snap") and not clapmod.mode_matches("snap", "Clap+Snap")
    assert clapmod.mode_matches("both", "Clap+Snap") and not clapmod.mode_matches("off", "Clap")


# ------------------------------------------------------------------ ctl

@pytest.fixture
def no_capture(monkeypatch):
    """The service starts nothing: no model download, no microphone."""
    applied = []

    def apply(self, mode, source, sensitivity, count=2):
        applied.append((mode, source, sensitivity))
        self.mode, self.source, self.sensitivity, self.count = mode, source, sensitivity, count
        self.state = "off" if mode == "off" else "listening"

    monkeypatch.setattr(clapmod.ClapService, "apply", apply)
    monkeypatch.setattr(clapmod, "missing", lambda: [])
    return applied


def test_ctl_clap_commands(tmp_path, monkeypatch, no_capture):
    async def sources():
        return [{"name": "mic1", "description": "Digital Microphone", "default": True}]
    monkeypatch.setattr(clapmod, "list_sources", sources)

    async def body(d):
        g = await cmd(d, "get")
        assert g["clap"] == "off" and g["clap_status"]["available"] is True and "clap_last" not in g
        g = await cmd(d, "clap", "snap")
        assert g["clap"] == "snap" and d.settings["widget_clap"] == {"1": "snap"}
        assert no_capture[-1] == ("snap", "", 50)
        g = await cmd(d, "set", "clap=off")
        assert g["clap"] == "off" and no_capture[-1] == ("off", "", 50)
        g = await cmd(d, "set", "clap_sensitivity=80")
        assert g["clap_sensitivity"] == 80
        g = await cmd(d, "clap-source", "mic1")
        assert g["clap_source"] == "mic1"
        g = await cmd(d, "set", "clap_source=default")
        assert g["clap_source"] == ""
        with pytest.raises(UserError):
            await cmd(d, "clap", "loud")
        assert (await cmd(d, "clap-sources"))["sources"][0]["name"] == "mic1"
        meter = await cmd(d, "clap-meter")
        assert meter["clap"] == "off" and meter["level"] is None and meter["last"] is None
        assert no_capture[0] == ("off", "", 50) and no_capture[-1] == ("off", "", 80)
        saved = d.state_path.read_text()
        assert '"clap_sensitivity": 80' in saved
        assert (await cmd(d, "get"))["clap_count"] == 2
        g = await cmd(d, "clap-count", "3")
        assert g["clap_count"] == 3 and d.clap.count == 3
        for bad in ("4", "1", "two", "2.5"):
            with pytest.raises(UserError, match="clap_count"):
                await cmd(d, "set", f"clap_count={bad}")
        assert d.settings["clap_count"] == 3
        assert (await cmd(d, "clap-hits"))["count"] == 3
        assert '"clap_count": 3' in d.state_path.read_text()
        g = await cmd(d, "set", "clap_count=2")
        assert g["clap_count"] == 2 and d.clap.count == 2
    run(tmp_path, mock_state(mock_1={}), body)


def test_clap_is_refused_without_the_extra(tmp_path, monkeypatch, no_capture):
    monkeypatch.setattr(clapmod, "missing", lambda: ["numpy", "ai-edge-litert"])

    async def body(d):
        with pytest.raises(UserError, match="requirements-clap.txt"):
            await cmd(d, "clap", "both")
        assert d.settings["widget_clap"] == {"1": "off"}
        g = await cmd(d, "clap", "off")  # off is always fine
        assert g["clap_status"]["available"] is False and "requirements-clap.txt" in g["clap_status"]["install"]
    run(tmp_path, mock_state(mock_1={}), body)


def test_each_widget_has_its_own_mode(tmp_path, no_capture):
    async def body(d):
        await cmd(d, "set", "widgets=2")
        await cmd(d, "widget-bulbs", "mock:1,mock:2")
        await cmd(d, "@2", "widget-bulbs", "mock:2,mock:3")
        g = await cmd(d, "@2", "clap", "clap")
        assert g["clap"] == "clap" and (await cmd(d, "get"))["clap"] == "off"
        assert no_capture[-1][0] == "clap"  # capture runs: one widget listens
        await cmd(d, "clap", "snap")
        assert no_capture[-1][0] == "both"  # what any widget wants
        assert d.settings["widget_clap"] == {"1": "snap", "2": "clap"}
        assert (await cmd(d, "clap-meter"))["clap"] == "both"

        d.on_clap({"label": "Clap", "score": 0.8})  # widget 2 only
        await settle(d)
        assert [d.bulbs[f"mock:{i}"].power for i in (1, 2, 3)] == [False, True, True]
        d.on_clap({"label": "Snap", "score": 0.8})  # widget 1 (bulbs 1, 2): one is on, so off
        assert [d.bulbs[f"mock:{i}"].power for i in (1, 2, 3)] == [False, False, True]
        await cmd(d, "@2", "clap", "both")
        d.on_clap({"label": "Snap", "score": 0.8})  # widget 1 switches 1 and 2 on; widget 2 then only 3
        assert [d.bulbs[f"mock:{i}"].power for i in (1, 2, 3)] == [True, True, False]
        d.on_clap({"label": "Clap+Snap", "score": 0.8})  # a mixed pair: only "both" (widget 2: 2 is on)
        assert [d.bulbs[f"mock:{i}"].power for i in (1, 2, 3)] == [True, False, False]

        await cmd(d, "set", "widgets=1")  # widget 2 goes, and its mode with it
        assert d.settings["widget_clap"] == {"1": "snap"} and no_capture[-1][0] == "snap"
        await cmd(d, "clap", "off")
        assert no_capture[-1][0] == "off"  # no widget listens: capture stops
    run(tmp_path, mock_state(mock_1={}, mock_2={}, mock_3={}), body)


def test_a_double_toggles_widget_1s_selection(tmp_path, no_capture):
    async def body(d):
        await cmd(d, "clap", "both")
        await cmd(d, "select", "mock:1")
        d.on_clap({"label": "Clap", "score": 0.8})
        await settle(d)
        assert d.bulbs["mock:1"].power is True and d.bulbs["mock:2"].power is False
        d.on_clap({"label": "Snap", "score": 0.7})
        assert d.bulbs["mock:1"].power is False
        await cmd(d, "select", "all")
        d.on_clap({"label": "Clap", "score": 0.8})  # none on: all on
        assert d.bulbs["mock:1"].power and d.bulbs["mock:2"].power
        d.sleeping = True
        d.on_clap({"label": "Clap", "score": 0.8})  # asleep: hands off
        assert d.bulbs["mock:1"].power
        d.sleeping = False
    run(tmp_path, mock_state(mock_1={}, mock_2={}), body)


def test_the_service_reports_unavailable_without_the_extra(monkeypatch):
    monkeypatch.setattr(clapmod, "missing", lambda: ["numpy"])

    async def muted(source):
        return None
    monkeypatch.setattr(clapmod, "source_muted", muted)

    async def main():
        svc = clapmod.ClapService(lambda ev: None)
        svc.apply("both", "", 50)
        await asyncio.sleep(0.05)
        out = svc.describe()
        svc.stop()
        return out
    st = asyncio.run(main())
    assert st["state"] == "unavailable" and st["available"] is False and st["missing"] == ["numpy"]


def test_a_muted_source_is_reported_while_listening(monkeypatch):
    asked = []

    async def muted(source):
        asked.append(source)
        return True
    monkeypatch.setattr(clapmod, "source_muted", muted)
    monkeypatch.setattr(clapmod, "missing", lambda: ["numpy"])  # no capture, only the mute watch

    async def main():
        svc = clapmod.ClapService(lambda ev: None)
        assert svc.describe()["muted"] is None
        svc.apply("clap", "mic1", 50)
        await asyncio.sleep(0.05)
        on = svc.describe()["muted"]
        svc.apply("off", "mic1", 50)
        return on, svc.describe()["muted"]
    assert asyncio.run(main()) == (True, None)
    assert asked == ["mic1"]


@pytest.mark.parametrize("out, want", [(b"Mute: yes\n", True), (b"Mute: no\n", False), (b"", None)])
def test_source_muted_reads_pactl(monkeypatch, out, want):
    calls = []

    class Proc:
        async def communicate(self):
            return out, b""

    async def spawn(*args, **kw):
        calls.append((args, kw["env"].get("LC_ALL")))
        return Proc()
    monkeypatch.setattr(clapmod.asyncio, "create_subprocess_exec", spawn)
    assert asyncio.run(clapmod.source_muted("")) is want
    assert calls == [(("pactl", "get-source-mute", "@DEFAULT_SOURCE@"), "C")]


# ------------------------------------------------------------------ tolerance (2026-10-21)
# "the clap&snip detection is not sensitive enough … dissimilar claps seem to
# get ignored the most": a hit is judged by the clap family's summed score,
# the bar is lower for a clap-like shape, and the second hit of a double
# needs only to be not clearly speech, music or typing.

def test_the_family_sum_accepts_a_clap_the_top_1_label_missed():
    """Silence and Inside, small room win the top spot; Hands + Clapping +
    Cap gun add up past the bar."""
    sig = add_clap(add_clap(room(3.0), 1.0), 1.3)
    scores = {494: 0.4, 500: 0.2, 56: 0.08, CLAP: 0.06, 425: 0.1}  # family 0.19
    (d,) = doubles(detect(sig, FakeYamnet(scores))[1])
    assert d["label"] == "Clap"


@pytest.mark.parametrize("first, second, at2, kw2, label", [
    # loud clap, then a soft one the model hears as room and silence
    ({CLAP: 0.5}, {494: 0.4, 500: 0.2, CLAP: 0.02}, 1.4, {"amp": 0.03}, "Clap"),
    # a clap, then a snap-like hit (bright, short, little score)
    ({CLAP: 0.45}, {SNAP: 0.12, 425: 0.1}, 1.45, {"amp": 0.1, "tau": 0.003}, "Clap+Snap"),
    # a snap, then a clap of no clear kind: the pair is a double snap
    ({SNAP: 0.5}, {494: 0.5}, 1.4, {"amp": 0.2}, "Snap"),
    # a clap, then a slower one (not clap-like: needs the partner bar, 0.075)
    ({CLAP: 0.5}, {CLAP: 0.06, 432: 0.1}, 1.5, {"amp": 0.3, "tau": 0.045}, "Clap"),
])
def test_dissimilar_hits_make_a_double(first, second, at2, kw2, label):
    sig = add_clap(add_clap(room(3.5), 1.0, 0.5), at2, **kw2)
    clf = FakeSequence(first, second)
    det, events = detect(sig, clf)
    (d,) = doubles(events)
    assert d["label"] == label and abs(d["gap"] - (at2 - 1.0)) < 0.02
    assert clf.calls == 2  # far enough apart for windows of their own
    a, b = list(det.history)
    assert a["accepted"] and b["partner"] and a["paired"] and b["paired"]


def test_a_slow_partner_shows_its_shape():
    sig = add_clap(add_clap(room(3.5), 1.0, 0.5), 1.5, 0.3, tau=0.045)
    det, _events = detect(sig, FakeSequence({CLAP: 0.5}, {CLAP: 0.06, 432: 0.1}))
    a, b = list(det.history)
    assert a["clap_like"] and not b["clap_like"] and b["decay_ms"] > clapmod.CLAP_DECAY_MS
    assert not b["accepted"] and b["partner"] and b["why"].startswith("family 0.11 <")


@pytest.mark.parametrize("second", [
    {378: 0.5, CLAP: 0.05},   # clearly typing
    {0: 0.7, CLAP: 0.1},      # clearly speech
    {132: 0.6},               # clearly music
])
def test_a_partner_that_is_clearly_something_else_is_no_double(second):
    sig = add_clap(add_clap(room(3.5), 1.0, 0.5), 1.4, 0.2)
    det, events = detect(sig, FakeSequence({CLAP: 0.5}, second))
    assert doubles(events) == []
    a, b = list(det.history)
    assert a["accepted"] and not b["partner"] and "beats" in b["why"]


def test_two_partners_without_an_accepted_hit_are_no_double():
    sig = add_clap(add_clap(room(3.5), 1.0, 0.3, tau=0.045), 1.5, 0.3, tau=0.045)  # not clap-like
    assert doubles(detect(sig, FakeYamnet({CLAP: 0.12}))[1]) == []  # partner bar 0.075, first bar 0.3


def test_pairs_120_and_800_ms_apart_count():
    for gap in (0.13, 0.78):
        sig = add_clap(add_clap(room(3.5), 1.0), 1.0 + gap)
        (d,) = doubles(detect(sig)[1])
        assert abs(d["gap"] - gap) < 0.02


@pytest.mark.parametrize("seed", range(6))
def test_gate_hears_a_soft_second_hit_on_the_first_ones_reverb(monkeypatch, seed):
    """A loud clap rings in the room; the second one, 14 dB softer and 200 ms
    later, rises on its tail, not out of quiet (the old gate held it busy)."""
    monkeypatch.setattr(sys.modules[__name__], "RNG", np.random.default_rng(seed))
    sig = add_clap(room(3.0), 1.0, 0.6, reverb=0.3, rt60=0.6)
    add_clap(sig, 1.2, 0.12)
    onsets = gate_onsets(mic(sig))
    assert [round(o * 160 / RATE, 2) for o in onsets] == [1.0, 1.2]
    (d,) = doubles(detect(mic(sig), FakeYamnet({CLAP: 0.4}))[1])
    assert abs(d["gap"] - 0.2) < 0.02


@pytest.mark.parametrize("seed", range(6))
def test_the_tail_rule_does_not_split_one_ringing_clap(monkeypatch, seed):
    monkeypatch.setattr(sys.modules[__name__], "RNG", np.random.default_rng(seed))
    for reverb, rt60 in ((0.3, 0.6), (0.4, 0.6), (0.3, 1.0)):
        sig = add_clap(room(3.0), 1.0, 0.6, reverb=reverb, rt60=rt60)
        assert len(gate_onsets(mic(sig))) == 1


@pytest.mark.parametrize("scores", [
    {378: 0.6, 380: 0.3, CLAP: 0.1},   # what typing sounds like to the model
    {CLAP: 0.4, 378: 0.3},             # the model fooled: still a burst, not a double
])
def test_a_typing_burst_never_toggles(scores):
    sig = room(4.0)
    for k in range(8):
        add_clap(sig, 1.0 + 0.17 * k + (0.03 if k % 2 else 0.0), 0.08, tau=0.004)
    det, events = detect(sig, FakeYamnet(scores), sensitivity=100)
    assert doubles(events) == []
    assert len([e for e in events if e["event"] == "hit"]) >= 6


def test_speech_never_toggles():
    """Syllables ring on (the gate holds them), and transients the model
    hears as speech are refused even as partners."""
    sig = room(4.0)
    t = np.arange(int(0.18 * RATE)) / RATE
    for at in (1.0, 1.3, 1.6, 1.9):
        start = int(at * RATE)
        sig[start:start + len(t)] += (0.2 * np.sin(2 * np.pi * 180 * t) * np.minimum(1, t / 0.005)).astype(np.float32)
    assert doubles(detect(sig, FakeYamnet({0: 0.8}), sensitivity=100)[1]) == []
    sig = add_clap(add_clap(room(3.0), 1.0), 1.3)
    assert doubles(detect(sig, FakeYamnet({0: 0.6, 1: 0.2, CLAP: 0.25}), sensitivity=100)[1]) == []


def test_sensitivity_scales_the_bars():
    lo, mid, hi = (clapmod.bars(s) for s in (0, 50, 100))
    assert lo["first"] > mid["first"] > hi["first"] > 0
    assert lo["partner"] > mid["partner"] > hi["partner"] > 0
    assert lo["veto"] < mid["veto"] < hi["veto"]  # high: only a clear confuser refuses a partner
    sig = add_clap(add_clap(room(3.0), 1.0), 1.3)
    scores = {CLAP: 0.15}  # clap-like bar: 0.3 (sensitivity 0), 0.06 (100)
    assert doubles(detect(sig, FakeYamnet(scores), sensitivity=0)[1]) == []
    assert len(doubles(detect(sig, FakeYamnet(scores), sensitivity=100)[1])) == 1


def test_pair_label():
    assert clapmod.pair_label("Clap", "Clap") == "Clap"
    assert clapmod.pair_label("Snap", "") == "Snap" and clapmod.pair_label("", "Snap") == "Snap"
    assert clapmod.pair_label("", "") == "Clap"
    assert clapmod.pair_label("Snap", "Clap") == "Clap+Snap"


def test_the_history_keeps_the_last_30_hits():
    sig = room(30.0)
    for k in range(35):
        add_clap(sig, 0.5 + 0.8 * k)
    det, _events = detect(sig, FakeYamnet({494: 0.5}))
    assert len(det.history) == 30
    h = det.history[-1]
    assert set(h) >= {"at", "over_db", "rise_db", "decay_ms", "clap_like", "family", "kind", "top", "accepted",
                      "partner", "why", "paired", "sequence"}
    assert h["top"][0] == ["Silence", 0.5] and h["accepted"] is False and h["over_db"] > 20


# ------------------------------------------------------------------ through the daemon

def feed_service(d, sig, now0=100.0, chunk=800):
    for i in range(0, len(sig), chunk):
        part = sig[i:i + chunk]
        d.clap._mic(part, now0 + (i + len(part)) / RATE)


@pytest.mark.parametrize("mode", ["snap", "both"])
def test_a_double_snap_toggles_through_the_daemon(tmp_path, no_capture, mode):
    """The live journal of 2026-10-21 seemed to show double snaps that did
    nothing; they had toggled (the grep for "clap" hid "double snap" lines).
    Pinned end to end: microphone samples, detector, service, daemon."""
    async def body(d):
        await cmd(d, "clap", mode)
        d.clap.detector = HitSequenceDetector(FakeYamnet({SNAP: 0.6}), d.clap.mode, 50)
        feed_service(d, add_clap(add_clap(room(3.0), 1.0, 0.1, tau=0.003), 1.35, 0.1, tau=0.003))
        await settle(d)
        assert d.bulbs["mock:1"].power is True and d.clap.last_double["label"] == "Snap"
        hits = (await cmd(d, "clap-hits"))["hits"]
        assert len(hits) == 2 and all(h["paired"] and h["kind"] == "Snap" for h in hits)
        assert {"time", "age", "family", "top", "why"} <= set(hits[0]) and "at" not in hits[0]
        d.clap.detector = HitSequenceDetector(FakeYamnet({SNAP: 0.6}), d.clap.mode, 50)
        feed_service(d, add_clap(add_clap(room(3.0), 1.0, 0.1, tau=0.003), 1.35, 0.1, tau=0.003), now0=200.0)
        await settle(d)
        assert d.bulbs["mock:1"].power is False
    run(tmp_path, mock_state(mock_1={}), body)


@pytest.mark.parametrize("mode", ["clap", "both"])
def test_a_triple_clap_toggles_through_the_daemon_under_count_3(tmp_path, no_capture, mode):
    async def body(d):
        await cmd(d, "clap", mode)
        await cmd(d, "set", "clap_count=3")
        d.clap.detector = HitSequenceDetector(FakeYamnet(), d.clap.mode, 50, count=d.clap.count)
        feed_service(d, add_clap(add_clap(room(3.0), 1.0), 1.3))  # a double: nothing
        await settle(d)
        assert d.bulbs["mock:1"].power is False and d.clap.last_double is None
        feed_service(d, add_clap(add_clap(add_clap(room(3.0), 1.0), 1.3), 1.6), now0=200.0)
        await settle(d)
        assert d.bulbs["mock:1"].power is True and d.clap.last_double["count"] == 3
        hits = (await cmd(d, "clap-hits"))["hits"]
        assert [h["sequence"] for h in hits] == [0, 0, 3, 3, 3]
        assert (await cmd(d, "clap-meter"))["double"]["count"] == 3
    run(tmp_path, mock_state(mock_1={}), body)


def test_clap_hits_without_a_detector(tmp_path, no_capture):
    async def body(d):
        out = await cmd(d, "clap-hits")
        assert out["hits"] == [] and out["bars"]["first"] == 0.3 and out["sensitivity"] == 50 and out["count"] == 2
    run(tmp_path, mock_state(mock_1={}), body)


# --------------------------------------------------------------------- a source that is gone

@pytest.fixture
def warnings(monkeypatch):
    said = []
    monkeypatch.setattr(clapmod.log, "warning", lambda msg, *args: said.append(msg % args))
    monkeypatch.setattr(clapmod, "_missing_warned", set())
    return said


def _sources(monkeypatch, names=None, error=False):
    asked = []

    async def sources():
        asked.append(1)
        if error:
            raise clapmod.CaptureError("pactl: no pactl")
        return [{"name": n, "description": n, "default": False} for n in names]
    monkeypatch.setattr(clapmod, "list_sources", sources)
    return asked


def test_a_missing_source_falls_back_to_the_default_with_one_warning(monkeypatch, warnings):
    _sources(monkeypatch, ["mic1"])

    async def main():
        out = []
        for _ in range(2):
            cap = clapmod.Capture(lambda *_: None, "usb-mic")
            await cap.check_source()
            out.append((cap.source, cap.missing))
        return out
    assert asyncio.run(main()) == [("", "usb-mic"), ("", "usb-mic")]
    assert warnings == ["clap detection: source usb-mic not found; using the default"]


def test_a_source_that_is_there_is_kept_and_warned_again_once_gone(monkeypatch, warnings):
    names = ["usb-mic"]
    _sources(monkeypatch, names)

    async def check():
        cap = clapmod.Capture(lambda *_: None, "usb-mic")
        await cap.check_source()
        return cap.source, cap.missing
    assert asyncio.run(check()) == ("usb-mic", None)
    names.clear()
    assert asyncio.run(check()) == ("", "usb-mic")
    names.append("usb-mic")
    assert asyncio.run(check()) == ("usb-mic", None)
    names.clear()
    assert asyncio.run(check()) == ("", "usb-mic")
    assert len(warnings) == 2


def test_an_unreadable_source_list_keeps_the_source(monkeypatch, warnings):
    _sources(monkeypatch, error=True)
    cap = clapmod.Capture(lambda *_: None, "usb-mic")
    asyncio.run(cap.check_source())
    assert (cap.source, cap.missing, warnings) == ("usb-mic", None, [])


@pytest.mark.parametrize("source, monitor", [("", False), ("", True)])
def test_the_default_and_the_monitor_are_not_checked(monkeypatch, warnings, source, monitor):
    asked = _sources(monkeypatch, [])
    cap = clapmod.Capture(lambda *_: None, source, monitor=monitor)
    asyncio.run(cap.check_source())
    assert (cap.source, cap.missing, asked) == (source, None, [])


def test_the_check_runs_before_the_recorder_starts(monkeypatch, warnings):
    _sources(monkeypatch, ["mic1"])
    seen = []

    async def spawn(*cmd, **kw):
        seen.append(cmd)
        raise OSError("no recorder in tests")
    monkeypatch.setattr(clapmod.asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(clapmod.shutil, "which", lambda name: "/usr/bin/" + name if name == "parec" else None)
    cap = clapmod.Capture(lambda *_: None, "gone")
    with pytest.raises(OSError):
        asyncio.run(cap.run())
    assert seen and not any(a.startswith("--device") for a in seen[0])


def test_describe_reports_the_missing_source(monkeypatch):
    svc = clapmod.ClapService(lambda ev: None)
    assert svc.describe()["source_missing"] is None
    svc.capture = clapmod.Capture(lambda *_: None, "")
    svc.capture.missing = "usb-mic"
    assert svc.describe()["source_missing"] == "usb-mic"
    svc.stop()
    assert svc.describe()["source_missing"] is None


# --------------------------------------------------------------------- the test run (clap off, Sound page open)

@pytest.fixture
def fake_run(monkeypatch):
    """_run without a model or a microphone: a detector on FakeYamnet, then
    it waits (as a capture would) until cancelled. Records each start."""
    runs = []

    async def _run(self):
        runs.append({"mode": self.run_mode(), "source": self.source, "sensitivity": self.sensitivity,
                     "count": self.count, "test": self.test})
        self.detector = HitSequenceDetector(FakeYamnet(), self.run_mode(), self.sensitivity, count=self.count)
        self.state = "listening"
        await asyncio.Event().wait()

    async def muted(source):
        return None
    monkeypatch.setattr(clapmod.ClapService, "_run", _run)
    monkeypatch.setattr(clapmod, "source_muted", muted)
    monkeypatch.setattr(clapmod, "missing", lambda: [])
    monkeypatch.setattr(clapmod, "LEASE_S", 0.2)
    return runs


def feed_svc(svc, sig, now0=100.0, chunk=800):
    for i in range(0, len(sig), chunk):
        part = sig[i:i + chunk]
        svc._mic(part, now0 + (i + len(part)) / RATE)


def test_listen_starts_a_test_run_that_never_toggles(fake_run):
    toggled = []

    async def main():
        svc = clapmod.ClapService(toggled.append)
        svc.apply("off", "mic1", 60, 2)
        svc.listen()
        await asyncio.sleep(0.02)
        assert fake_run == [{"mode": "both", "source": "mic1", "sensitivity": 60, "count": 2, "test": True}]
        meter = svc.meter()
        assert meter["test"] is True and meter["state"] == "listening" and meter["level"] is not None
        feed_svc(svc, add_clap(add_clap(room(3.0), 1.0), 1.3))
        out = svc.last_double, svc.hits(), svc.meter()["double"]
        svc.stop()
        return out
    last, hits, double = asyncio.run(main())
    assert toggled == []  # shown, never switched
    assert last["label"] == "Clap" and last["test"] is True and double["test"] is True
    assert len(hits) == 2 and all(h["paired"] for h in hits)


def test_the_test_run_stops_when_the_lease_runs_out(fake_run):
    async def main():
        svc = clapmod.ClapService(lambda ev: None)
        svc.apply("off", "", 50)
        svc.listen()
        await asyncio.sleep(0.12)
        svc.listen()  # renewed: still running past the first lease
        await asyncio.sleep(0.12)
        alive = svc.test, svc.state, len(fake_run)
        task = svc.task
        await asyncio.sleep(0.25)
        await asyncio.sleep(0)
        return alive, (svc.test, svc.state, svc.task, svc.detector, svc.lease_task), task.cancelled()
    alive, after, cancelled = asyncio.run(main())
    assert alive == (True, "listening", 1)  # one start, renewed
    assert after == (False, "off", None, None, None) and cancelled  # the microphone is free again


def test_listen_changes_nothing_while_clap_is_on(fake_run):
    async def main():
        svc = clapmod.ClapService(lambda ev: None)
        svc.apply("clap", "", 50)
        await asyncio.sleep(0.02)
        task = svc.task
        svc.listen()
        await asyncio.sleep(0.02)
        out = svc.test, svc.task is task, svc.lease_task, svc.describe()["test"]
        svc.stop()
        return out
    assert asyncio.run(main()) == (False, True, None, False)
    assert [r["mode"] for r in fake_run] == ["clap"]


def test_listen_does_nothing_without_the_extra(fake_run, monkeypatch):
    monkeypatch.setattr(clapmod, "missing", lambda: ["numpy"])

    async def main():
        svc = clapmod.ClapService(lambda ev: None)
        svc.listen()
        await asyncio.sleep(0.02)
        return svc.test, svc.task
    assert asyncio.run(main()) == (False, None) and fake_run == []


def test_clap_turned_on_during_a_test_takes_over_and_toggles(fake_run):
    toggled = []

    async def main():
        svc = clapmod.ClapService(toggled.append)
        svc.listen()
        await asyncio.sleep(0.02)
        svc.apply("clap", "", 50)
        await asyncio.sleep(0.02)
        state = svc.test, svc.lease_task, svc.state
        feed_svc(svc, add_clap(add_clap(room(3.0), 1.0), 1.3))
        await asyncio.sleep(0.3)  # past the old lease: the normal run stays
        out = state, svc.state, svc.task is not None
        svc.stop()
        return out
    state, later, running = asyncio.run(main())
    assert state == (False, None, "listening") and later == "listening" and running
    assert [(r["mode"], r["test"]) for r in fake_run] == [("both", True), ("clap", False)]
    assert len(toggled) == 1 and toggled[0]["label"] == "Clap"


def test_settings_reach_the_test_run_like_the_normal_one(fake_run):
    async def main():
        svc = clapmod.ClapService(lambda ev: None)
        svc.listen()
        await asyncio.sleep(0.02)
        svc.apply("off", "", 80, 3)  # sensitivity, count: the detector takes them
        await asyncio.sleep(0.02)
        det = svc.detector
        kept = len(fake_run), det.count, det.mode, svc.test
        svc.apply("off", "mic2", 80, 3)  # another microphone: a new run, still a test
        await asyncio.sleep(0.02)
        out = kept, svc.test, svc.lease_task is not None
        svc.stop()
        return out
    kept, test, leased = asyncio.run(main())
    assert kept == (1, 3, "both", True) and test and leased
    assert fake_run[-1] == {"mode": "both", "source": "mic2", "sensitivity": 80, "count": 3, "test": True}


def test_clap_meter_listen_through_the_daemon(tmp_path, fake_run):
    async def body(d):
        meter = await cmd(d, "clap-meter")
        assert meter["test"] is False and meter["state"] == "off" and fake_run == []
        meter = await cmd(d, "clap-meter", "--listen")
        await asyncio.sleep(0.02)
        meter = await cmd(d, "clap-meter", "--listen")
        assert meter["clap"] == "off" and meter["test"] is True and meter["state"] == "listening"
        assert len(fake_run) == 1 and fake_run[0]["mode"] == "both"
        feed_service(d, add_clap(add_clap(room(3.0), 1.0), 1.3))
        await settle(d)
        assert d.bulbs["mock:1"].power is False  # a test hit switches nothing
        hits = await cmd(d, "clap-hits")
        assert hits["test"] is True and len(hits["hits"]) == 2
        assert (await cmd(d, "clap-meter"))["double"]["test"] is True
        await cmd(d, "clap", "both")  # on: the normal run, the flag changes nothing
        await asyncio.sleep(0.02)
        meter = await cmd(d, "clap-meter", "--listen")
        assert meter["test"] is False and meter["clap"] == "both" and fake_run[-1]["test"] is False
    run(tmp_path, mock_state(mock_1={}), body)
