"""DFS pipeline + lineup card — slate picking, frame glue, and a render smoke."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
from velocity.dfs.pipeline import lineup_frame, main_slate_group, solve_slate

# A two-group board: group 9 is a single-game showdown, group 1 the main slate.
_POOL_ROWS = [
    # name, pos, team, salary, points (points only used to build the FP frame)
    ("QB A", "QB", "AAA", 8000, 24.0),
    ("QB B", "QB", "BBB", 6000, 19.0),
    ("RB A", "RB", "AAA", 9000, 22.0),
    ("RB B", "RB", "BBB", 7000, 17.0),
    ("RB C", "RB", "CCC", 5000, 13.0),
    ("RB D", "RB", "DDD", 4000, 9.0),
    ("WR A", "WR", "AAA", 9000, 21.0),
    ("WR B", "WR", "BBB", 7500, 17.5),
    ("WR C", "WR", "CCC", 6000, 14.0),
    ("WR D", "WR", "DDD", 4500, 10.0),
    ("WR E", "WR", "EEE", 3500, 7.0),
    ("TE A", "TE", "AAA", 6500, 14.5),
    ("TE B", "TE", "BBB", 3000, 6.5),
    ("DST A", "DST", "AAA", 3500, 9.0),
    ("DST B", "DST", "BBB", 2500, 6.0),
]


def _salaries() -> pd.DataFrame:
    games = {"AAA": "AAA @ BBB", "BBB": "AAA @ BBB", "CCC": "CCC @ DDD",
             "DDD": "CCC @ DDD", "EEE": "EEE @ FFF"}
    rows = [
        {"draft_group_id": "1", "player_id": f"p{i}", "player_name": name,
         "position": pos, "salary": salary, "team": team,
         "competition": games[team]}
        for i, (name, pos, team, salary, _) in enumerate(_POOL_ROWS)
    ]
    rows.append({  # a showdown group spanning one game — never the main slate
        "draft_group_id": "9", "player_id": "p0", "player_name": "QB A",
        "position": "QB", "salary": 11000, "team": "AAA",
        "competition": "AAA @ BBB",
    })
    return pd.DataFrame(rows)


def _fp() -> pd.DataFrame:
    """Every skill player's points expressed as a single rec_yds stat (0.1/yd)."""
    rows = [
        {"player_id": f"p{i}", "player_name": name, "team": team, "position": pos,
         "stat": "rec_yds", "value": points * 10.0}
        for i, (name, pos, team, salary, points) in enumerate(_POOL_ROWS)
        if pos != "DST"  # defenses ride the projectionless-punt path
    ]
    return pd.DataFrame(rows)


def test_main_slate_group_prefers_the_most_games() -> None:
    assert main_slate_group(_salaries()) == "1"
    assert main_slate_group(_salaries().iloc[0:0]) is None


def test_solve_slate_builds_a_legal_lineup_from_frames() -> None:
    run = solve_slate(_salaries(), _fp())
    assert run.draft_group_id == "1"
    assert run.n_games == 3
    assert run.lineup is not None
    assert run.lineup.total_salary <= 50_000
    assert len(run.lineup.slots) == 9
    # DSTs joined at 0.0 (the legal punt), skill players at their FP points.
    dst = next(s for s in run.lineup.slots if s.slot == "DST")
    assert dst.points == 0.0

    frame = lineup_frame(run)
    assert len(frame) == 9
    assert list(frame["slot"]) == ["QB", "RB", "RB", "WR", "WR", "WR", "TE",
                                   "FLEX", "DST"]
    assert (frame["draft_group_id"] == "1").all()


def test_solve_slate_handles_an_unsolvable_board() -> None:
    salaries = _salaries()
    run = solve_slate(salaries[salaries["position"] != "TE"], _fp())
    assert run.lineup is None
    assert lineup_frame(run).empty


def test_render_dfs_card_writes_a_png(tmp_path: Path) -> None:
    from velocity.report.dfs_png import dfs_caption, render_dfs_card

    run = solve_slate(_salaries(), _fp())
    assert run.lineup is not None
    dest = tmp_path / "dfs_nfl_test.png"
    render_dfs_card(run.lineup, dest, when="THURSDAY, SEP 10",
                    slate_label="DK CLASSIC · 3 GAMES")
    assert dest.stat().st_size > 20_000  # a real rendered frame, not a stub

    copy = dfs_caption(run.lineup)
    assert f"${run.lineup.total_salary:,}" in copy
    assert "QB:" in copy


def test_is_season_long_flags_week_zero_frames() -> None:
    from velocity.dfs.pipeline import is_season_long

    week0 = pd.DataFrame({"week": [0, 0], "value": [372.0, 250.0]})
    weekly = pd.DataFrame({"week": [4, 4], "value": [24.7, 18.0]})
    mixed = pd.DataFrame({"week": [0, 4]})
    assert is_season_long(week0)
    assert not is_season_long(weekly)
    assert not is_season_long(mixed)  # any weekly rows → usable
    assert not is_season_long(pd.DataFrame())  # empty is a different problem


# --- MLB classic ---------------------------------------------------------------

_MLB_BOARD = [
    # name, DK position (raw), team, salary, opponent-competition
    ("Ace One", "SP", "NYY", 10500),
    ("Ace Two", "SP", "BOS", 9000),
    ("Ace Three", "RP", "TB", 7000),
    ("Catcher A", "C", "NYY", 4500),
    ("Catcher B", "C", "BOS", 3000),
    ("First A", "1B", "NYY", 5500),
    ("First B", "1B", "TB", 3500),
    ("Second A", "2B", "BOS", 4800),
    ("Second B", "2B/SS", "TB", 3200),
    ("Third A", "3B", "NYY", 5200),
    ("Third B", "3B", "BAL", 2900),
    ("Short A", "SS", "BOS", 5000),
    ("Short B", "SS", "BAL", 2800),
    ("Out A", "OF", "NYY", 6200),
    ("Out B", "OF", "BOS", 5100),
    ("Out C", "OF", "TB", 4200),
    ("Out D", "OF", "BAL", 3300),
    ("Out E", "OF", "NYY", 2600),
]


def _mlb_salaries() -> pd.DataFrame:
    comp = {"NYY": "NYY @ BOS", "BOS": "NYY @ BOS", "TB": "TB @ BAL",
            "BAL": "TB @ BAL"}
    return pd.DataFrame([
        {"draft_group_id": "7", "player_id": f"m{i}", "player_name": name,
         "position": pos, "salary": salary, "team": team,
         "competition": comp[team]}
        for i, (name, pos, team, salary) in enumerate(_MLB_BOARD)
    ])


def _mlb_fp() -> pd.DataFrame:
    """Season-total projections (week 0), the real FP MLB shape."""
    rows = []

    def add(name: str, pos: str, stats: dict) -> None:
        rows.extend(
            {"player_id": name, "player_name": name, "team": "X",
             "position": pos, "stat": stat, "value": value, "week": 0}
            for stat, value in stats.items()
        )

    # Pitchers: per-start value = (ip*2.25 + k*2 + w*4 - er*2 - h*.6 - bb*.6)/gs
    add("Ace One", "SP", {"gs": 30, "g": 30, "ip": 180, "k": 210, "w": 14,
                          "er": 60, "h": 150, "bb": 45})
    add("Ace Two", "SP", {"gs": 28, "g": 28, "ip": 160, "k": 150, "w": 10,
                          "er": 70, "h": 155, "bb": 50})
    add("Ace Three", "RP", {"gs": 20, "g": 20, "ip": 100, "k": 90, "w": 6,
                            "er": 45, "h": 100, "bb": 40})
    # Hitters: per-game value from season counting stats over g games.
    hitters = [("Catcher A", "C", 140), ("Catcher B", "C", 120),
               ("First A", "1B", 150), ("First B", "1B", 130),
               ("Second A", "2B", 145), ("Second B", "2B/SS", 125),
               ("Third A", "3B", 150), ("Third B", "3B", 110),
               ("Short A", "SS", 148), ("Short B", "SS", 100),
               ("Out A", "OF", 152), ("Out B", "OF", 140),
               ("Out C", "OF", 128), ("Out D", "OF", 115)]
    for name, pos, g in hitters:
        add(name, pos, {"g": g, "h": g * 1.0, "2b": g * 0.2, "hr": g * 0.15,
                        "rbi": g * 0.5, "r": g * 0.5, "bb": g * 0.35,
                        "sb": g * 0.05})
    # Out E has no games projected → dropped from the pool entirely.
    add("Out E", "OF", {"h": 50.0})
    return pd.DataFrame(rows)


def test_mlb_scorer_normalizes_season_totals_per_game() -> None:
    from velocity.dfs.scoring import dk_expected_points_mlb

    # One player per class: the league prior equals the player's own rate,
    # so shrinkage is the identity and the raw per-game math is checkable.
    rows = []
    for stat, value in {"gs": 30, "g": 30, "ip": 180, "k": 210, "w": 14,
                        "er": 60, "h": 150, "bb": 45}.items():
        rows.append({"player_id": "p", "player_name": "Ace One", "team": "X",
                     "position": "SP", "stat": stat, "value": value, "week": 0})
    for stat, value in {"g": 100, "h": 100.0, "2b": 20.0, "hr": 15.0,
                        "rbi": 50.0, "r": 50.0, "bb": 35.0, "sb": 5.0}.items():
        rows.append({"player_id": "o", "player_name": "Out A", "team": "X",
                     "position": "OF", "stat": stat, "value": value, "week": 0})
    rows.append({"player_id": "x", "player_name": "Out E", "team": "X",
                 "position": "OF", "stat": "h", "value": 50.0, "week": 0})
    points = dk_expected_points_mlb(pd.DataFrame(rows))
    ace = points[points["player_name"] == "Ace One"].iloc[0]
    expected = (180 * 2.25 + 210 * 2 + 14 * 4 - 60 * 2 - 150 * 0.6
                - 45 * 0.6) / 30
    assert abs(ace["points"] - round(expected, 2)) < 0.01
    hitter = points[points["player_name"] == "Out A"].iloc[0]
    expected_h = (100 * 3 + 20 * 2 + 15 * 7 + 50 * 2 + 50 * 2 + 35 * 2
                  + 5 * 5) / 100
    assert abs(hitter["points"] - round(expected_h, 2)) < 0.01
    # No games denominator -> no projection (Out E has stats but no g).
    assert "Out E" not in set(points["player_name"])


def test_mlb_scorer_shrinks_thin_samples_and_relief_starts() -> None:
    from velocity.dfs.scoring import dk_expected_points_mlb

    rows = []

    def add(name: str, pos: str, stats: dict) -> None:
        rows.extend({"player_id": name, "player_name": name, "team": "X",
                     "position": pos, "stat": k, "value": v, "week": 0}
                    for k, v in stats.items())

    # An everyday hitter and a one-game call-up with the SAME raw per-game
    # production: shrinkage must price the call-up well below the regular
    # (the weak bench bat drags the league prior below their shared rate,
    # and only the one-game sample follows it down).
    add("Regular", "OF", {"g": 130, "h": 130.0, "hr": 130 * 0.2, "r": 65.0})
    add("Call Up", "OF", {"g": 1, "h": 1.0, "hr": 0.2, "r": 0.5})
    add("Bench Bat", "OF", {"g": 120, "h": 60.0, "hr": 3.0, "r": 20.0})
    # A true starter and a reliever with one spot start: the reliever's whole
    # season must NOT be divided by his single start (the live bug).
    add("Starter", "SP", {"g": 28, "gs": 28, "ip": 170, "k": 180, "w": 12,
                          "er": 60, "h": 150, "bb": 45})
    add("Reliever", "RP", {"g": 47, "gs": 1, "ip": 60, "k": 70, "w": 4,
                           "er": 25, "h": 50, "bb": 20})
    points = dk_expected_points_mlb(pd.DataFrame(rows)).set_index("player_name")
    assert points.loc["Call Up", "points"] < points.loc["Regular", "points"] * 0.8
    # Per-appearance: the reliever prices at his per-outing value (~6), far
    # below the starter's per-start (~19) — not at 60 IP / 1 start.
    assert points.loc["Reliever", "points"] < 12
    assert points.loc["Starter", "points"] > points.loc["Reliever", "points"]


def test_solve_slate_builds_a_legal_mlb_lineup() -> None:
    from velocity.dfs.pipeline import LEAGUE_SPECS

    spec, scorer = LEAGUE_SPECS["mlb"]
    run = solve_slate(_mlb_salaries(), _mlb_fp(), spec=spec, scorer=scorer)
    assert run.lineup is not None
    slots = [s.slot for s in run.lineup.slots]
    assert slots == ["P", "P", "C", "1B", "2B", "3B", "SS", "OF", "OF", "OF"]
    assert run.lineup.total_salary <= 50_000
    # SP/RP both normalized into the P pool; the multi-position fielder
    # ("2B/SS") is priced at the primary slot.
    positions = {s.player_name: s.position for s in run.lineup.slots}
    assert all(positions[n] == "P" for n in positions
               if n.startswith("Ace") and n in positions)


def test_normalize_positions_maps_dk_mlb_strings() -> None:
    from velocity.dfs.optimizer import MLB_CLASSIC, NFL_CLASSIC
    from velocity.dfs.pipeline import normalize_positions

    board = pd.DataFrame({"position": ["SP", "RP", "2B/SS", "OF", "c "]})
    out = normalize_positions(board, MLB_CLASSIC)
    assert list(out["position"]) == ["P", "P", "2B", "OF", "C"]
    # Football boards pass through untouched.
    nfl = pd.DataFrame({"position": ["QB", "RB"]})
    assert normalize_positions(nfl, NFL_CLASSIC) is nfl


def test_mlb_solver_enforces_the_five_hitter_team_cap() -> None:
    from velocity.dfs.pipeline import LEAGUE_SPECS

    spec, scorer = LEAGUE_SPECS["mlb"]
    # A board where one team (NYY) fields seven elite-value cheap hitters
    # spanning every slot: the unconstrained optimum stacks 6+ of them, so
    # the DK cap has to bite.
    board_rows = [
        ("Ace One", "SP", "BOS", 9000), ("Ace Two", "SP", "TB", 8500),
        ("NYY C", "C", "NYY", 3000), ("NYY 1B", "1B", "NYY", 3000),
        ("NYY 2B", "2B", "NYY", 3000), ("NYY 3B", "3B", "NYY", 3000),
        ("NYY SS", "SS", "NYY", 3000), ("NYY OF1", "OF", "NYY", 3000),
        ("NYY OF2", "OF", "NYY", 3000),
        ("BOS C", "C", "BOS", 3200), ("BOS 1B", "1B", "BOS", 3200),
        ("BOS 2B", "2B", "BOS", 3200), ("BOS 3B", "3B", "BOS", 3200),
        ("BOS SS", "SS", "BOS", 3200), ("BOS OF", "OF", "BOS", 3200),
        ("TB OF", "OF", "TB", 3200), ("BAL OF", "OF", "BAL", 3200),
    ]
    comp = {"NYY": "NYY @ BOS", "BOS": "NYY @ BOS", "TB": "TB @ BAL",
            "BAL": "TB @ BAL"}
    salaries = pd.DataFrame([
        {"draft_group_id": "7", "player_id": f"c{i}", "player_name": name,
         "position": pos, "salary": salary, "team": team,
         "competition": comp[team]}
        for i, (name, pos, team, salary) in enumerate(board_rows)
    ])
    rows = []
    for name, pos, team, _ in board_rows:
        if pos == "SP":
            stats = {"gs": 30, "g": 30, "ip": 180, "k": 200, "w": 12,
                     "er": 60, "h": 150, "bb": 45}
        else:
            hr = 0.6 if team == "NYY" else 0.1  # NYY bats are outliers
            stats = {"g": 140, "h": 140.0, "hr": 140 * hr, "rbi": 70.0,
                     "r": 70.0, "bb": 45.0}
        rows.extend({"player_id": name, "player_name": name, "team": team,
                     "position": pos, "stat": k, "value": v, "week": 0}
                    for k, v in stats.items())
    fp = pd.DataFrame(rows)
    run = solve_slate(salaries, fp, spec=spec, scorer=scorer)
    assert run.lineup is not None
    per_team: dict[str, int] = {}
    for s in run.lineup.slots:
        if s.position != "P" and s.team:
            per_team[s.team] = per_team.get(s.team, 0) + 1
    assert per_team["NYY"] == 5  # capped, not the unconstrained 6-7
    assert max(per_team.values()) <= 5


def test_classic_slates_selects_same_type_multi_game_groups() -> None:
    from velocity.dfs.pipeline import classic_slates, slate_label_ct

    rows = []

    def board(gid, n_games, ctype, start, suffix, players=12):
        for i in range(players):
            rows.append({
                "draft_group_id": gid, "player_id": f"{gid}-{i}",
                "player_name": f"P {gid} {i}", "position": "OF",
                "salary": 3000, "team": f"T{i % (2 * n_games)}",
                "competition": f"game {i % n_games}",
                "contest_type_id": ctype,
                "slate_start": pd.Timestamp(start), "suffix": suffix,
            })

    board("100", 7, 28, "2026-08-24 23:40", "")          # main classic
    board("101", 3, 28, "2026-08-24 22:40", "Turbo")     # early turbo classic
    board("102", 6, 28, "2026-08-25 00:38", "Night")     # night classic
    board("103", 7, 45, "2026-08-24 23:40", "MLB Tiers")  # other game style
    board("104", 1, 114, "2026-08-24 23:10", "ATL @ MIL")  # showdown
    slates = classic_slates(pd.DataFrame(rows))
    assert [s.draft_group_id for s in slates] == ["101", "100", "102"]  # lock order
    assert [s.suffix for s in slates] == ["Turbo", "", "Night"]
    label = slate_label_ct(slates[0])
    # 22:40 UTC in August is 5:40 PM CDT.
    assert label == "Mon 5:40 PM CT (Turbo) · 3 games"


def test_classic_slates_falls_back_without_type_columns() -> None:
    from velocity.dfs.pipeline import classic_slates

    rows = []
    for gid, n_games in (("1", 5), ("9", 1)):
        for i in range(8):
            rows.append({
                "draft_group_id": gid, "player_id": f"{gid}-{i}",
                "player_name": f"P{i}", "position": "OF", "salary": 3000,
                "team": f"T{i}", "competition": f"g{i % n_games}",
                "kickoff": pd.Timestamp("2026-08-24 23:40"),
            })
    slates = classic_slates(pd.DataFrame(rows))
    # Old snapshots: multi-game filter alone — the showdown group drops.
    assert [s.draft_group_id for s in slates] == ["1"]


def test_lineup_frame_carries_kickoffs() -> None:
    salaries = _salaries().assign(kickoff=pd.Timestamp("2026-09-13 17:00"))
    run = solve_slate(salaries, _fp())
    assert run.lineup is not None
    assert all(s.kickoff == pd.Timestamp("2026-09-13 17:00")
               for s in run.lineup.slots)
    frame = lineup_frame(run)
    assert (frame["kickoff"] == pd.Timestamp("2026-09-13 17:00")).all()


def test_game_time_ct_formats_and_handles_missing() -> None:
    from velocity.dfs.pipeline import game_time_ct

    assert game_time_ct(pd.Timestamp("2026-08-24 23:40")) == "6:40P CT"
    assert game_time_ct(None) == "—"
    # January (CST, UTC-6): the tz database keeps the hour honest.
    assert game_time_ct(pd.Timestamp("2027-01-10 23:40")) == "5:40P CT"


def test_eligible_board_drops_out_players_and_non_probable_pitchers() -> None:
    from velocity.dfs.optimizer import MLB_CLASSIC, NFL_CLASSIC
    from velocity.dfs.pipeline import eligible_board

    board = pd.DataFrame([
        {"player_name": "Real Probable", "position": "P", "probable": True,
         "status": "None"},
        {"player_name": "Bench Arm", "position": "P", "probable": False,
         "status": "None"},
        {"player_name": "IL Arm", "position": "P", "probable": True,
         "status": "IL"},
        {"player_name": "Everyday Hitter", "position": "OF", "probable": False,
         "status": "None"},
        {"player_name": "DTD Hitter", "position": "OF", "probable": False,
         "status": "DTD"},
        {"player_name": "Out Hitter", "position": "OF", "probable": False,
         "status": "OUT"},
    ])
    kept = set(eligible_board(board, MLB_CLASSIC)["player_name"])
    # Non-probable and IL'd pitchers leave (the live Turbo-lineup bug);
    # hitters stay regardless of the flag; DTD plays, OUT doesn't.
    assert kept == {"Real Probable", "Everyday Hitter", "DTD Hitter"}
    # Football: the probable rule is MLB's; the status rule is universal.
    nfl = pd.DataFrame([
        {"player_name": "QB", "position": "QB", "status": "Q"},
        {"player_name": "IR RB", "position": "RB", "status": "IR"},
    ])
    assert set(eligible_board(nfl, NFL_CLASSIC)["player_name"]) == {"QB"}
    # Pre-flag snapshots (no status/probable columns) pass through untouched.
    old = pd.DataFrame([{"player_name": "P Old", "position": "P"}])
    assert len(eligible_board(old, MLB_CLASSIC)) == 1
