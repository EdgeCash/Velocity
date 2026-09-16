"""The Odds API credit ledger — per-call cost accounting, offline.

The client records one row per credit-spending call so a month of runs can
answer "are we on the right plan" with a number. These tests pin the three
things that make the ledger trustworthy: the league is read off the request
*path* (it is never in the query), the key never reaches a row, and accounting
failure never propagates into a fetch.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest
from velocity.ingest.theoddsapi import (
    USAGE_COLUMNS,
    TheOddsAPIClient,
    classify_endpoint,
    describe_usage,
    project_monthly,
    usage_frame,
    usage_summary,
    write_usage,
)


def _client() -> TheOddsAPIClient:
    return TheOddsAPIClient(api_key="super-secret-key")


def _record(client: TheOddsAPIClient, endpoint: str, **headers: str) -> None:
    client._record(endpoint, {"regions": "us", "markets": "h2h,spreads"}, headers)


@pytest.mark.parametrize(
    ("endpoint", "kind", "league"),
    [
        ("sports", "sports", ""),
        ("sports/baseball_mlb/odds", "odds", "mlb"),
        ("sports/americanfootball_nfl/events", "events", "nfl"),
        ("sports/americanfootball_nfl/events/evt-1/odds", "event_odds", "nfl"),
        ("historical/sports/icehockey_nhl/odds", "historical_odds", "nhl"),
        ("historical/sports/icehockey_nhl/events", "historical_events", "nhl"),
        (
            "historical/sports/basketball_wnba/events/evt-2/odds",
            "historical_event_odds",
            "wnba",
        ),
        ("/sports/americanfootball_ncaaf/odds", "odds", "ncaaf"),
    ],
)
def test_classify_endpoint_reads_kind_and_league(endpoint, kind, league):
    assert classify_endpoint(endpoint) == (kind, league)


def test_classify_endpoint_degrades_on_an_unknown_shape():
    """A new endpoint masks to its shape rather than raising — a ledger never breaks a fetch."""
    kind, league = classify_endpoint("sports/baseball_mlb/scores")
    assert kind == "sports/*/*"
    assert league == "mlb"


def test_classify_endpoint_keeps_an_unmapped_sport_key_verbatim():
    """A league we don't map yet still groups — under its own API key, not a blank."""
    assert classify_endpoint("sports/soccer_epl/odds") == ("odds", "soccer_epl")


def test_record_captures_the_per_call_cost():
    client = _client()
    _record(client, "sports/baseball_mlb/odds", last="3", used="3", remaining="99997")
    (row,) = client.usage
    assert row["kind"] == "odds"
    assert row["league"] == "mlb"
    assert row["cost"] == 3
    assert row["used"] == 3
    assert row["remaining"] == 99997
    assert row["markets"] == "h2h,spreads"


def test_record_never_banks_the_api_key():
    """The ledger is banked to an Actions artifact, which is not a private place."""
    client = _client()
    _record(client, "sports/baseball_mlb/odds", last="3", used="3", remaining="99997")
    assert "super-secret-key" not in client.usage_ledger().to_csv()
    assert not any("super-secret-key" in str(v) for row in client.usage for v in row.values())


def test_record_survives_a_response_without_the_headers():
    """A missing header records a missing cost, not a zero — and never raises."""
    client = _client()
    _record(client, "sports/baseball_mlb/odds")
    frame = client.usage_ledger()
    assert pd.isna(frame.loc[0, "cost"])
    assert frame.loc[0, "kind"] == "odds"


def test_record_swallows_its_own_failure():
    """Accounting is never worth a failed run: a hostile params mapping is absorbed."""

    class Exploding(dict):
        def get(self, *args, **kwargs):
            raise RuntimeError("boom")

    client = _client()
    client._record("sports/baseball_mlb/odds", Exploding(), {"last": "1"})
    assert client.usage == []


def test_usage_frame_is_typed_and_ordered():
    frame = usage_frame([{"at": pd.Timestamp("2026-09-01"), "kind": "odds", "cost": "4"}])
    assert list(frame.columns) == USAGE_COLUMNS
    assert frame["cost"].dtype == "Int64"
    assert frame["at"].dtype == "datetime64[ns]"
    assert frame.loc[0, "cost"] == 4


def test_usage_frame_of_nothing_is_the_empty_ledger():
    frame = usage_frame([])
    assert frame.empty
    assert list(frame.columns) == USAGE_COLUMNS


def test_usage_summary_ranks_the_dearest_calls_first():
    client = _client()
    _record(client, "sports/baseball_mlb/odds", last="3")
    for i in range(2):
        _record(client, f"sports/americanfootball_nfl/events/e{i}/odds", last="12")
    summary = usage_summary(client.usage_ledger())
    assert list(summary["kind"]) == ["event_odds", "odds"]
    assert list(summary["credits"]) == [24, 3]
    assert list(summary["calls"]) == [2, 1]
    assert summary.loc[0, "per_call"] == 12.0


def test_project_monthly_withholds_a_number_from_too_short_a_window():
    """One afternoon says more about the sample than about the plan."""
    rows = [
        {"at": pd.Timestamp("2026-09-01 18:00"), "kind": "odds", "cost": 3},
        {"at": pd.Timestamp("2026-09-01 18:10"), "kind": "odds", "cost": 3},
    ]
    projection = project_monthly(usage_frame(rows))
    assert projection["credits"] == 6.0
    assert pd.isna(projection["monthly"])
    assert pd.isna(projection["plan_use_pct"])


def test_project_monthly_extrapolates_a_long_enough_window():
    rows = [
        {"at": pd.Timestamp("2026-09-01"), "kind": "odds", "cost": 100},
        {"at": pd.Timestamp("2026-09-11"), "kind": "odds", "cost": 900},
    ]
    projection = project_monthly(usage_frame(rows), plan=100_000)
    assert projection["days"] == pytest.approx(10.0)
    assert projection["per_day"] == pytest.approx(100.0)
    assert projection["monthly"] == pytest.approx(3000.0)
    assert projection["plan_use_pct"] == pytest.approx(3.0)


def test_write_usage_banks_a_parquet_that_round_trips(tmp_path):
    client = _client()
    _record(client, "sports/baseball_mlb/odds", last="3", used="3", remaining="99997")
    dest = write_usage(client.usage, tmp_path / "odds", "20260901T180000Z")
    assert dest is not None and dest.exists()
    restored = pd.read_parquet(dest)
    assert list(restored.columns) == USAGE_COLUMNS
    assert restored.loc[0, "cost"] == 3


def test_write_usage_banks_nothing_for_a_run_that_made_no_calls(tmp_path):
    assert write_usage([], tmp_path / "odds", "20260901T180000Z") is None
    assert not (tmp_path / "odds").exists()


def test_describe_usage_reports_spend_and_what_is_left():
    client = _client()
    _record(client, "sports/baseball_mlb/odds", last="3", used="3", remaining="99997")
    _record(client, "sports/americanfootball_nfl/events/e0/odds", last="12", remaining="99985")
    text = describe_usage(client.usage)
    assert "15 spent over 2 calls" in text
    assert "99985 left this month" in text
    assert "event_odds [nfl]: 12 credits over 1 call" in text


def test_describe_usage_says_so_when_nothing_was_spent():
    assert describe_usage([]) == "credits: no API calls this run"


# --- the report CLI: banked ledgers → a plan-sizing verdict ------------------

REPO = Path(__file__).parent.parent
REPORT = REPO / "scripts" / "report_odds_credits.py"


def _bank(folder: Path, day: int, rows: list[tuple[str, int]]) -> None:
    """Bank one run's ledger under ``folder`` at a fixed date, so spans are exact."""
    client = _client()
    for endpoint, cost in rows:
        _record(client, endpoint, last=str(cost), remaining="99000")
    for row in client.usage:
        row["at"] = pd.Timestamp(f"2026-09-{day:02d} 18:00:00")
    assert write_usage(client.usage, folder / f"run{day}", f"2026090{day}") is not None


def _report(folder: Path, *args: str) -> str:
    result = subprocess.run(
        [sys.executable, str(REPORT), "--ledgers", str(folder), *args],
        capture_output=True, text=True, check=True, cwd=REPO,
    )
    return result.stdout


def test_report_finds_ledgers_in_nested_run_folders(tmp_path):
    """Each run banks into its own artifact folder, so the search is recursive."""
    _bank(tmp_path, 1, [("sports/baseball_mlb/odds", 3)])
    _bank(tmp_path, 11, [("sports/americanfootball_nfl/events/e0/odds", 12)])
    out = _report(tmp_path)
    assert "2 calls" in out
    assert "event_odds" in out and "odds" in out


def test_report_projects_a_month_and_verdicts_the_plan(tmp_path):
    _bank(tmp_path, 1, [("sports/baseball_mlb/odds", 100)])
    _bank(tmp_path, 11, [("sports/baseball_mlb/odds", 900)])
    out = _report(tmp_path, "--plan", "100000")
    assert "100 credits/day" in out
    assert "3000/month" in out
    assert "over-provisioned" in out


def test_report_verdicts_a_tight_plan_as_tight(tmp_path):
    _bank(tmp_path, 1, [("sports/americanfootball_nfl/events/e0/odds", 1000)])
    _bank(tmp_path, 11, [("sports/americanfootball_nfl/events/e1/odds", 32_000)])
    out = _report(tmp_path, "--plan", "100000")
    assert "verdict: tight" in out


def test_report_withholds_a_projection_from_one_afternoon(tmp_path):
    _bank(tmp_path, 1, [("sports/baseball_mlb/odds", 3)])
    out = _report(tmp_path)
    assert "projection: withheld" in out
    assert "credits/day" not in out


def test_report_says_so_when_there_is_nothing_banked(tmp_path):
    out = _report(tmp_path / "empty")
    assert "no credit ledgers" in out


def test_report_prices_the_league_axis(tmp_path):
    """A plan cut is made by league, so the report has to price that axis too."""
    _bank(tmp_path, 1, [("sports/americanfootball_nfl/events/e0/odds", 90),
                        ("sports/baseball_mlb/odds", 10)])
    out = _report(tmp_path)
    by_league = out.split("by league:")[1]
    assert "90.0" in by_league and "10.0" in by_league


# --------------------------------------------------------------------------
# What one market costs — the axis a board change actually uses (2026-09-16)
# --------------------------------------------------------------------------


def _market_ledger() -> pd.DataFrame:
    """Calls whose cost equals their market count — the real billing shape."""
    from velocity.ingest.theoddsapi import usage_frame

    return usage_frame([
        {"at": pd.Timestamp("2026-09-16T12:00"), "kind": "*/sports/*/odds",
         "endpoint": "e", "league": "nfl", "markets": "h2h,spreads,totals",
         "regions": "us", "cost": 3, "used": 3, "remaining": 100},
        {"at": pd.Timestamp("2026-09-16T12:01"), "kind": "*/sports/*/events/*/odds",
         "endpoint": "e", "league": "nfl", "markets": "player_pass_yds,player_rush_yds",
         "regions": "us", "cost": 2, "used": 5, "remaining": 98},
        {"at": pd.Timestamp("2026-09-16T12:02"), "kind": "*/sports/*/events/*/odds",
         "endpoint": "e", "league": "nfl", "markets": "player_pass_yds,player_rush_yds",
         "regions": "us", "cost": 2, "used": 7, "remaining": 96},
    ])


def test_one_market_costs_one_credit() -> None:
    """The billing rule, read off the ledger rather than off the docs.

    This is what makes a board decision answerable without waiting a week: the
    per-market rate falls out of the billing shape, not out of the traffic.
    """
    from scripts.report_odds_credits import cost_per_market

    table = cost_per_market(_market_ledger()).set_index("kind")
    assert table.loc["*/sports/*/events/*/odds", "credits_per_market"] == pytest.approx(1.0)
    assert table.loc["*/sports/*/odds", "credits_per_market"] == pytest.approx(1.0)
    assert table.loc["*/sports/*/events/*/odds", "markets_per_call"] == pytest.approx(2.0)


def test_credit_free_calls_do_not_drag_the_rate_down() -> None:
    """A /sports call costs nothing and lists no markets.

    Counting it would report a per-market rate below the real one and make
    every board change look cheaper than it is.
    """
    from scripts.report_odds_credits import cost_per_market
    from velocity.ingest.theoddsapi import usage_frame

    rows = _market_ledger().to_dict("records") + [
        {"at": pd.Timestamp("2026-09-16T12:03"), "kind": "*/sports", "endpoint": "s",
         "league": "", "markets": "", "regions": "", "cost": 0,
         "used": 7, "remaining": 96},
    ]
    table = cost_per_market(usage_frame(rows))
    assert "*/sports" not in set(table["kind"])
    assert table["credits_per_market"].eq(1.0).all()


def test_cost_per_market_of_nothing_is_empty_not_a_crash() -> None:
    from scripts.report_odds_credits import cost_per_market
    from velocity.ingest.theoddsapi import usage_frame

    assert cost_per_market(usage_frame([])).empty
    assert cost_per_market(pd.DataFrame()).empty


# --------------------------------------------------------------------------
# The live slate must bank what it spends (2026-09-16)
# --------------------------------------------------------------------------


def _live_slate_module():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "run_live_slate_ledger",
        Path(__file__).resolve().parents[1] / "scripts" / "run_live_slate.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_live_slate_banks_every_call_site(monkeypatch, tmp_path) -> None:
    """It spent credits from four call sites and banked none of them.

    Each site built its own client and dropped that client's ``usage`` when it
    went out of scope, so the two collectors banked ledgers and the live slate
    — which runs far more often than either — banked nothing. Any projection
    off those ledgers understated the real spend, which is worse than no
    accounting because it reads like a number.
    """
    monkeypatch.setenv("THE_ODDS_API", "test-key")
    mod = _live_slate_module()
    headers = {"last": "3", "used": "30", "remaining": "900"}

    first, second = mod._odds_client(), mod._odds_client()
    first._record("/v4/sports/americanfootball_nfl/odds",
                  {"markets": "h2h,spreads,totals", "regions": "us"}, headers)
    second._record("/v4/sports/americanfootball_nfl/events/x/odds",
                   {"markets": "player_pass_yds,player_rush_yds", "regions": "us"},
                   {"last": "2", "used": "32", "remaining": "898"})

    ledger = mod.bank_odds_usage(tmp_path, "nfl_test")
    assert ledger is not None
    banked = pd.read_parquet(ledger)
    assert len(banked) == 2, "a client's calls went missing between sites"
    assert set(banked["kind"]) == {"*/sports/*/odds", "*/sports/*/events/*/odds"}
    assert int(banked["cost"].sum()) == 5


def test_a_slate_that_spent_nothing_banks_nothing(monkeypatch, tmp_path) -> None:
    """An offline slate (``--snapshot-file``) makes no calls and writes no file."""
    monkeypatch.setenv("THE_ODDS_API", "test-key")
    mod = _live_slate_module()
    assert mod.bank_odds_usage(tmp_path, "nfl_test") is None
    assert not list(tmp_path.glob("*.parquet"))


def test_no_call_site_bypasses_the_accumulator() -> None:
    """A new ``from_env()`` call site would silently stop being accounted for.

    The helper exists precisely because four of them drifted out of the ledger
    once already, so the property worth pinning is that only the helper builds
    a client.
    """
    source = (Path(__file__).resolve().parents[1]
              / "scripts" / "run_live_slate.py").read_text()
    assert source.count("TheOddsAPIClient.from_env()") == 1, (
        "a call site is building its own client again — route it through "
        "_odds_client() or its spend will not reach the ledger"
    )
