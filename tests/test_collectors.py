"""Collector resilience — the 5xx retry spends bounded calls, 4xx never retries."""

from __future__ import annotations

import importlib.util
import urllib.error
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "collect_bettingpros",
    Path(__file__).resolve().parents[1] / "scripts" / "collect_bettingpros.py",
)
bp = importlib.util.module_from_spec(_spec)
assert _spec and _spec.loader
_spec.loader.exec_module(bp)


def _http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("https://x", code, "err", None, None)  # type: ignore[arg-type]


def test_retry_5xx_recovers_from_a_gateway_blip(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bp.time, "sleep", lambda _s: None)
    calls = {"n": 0}

    def flaky() -> str:
        calls["n"] += 1
        if calls["n"] < 3:
            raise _http_error(504)
        return "ok"

    assert bp._retry_5xx(flaky, "test") == "ok"
    assert calls["n"] == 3  # two retries, then success — the budget ceiling


def test_retry_5xx_gives_up_after_three_attempts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bp.time, "sleep", lambda _s: None)
    calls = {"n": 0}

    def dead() -> str:
        calls["n"] += 1
        raise _http_error(502)

    with pytest.raises(urllib.error.HTTPError):
        bp._retry_5xx(dead, "test")
    assert calls["n"] == 3  # never more — the call budget stays bounded


def test_retry_5xx_never_retries_4xx(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bp.time, "sleep", lambda _s: None)
    calls = {"n": 0}

    def throttled() -> str:
        calls["n"] += 1
        raise _http_error(429)

    with pytest.raises(urllib.error.HTTPError):
        bp._retry_5xx(throttled, "test")
    assert calls["n"] == 1  # a quota/throttle error must not burn more calls


def test_collect_isolates_a_dead_sport(monkeypatch: pytest.MonkeyPatch) -> None:
    """One sport 504ing through all retries must not void the other's lines."""
    import pandas as pd

    monkeypatch.setattr(bp.time, "sleep", lambda _s: None)

    class FakeClient:
        def events(self, sport: str) -> list[dict]:
            if sport == "NCAAF":
                raise _http_error(504)
            return [{"id": 1, "home": "Kansas City Chiefs",
                     "visitor": "Buffalo Bills", "scheduled": "2026-09-10",
                     "participants": []}]

        def game_lines(self, sport: str, event_ids: list) -> pd.DataFrame:
            return pd.DataFrame([{"game_id": "1", "book": "dk",
                                  "market": "spread", "side": "home",
                                  "price": -110, "point": -2.5}])

    monkeypatch.setattr(
        bp.BettingProsClient, "from_env", staticmethod(lambda: FakeClient())
    )
    lines, events, failed = bp.collect(("NFL", "NCAAF"), pd.Timestamp("2026-09-10"))
    assert failed == ["NCAAF"]
    assert len(lines) == 1 and lines["league"].iloc[0] == "nfl"  # NFL banked
    assert len(events) == 1  # the dead sport contributed nothing, not garbage
