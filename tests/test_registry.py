"""The registry hides the mock backend unless asked for; requires() for install.sh."""

import pytest

from haus import backends
from haus.common import UserError


@pytest.fixture
def fresh(monkeypatch):
    monkeypatch.setattr(backends, "_registry", None)
    yield monkeypatch
    backends._registry = None


def test_mock_hidden_by_default(fresh):
    fresh.delenv("LICHTHAUS_BACKENDS", raising=False)
    fresh.delenv("LICHTHAUS_MOCK", raising=False)
    reg = backends.registry()
    assert "mock" not in reg and "mesh" in reg
    assert all(reg[k].experimental for k in reg if k != "mesh")
    assert not reg["mesh"].experimental


@pytest.mark.parametrize("env", [{"LICHTHAUS_MOCK": "1"}, {"LICHTHAUS_BACKENDS": "mock,wiz"}])
def test_mock_shown_when_asked(fresh, env):
    fresh.delenv("LICHTHAUS_BACKENDS", raising=False)
    for k, v in env.items():
        fresh.setenv(k, v)
    assert "mock" in backends.registry()


def test_requires(fresh):
    fresh.delenv("LICHTHAUS_BACKENDS", raising=False)
    fresh.delenv("LICHTHAUS_MOCK", raising=False)
    assert backends.requires(["wiz", "hue", "lifx"]) == ["pywizlight", "aiohttp"]
    every = backends.requires(["all"])
    assert "btmesh[ble]" in every and len(every) == len(set(every))
    with pytest.raises(UserError):
        backends.requires(["bogus"])
