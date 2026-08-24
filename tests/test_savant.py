"""Baseball Savant Statcast ingest — the pure normalizer."""

from __future__ import annotations

from velocity.ingest.savant import normalize_statcast

CSV = (
    '"last_name, first_name","player_id","year","pa","bip",'
    '"barrel_batted_rate","launch_angle_avg","exit_velocity_avg",'
    '"hard_hit_percent","xiso","xwoba"\n'
    '"Judge, Aaron",592450,2026,261,150,21.7,"16.2","94.1",58.3,".330",".440"\n'
    '"Arraez, Luis",650333,2026,540,420,1.2,"6.1","86.0",22.1,".070",".300"\n'
)


def test_normalize_flips_names_and_types_columns() -> None:
    frame = normalize_statcast(CSV, "batter")
    assert list(frame["player_name"]) == ["Aaron Judge", "Luis Arraez"]
    assert list(frame["player_id"]) == ["592450", "650333"]  # str, statsapi id space
    assert (frame["side"] == "batter").all()
    judge = frame.iloc[0]
    assert judge["barrel_rate"] == 21.7
    assert judge["xiso"] == 0.330  # quoted ".330" coerces to a float
    assert judge["exit_velocity"] == 94.1
    assert frame["pa"].dtype.kind in "if"


def test_normalize_tolerates_missing_columns_and_empty_input() -> None:
    # A leaderboard without the optional metrics keeps what it has; a missing
    # metric never becomes a zero.
    lean = ('"last_name, first_name","player_id","year","pa"\n'
            '"Soto, Juan",665742,2026,600\n')
    frame = normalize_statcast(lean, "batter")
    assert "barrel_rate" not in frame.columns
    assert frame.iloc[0]["player_name"] == "Juan Soto"
    empty = normalize_statcast("", "batter")
    assert empty.empty and "player_id" in empty.columns


def test_normalize_handles_a_name_without_a_comma() -> None:
    csv = '"last_name, first_name","player_id","year"\n"Ichiro",111,2026\n'
    assert normalize_statcast(csv, "batter").iloc[0]["player_name"] == "Ichiro"
