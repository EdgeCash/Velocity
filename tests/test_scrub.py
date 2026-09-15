"""Credential scrubbing — provider-agnostic, because the hazard is.

BettingPros echoed its partner key in ``_pagination.self`` and the collector
banked it into 89 artifacts. The scrubber written afterwards lived beside the
BettingPros client and matched BettingPros' parameter names — so it silently
failed on The Odds API, whose key rides in the query string as ``apiKey``. A
scrubber that misses the other at-risk provider while reading as general
protection is worse than none, because the collector calling it looks safe.

These tests pin both provider shapes and the casing rules, so the next provider
added is a line in SECRET_QUERY_PARAMS plus a case here, not another incident.
"""

from __future__ import annotations

import pytest
from velocity.ingest.scrub import SECRET_QUERY_PARAMS, scrub_secrets

SENTINEL = "SUPERSECRETVALUE123"


@pytest.mark.parametrize(
    "url",
    [
        # BettingPros: the original incident's exact shape.
        f"https://api.bettingpros.com/v3/props?key={SENTINEL}&user=42&sport=NFL",
        # The Odds API: camelCase, the spelling the first scrubber missed.
        f"https://api.the-odds-api.com/v4/sports/x/odds?apiKey={SENTINEL}&regions=us",
        # Casing and separator variants a provider might pick.
        f"https://x.test/v1?APIKEY={SENTINEL}",
        f"https://x.test/v1?api-key={SENTINEL}",
        f"https://x.test/v1?api_key={SENTINEL}",
        f"https://x.test/v1?access_token={SENTINEL}",
        f"https://x.test/v1?a=1&token={SENTINEL}&b=2",
        f"https://x.test/v1?secret={SENTINEL}",
    ],
)
def test_every_credential_shape_is_redacted(url):
    assert SENTINEL not in scrub_secrets(url)
    assert "REDACTED" in scrub_secrets(url)


def test_the_odds_api_key_is_redacted_wherever_it_is_echoed():
    """Nested exactly as BettingPros echoed it — a link inside a wrapper."""
    payload = {
        "timestamp": "2026-09-15T00:00:00Z",
        "previous_timestamp": "2026-09-14T00:00:00Z",
        "_pagination": {
            "self": f"https://api.the-odds-api.com/v4/x?apiKey={SENTINEL}&regions=us",
            "next": None,
        },
        "data": [{"id": "evt", "bookmakers": [{"key": "draftkings"}]}],
    }
    scrubbed = scrub_secrets(payload)
    assert SENTINEL not in str(scrubbed)
    # Structure and every non-credential value survive, so normalizers are
    # unaffected — including a field literally named "key" that is not one.
    assert scrubbed["data"] == payload["data"]
    assert scrubbed["timestamp"] == payload["timestamp"]
    assert scrubbed["_pagination"]["next"] is None


def test_a_bare_field_named_key_is_not_a_credential():
    """``bookmakers[].key`` is "draftkings". Only URL query params are redacted."""
    payload = {"bookmakers": [{"key": "draftkings", "markets": [{"key": "h2h"}]}]}
    assert scrub_secrets(payload) == payload


def test_scrubbing_is_idempotent():
    once = scrub_secrets(f"https://x.test/v1?apiKey={SENTINEL}")
    assert scrub_secrets(once) == once


def test_non_string_leaves_are_untouched():
    payload = {"n": 3, "f": 1.5, "b": True, "z": None, "l": [1, 2]}
    assert scrub_secrets(payload) == payload


def test_secret_param_list_covers_both_casings_of_apikey():
    """The regex anchors on the whole param name: api_key does NOT cover apiKey."""
    assert "apikey" in SECRET_QUERY_PARAMS
    assert "api_key" in SECRET_QUERY_PARAMS


# --- the banking boundary: what the collectors actually write ---------------


def test_props_collector_scrubs_the_payload_it_banks(tmp_path):
    """The helper being correct is not the property that matters — this is.

    Exercises collect_football_props' offline ``--from-file`` path, which runs
    the same ``snapshot_league`` banking code the network path does.
    """
    import json
    import subprocess
    import sys
    from pathlib import Path

    repo = Path(__file__).parent.parent
    leaked = f"https://api.the-odds-api.com/v4/x?apiKey={SENTINEL}&regions=us"
    payloads = {
        "evt-001": {
            "id": "evt-001",
            "sport_key": "americanfootball_nfl",
            "commence_time": "2026-09-10T00:20:00Z",
            "home_team": "Kansas City Chiefs",
            "away_team": "Baltimore Ravens",
            "bookmakers": [],
            "_pagination": {"self": leaked},
        }
    }
    src = tmp_path / "payloads.json"
    src.write_text(json.dumps(payloads))
    out = tmp_path / "props"
    subprocess.run(
        [sys.executable, str(repo / "scripts" / "collect_football_props.py"),
         "--from-file", str(src), "--league", "nfl", "--out", str(out)],
        check=True, capture_output=True, text=True, cwd=repo, timeout=180,
    )
    banked = list((out / "raw").glob("*.json"))
    assert banked, "collector banked no raw payload"
    for path in banked:
        text = path.read_text()
        assert SENTINEL not in text, f"key survived into {path.name}"
        assert "REDACTED" in text
