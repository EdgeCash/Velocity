"""Baseball Savant (Statcast) ingest — batted-ball skill, the HR model's core.

Home-run *outcomes* are a rare binary event, so a batter's HR total is a
noisy read on his power. Statcast measures the input instead — how hard and
at what angle he hits the ball — and that stabilizes far sooner. Measured on
our own pull (docs/PROPS_HR.md): last season's barrel rate predicts this
season's HR/PA at r² ≈ 0.39, edging prior-season HR/PA itself (0.36). That
gap is the whole reason this model exists — the market anchors on the
counting stat, the batted-ball rate knows first.

MLB publishes Statcast leaderboards through baseballsavant.mlb.com with a
``csv=true`` switch — free, keyless, and keyed by the SAME MLBAM player id
statsapi uses, so it joins to the box-score banks with no name matching.

Two layers, kept strictly separate so the test gate stays offline (the
repo-wide ingest pattern):

* :func:`normalize_statcast` — **pure**: leaderboard CSV → tidy frame.
* :func:`fetch_statcast` — the network layer.
"""

from __future__ import annotations

import io
import urllib.request

import pandas as pd

_LEADERBOARD = (
    "https://baseballsavant.mlb.com/leaderboard/custom"
    "?year={year}&type={side}&min={min_bip}&selections={selections}&csv=true"
)
_FETCH_TIMEOUT = 90
_USER_AGENT = "velocity/1.0"

# The batted-ball skill columns the HR model reads. barrel_batted_rate is the
# headline (a "barrel" is Statcast's exit-velocity/launch-angle combination
# that historically produces extra-base contact); xiso is expected isolated
# power, the direct power estimate.
BATTER_SELECTIONS = (
    "pa,bip,barrel_batted_rate,launch_angle_avg,exit_velocity_avg,"
    "hard_hit_percent,xiso,xwoba,b_k_percent,b_bb_percent"
)
PITCHER_SELECTIONS = (
    "pa,bip,barrel_batted_rate,launch_angle_avg,exit_velocity_avg,"
    "hard_hit_percent,xiso,xwoba"
)

# Savant's CSV headers → our column names.
_RENAME = {
    "player_id": "player_id",
    "last_name, first_name": "player_name",
    "year": "season",
    "pa": "pa",
    "bip": "bip",
    "barrel_batted_rate": "barrel_rate",
    "launch_angle_avg": "launch_angle",
    "exit_velocity_avg": "exit_velocity",
    "hard_hit_percent": "hard_hit_rate",
    "xiso": "xiso",
    "xwoba": "xwoba",
    "b_k_percent": "k_rate",
    "b_bb_percent": "bb_rate",
}
_NUMERIC = ("pa", "bip", "barrel_rate", "launch_angle", "exit_velocity",
            "hard_hit_rate", "xiso", "xwoba", "k_rate", "bb_rate")


def _flip_name(name: object) -> str:
    """Savant ships "Judge, Aaron" — return "Aaron Judge"."""
    text = str(name).strip().strip('"')
    if "," in text:
        last, _, first = text.partition(",")
        return f"{first.strip()} {last.strip()}".strip()
    return text


def normalize_statcast(csv_text: str, side: str) -> pd.DataFrame:
    """A Savant leaderboard CSV → the tidy Statcast frame.

    Returns ``player_id`` (MLBAM, the statsapi id space), ``player_name``,
    ``season``, ``side``, and whichever measured columns the leaderboard
    carried. Columns Savant omits simply don't appear — the model treats a
    missing metric as "no Statcast read", never as a zero.
    """
    if not csv_text.strip():
        return pd.DataFrame(columns=["player_id", "player_name", "season", "side"])
    frame = pd.read_csv(io.StringIO(csv_text))
    frame.columns = [str(c).strip().strip('"') for c in frame.columns]
    frame = frame.rename(columns=_RENAME)
    if "player_name" in frame.columns:
        frame["player_name"] = frame["player_name"].map(_flip_name)
    if "player_id" in frame.columns:
        frame["player_id"] = frame["player_id"].astype(str)
    for column in _NUMERIC:
        if column in frame.columns:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame["side"] = side
    keep = ["player_id", "player_name", "season", "side",
            *(c for c in _NUMERIC if c in frame.columns)]
    return frame[[c for c in keep if c in frame.columns]].reset_index(drop=True)


def fetch_statcast(
    season: int, side: str = "batter", min_bip: int = 25
) -> pd.DataFrame:  # pragma: no cover - network
    """One season's Statcast leaderboard for ``batter`` or ``pitcher``."""
    selections = BATTER_SELECTIONS if side == "batter" else PITCHER_SELECTIONS
    url = _LEADERBOARD.format(year=season, side=side, min_bip=min_bip,
                              selections=selections)
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(request, timeout=_FETCH_TIMEOUT) as response:  # noqa: S310
        return normalize_statcast(response.read().decode("utf-8-sig"), side)
