"""Football props collector — offline banking smoke.

The network fetch is credit-gated and lives behind the client's pragma; this
exercises the ``--from-file`` path: banked per-event payloads are re-processed
into the raw/normalized archive layout. The normalizers themselves are tested
in test_props_slate.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pandas as pd

REPO = Path(__file__).parent.parent
SCRIPT = REPO / "scripts" / "collect_football_props.py"

_UPDATE = "2026-09-10T12:00:00Z"


def _per_event_payloads() -> dict:
    """A banked ``{event_id: payload}`` map, as the per-event endpoint returns."""
    event = {
        "id": "evt-001",
        "sport_key": "americanfootball_nfl",
        "commence_time": "2026-09-10T00:20:00Z",
        "home_team": "Kansas City Chiefs",
        "away_team": "Buffalo Bills",
        "bookmakers": [
            {
                "key": "draftkings",
                "last_update": _UPDATE,
                "markets": [
                    {
                        "key": "player_pass_yds",
                        "last_update": _UPDATE,
                        "outcomes": [
                            {"name": "Over", "description": "Josh Allen",
                             "price": 100, "point": 249.5},
                            {"name": "Under", "description": "Josh Allen",
                             "price": -120, "point": 249.5},
                        ],
                    }
                ],
            }
        ],
    }
    return {"evt-001": event}


def test_from_file_banks_raw_and_normalized(tmp_path: Path) -> None:
    payloads = tmp_path / "payloads.json"
    payloads.write_text(json.dumps(_per_event_payloads()))
    out = tmp_path / "props"
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--from-file", str(payloads),
         "--league", "nfl", "--out", str(out)],
        capture_output=True, text=True, cwd=REPO,
    )
    assert result.returncode == 0, result.stderr
    assert "processed" in result.stdout
    # Raw JSON banked verbatim, plus the normalized PropLines parquet.
    assert list(out.glob("raw/nfl_event_*_evt-001.json"))
    props_files = list(out.glob("props_nfl_*.parquet"))
    assert props_files
    props = pd.read_parquet(props_files[0])
    assert len(props) == 2  # over + under
    assert (props["market"] == "pass_yards").all()
    assert (props["league"] == "nfl").all()
    assert not props["is_closing"].any()  # a live snapshot, not the close


def test_from_file_empty_board_succeeds(tmp_path: Path) -> None:
    payloads = tmp_path / "payloads.json"
    payloads.write_text(json.dumps({}))
    out = tmp_path / "props"
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--from-file", str(payloads),
         "--league", "ncaaf", "--out", str(out)],
        capture_output=True, text=True, cwd=REPO,
    )
    assert result.returncode == 0, result.stderr
    props_files = list(out.glob("props_ncaaf_*.parquet"))
    assert props_files
    assert pd.read_parquet(props_files[0]).empty


def _payloads_with_team_totals() -> dict:
    """A per-event payload carrying a prop AND the team-totals derivative."""
    base = _per_event_payloads()
    base["evt-001"]["bookmakers"][0]["markets"].append({
        "key": "team_totals",
        "last_update": _UPDATE,
        "outcomes": [
            {"name": "Over", "description": "Kansas City Chiefs",
             "price": -110, "point": 24.5},
            {"name": "Under", "description": "Kansas City Chiefs",
             "price": -110, "point": 24.5},
            {"name": "Over", "description": "Buffalo Bills",
             "price": -105, "point": 21.5},
            {"name": "Under", "description": "Buffalo Bills",
             "price": -115, "point": 21.5},
        ],
    })
    return base


def test_from_file_banks_team_totals_beside_the_props(tmp_path: Path) -> None:
    payloads = tmp_path / "payloads.json"
    payloads.write_text(json.dumps(_payloads_with_team_totals()))
    out = tmp_path / "props"
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--from-file", str(payloads),
         "--league", "nfl", "--out", str(out)],
        capture_output=True, text=True, cwd=REPO,
    )
    assert result.returncode == 0, result.stderr
    # The props parquet is untouched by the rider…
    props = pd.read_parquet(next(iter(out.glob("props_nfl_*.parquet"))))
    assert (props["market"] == "pass_yards").all()
    # …and the team-total lines land beside it, resolved to home/away markets.
    tt_files = list(out.glob("team_totals_nfl_*.parquet"))
    assert tt_files, result.stdout
    tt = pd.read_parquet(tt_files[0])
    assert set(tt["market"]) == {"team_total_home", "team_total_away"}
    assert len(tt) == 4
    home = tt[tt["market"] == "team_total_home"]
    assert (home["point"] == 24.5).all()


def test_pre_cutover_payloads_write_no_team_totals_file(tmp_path: Path) -> None:
    payloads = tmp_path / "payloads.json"
    payloads.write_text(json.dumps(_per_event_payloads()))  # props only
    out = tmp_path / "props"
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--from-file", str(payloads),
         "--league", "nfl", "--out", str(out)],
        capture_output=True, text=True, cwd=REPO,
    )
    assert result.returncode == 0, result.stderr
    assert not list(out.glob("team_totals_nfl_*.parquet"))


# --- what we buy, and the trap in cutting it ---------------------------------


def _collector():  # type: ignore[no-untyped-def]
    import importlib.util

    spec = importlib.util.spec_from_file_location("collect_football_props", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_a_league_is_bought_only_when_something_can_price_it() -> None:
    """Audit finding 6b: NHL and NBA prop lines were bought and never read.

    No slate produced ``player_shots_on_goal`` or ``player_rebounds`` and no
    bank existed to produce one from — ``datasets/nhl/starters.parquet`` is
    goalies and the NBA vertical is unbuilt — so every scheduled run paid
    per-event credits for lines nothing could price.
    """
    markets = _collector().LEAGUE_PROP_MARKETS
    assert set(markets) == {"nfl", "ncaaf", "mlb"}
    assert "nhl" not in markets and "nba" not in markets


def test_the_schedule_does_not_buy_what_the_config_dropped() -> None:
    """Both halves, or the cut is only half made.

    The market set says what a league costs; the workflow's --leagues says
    whether it is bought at all. Leaving the league in the schedule is what
    actually spends the credits.
    """
    workflow = (REPO / ".github" / "workflows"
                / "collect-football-props.yml").read_text()
    assert "nfl ncaaf mlb nhl" not in workflow
    assert workflow.count("nfl ncaaf mlb") == 2  # the input default and the run line


def test_an_unconfigured_league_buys_nothing_rather_than_the_football_board() -> None:
    """The trap the cut would otherwise have set.

    ``LEAGUE_PROP_MARKETS.get(league, DEFAULT_EVENT_MARKETS)`` meant a league
    dropped from the config but left in --leagues would pull SIX FOOTBALL
    MARKETS against its events: strictly more expensive than the one market
    being cut, and invisible in a log that just says the league was
    snapshotted.
    """
    source = SCRIPT.read_text()
    assert 'LEAGUE_PROP_MARKETS.get(league, DEFAULT_EVENT_MARKETS)' not in source
    assert 'LEAGUE_PROP_MARKETS.get(league, "")' in source
    assert "no prop markets configured" in source


def test_the_way_back_in_is_kept() -> None:
    """Cutting the purchase is not the same as deleting the capability.

    SOG is in every banked NHL boxscore (docs/BUILD_NHL.md), so the skater
    bank is a build away. The normalizer mapping and the display label cost
    nothing and are what a future vertical re-enters through.
    """
    from velocity.ingest.theoddsapi import PROP_MARKET_BY_KEY

    assert PROP_MARKET_BY_KEY["player_shots_on_goal"] == "shots_on_goal"
    assert PROP_MARKET_BY_KEY["player_rebounds"] == "rebounds"

