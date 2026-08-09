"""Pure formatting for the plays app — no Streamlit, fully unit-testable.

Turns the runner's persisted parquets (game slate, props, parlays, games map,
projections) into the display shapes the app renders: a two-column
matchup/play table in the reference style ("Kansas City ML -145",
"Allen O249.5 PASS YDS +105") and per-game matchup card dicts.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path

import pandas as pd

# NFL city/region prefixes, longest-match-first, so "Kansas City Chiefs" →
# "Kansas City" and "Buffalo Bills" → "Buffalo" without guessing at nickname
# lengths. A name that matches nothing (already a code, or an NCAAF school,
# whose name IS the display name) displays unchanged.
_CITY_PREFIXES = sorted(
    [
        "Arizona", "Atlanta", "Baltimore", "Buffalo", "Carolina", "Chicago",
        "Cincinnati", "Cleveland", "Dallas", "Denver", "Detroit", "Green Bay",
        "Houston", "Indianapolis", "Jacksonville", "Kansas City", "Las Vegas",
        "Los Angeles", "Miami", "Minnesota", "New England", "New Orleans",
        "New York", "Philadelphia", "Pittsburgh", "San Francisco", "Seattle",
        "Tampa Bay", "Tennessee", "Washington",
    ],
    key=len,
    reverse=True,
)

_PROP_ABBREV = {
    "pass_yards": "PASS YDS",
    "pass_tds": "PASS TD",
    "rush_yards": "RUSH YDS",
    "receiving_yards": "REC YDS",
    "receptions": "REC",
    "anytime_td": "ATD",
}

_STAMP = r"\d{8}T\d{6}Z"


def city(team_name: str) -> str:
    """The team's city (display name), longest known prefix match."""
    name = str(team_name)
    for prefix in _CITY_PREFIXES:
        if name.startswith(prefix):
            return prefix
    return name


def _price(value: object) -> str:
    return f"{int(float(value)):+d}"  # type: ignore[arg-type]


def _point(value: object) -> str:
    return f"{float(value):g}"  # type: ignore[arg-type]


def matchup_names(games_map: pd.DataFrame | None) -> dict[str, tuple[str, str]]:
    """``game_id → (away_city, home_city)`` from the persisted games map."""
    if games_map is None or games_map.empty:
        return {}
    return {
        str(r["game_id"]): (city(str(r["away_team"])), city(str(r["home_team"])))
        for r in games_map.to_dict("records")
    }


def game_play_label(row: Mapping[str, object], names: Mapping[str, tuple[str, str]]) -> str:
    """A game-market slate row → its compact play string.

    "Kansas City ML -145", "Buffalo +2.5 -110", "O47.5 -105".
    """
    market = str(row["market"])
    side = str(row["side"])
    away, home = names.get(str(row["game_id"]), ("Away", "Home"))
    team = home if side == "home" else away
    price = _price(row["price"])

    if market == "moneyline":
        return f"{team} ML {price}"
    if market == "spread":
        return f"{team} {float(row['point']):+g} {price}"  # type: ignore[arg-type]
    if market == "total":
        return f"{'O' if side == 'over' else 'U'}{_point(row['point'])} {price}"
    return f"{market} {side} {price}"


_NAME_SUFFIXES = {"jr", "jr.", "sr", "sr.", "ii", "iii", "iv"}


def _surname(player: str) -> str:
    """The display surname: last token, keeping a generational suffix attached."""
    tokens = str(player).split()
    if not tokens:
        return str(player)
    if len(tokens) >= 2 and tokens[-1].lower() in _NAME_SUFFIXES:
        return " ".join(tokens[-2:])
    return tokens[-1]


def prop_play_label(row: Mapping[str, object]) -> str:
    """A prop slate row → "Allen O249.5 PASS YDS +105" ("Mesa Jr" keeps its suffix)."""
    abbrev = _PROP_ABBREV.get(str(row["market"]), str(row["market"]).upper())
    side = "O" if str(row["side"]) == "over" else "U"
    return (
        f"{_surname(str(row['player']))} {side}{_point(row['point'])} "
        f"{abbrev} {_price(row['price'])}"
    )


def plays_table(
    plays: pd.DataFrame | None,
    props: pd.DataFrame | None,
    parlays: pd.DataFrame | None,
    games_map: pd.DataFrame | None,
) -> pd.DataFrame:
    """The PLAYS view: one row per recommendation — matchup, play, stake.

    Game plays first (slate order), then props, then parlays (whose matchup
    cell names the legs' games).
    """
    names = matchup_names(games_map)

    def _matchup(game_id: object) -> str:
        away, home = names.get(str(game_id), (str(game_id), ""))
        return f"{away} @ {home}" if home else away

    rows: list[dict[str, object]] = []
    if plays is not None:
        for row in plays.to_dict("records"):
            rows.append({
                "matchup": _matchup(row["game_id"]),
                "play": game_play_label(row, names),
                "stake": row.get("stake"),
            })
    if props is not None:
        for row in props.to_dict("records"):
            rows.append({
                "matchup": _matchup(row["game_id"]),
                "play": prop_play_label(row),
                "stake": row.get("stake"),
            })
    if parlays is not None:
        for row in parlays.to_dict("records"):
            rows.append({
                "matchup": f"PARLAY ({row.get('n_legs', '?')} legs)",
                "play": f"{row.get('legs', '')} → {_price(row['price'])}",
                "stake": row.get("stake"),
            })
    return pd.DataFrame(rows, columns=["matchup", "play", "stake"])


def matchup_cards(
    projections: pd.DataFrame | None,
    games_map: pd.DataFrame | None,
    plays_view: pd.DataFrame,
) -> list[dict[str, object]]:
    """Per-game card dicts: teams, kickoff, model numbers, and that game's plays."""
    if projections is None or projections.empty:
        return []
    names = matchup_names(games_map)
    kickoffs: dict[str, object] = (
        {}
        if games_map is None or games_map.empty
        else {str(r["game_id"]): r.get("kickoff") for r in games_map.to_dict("records")}
    )
    cards: list[dict[str, object]] = []
    for row in projections.to_dict("records"):
        gid = str(row["game_id"])
        away, home = names.get(gid, (str(row.get("away", "")), str(row.get("home", ""))))
        matchup = f"{away} @ {home}"
        card: dict[str, object] = {
            "game_id": gid,
            "away": away,
            "home": home,
            "kickoff": kickoffs.get(gid),
            "p_home_win": row.get("p_home_win"),
            "mu_away": row.get("mu_away"),
            "mu_home": row.get("mu_home"),
            "fair_spread": row.get("fair_spread"),
            "fair_total": row.get("fair_total"),
            "plays": plays_view.loc[plays_view["matchup"] == matchup, "play"].tolist(),
        }
        cards.append(card)
    return cards


def newest(folder: Path, pattern: str) -> pd.DataFrame | None:
    """The newest parquet whose filename fully matches ``pattern`` (regex).

    Filenames end in a UTC ``%Y%m%dT%H%M%SZ`` stamp, so lexicographic order is
    chronological. Searches recursively (a downloaded artifact may nest files).
    """
    matches = sorted(
        (p for p in folder.rglob("*.parquet") if re.fullmatch(pattern, p.name)),
        key=lambda p: p.name,
    )
    return pd.read_parquet(matches[-1]) if matches else None


def _newest_stamp(folder: Path, prefix: str, league: str) -> str | None:
    """The newest run stamp among ``<prefix>_<league>_<stamp>*`` files."""
    pattern = re.compile(rf"{re.escape(prefix)}_{re.escape(league)}_({_STAMP})")
    stamps = [
        m.group(1)
        for p in folder.rglob("*")
        if (m := pattern.match(p.name)) is not None
    ]
    return max(stamps) if stamps else None


def card_images(folder: Path, league: str = "nfl") -> dict[str, object]:
    """The newest run's graphics: model cards, sim checks, record card, captions.

    Returns ``{"model": [(label, path)], "simcheck": [...], "record": path|None,
    "model_captions": str|None, "simcheck_captions": str|None}`` — everything
    the Cards tab needs to display and hand off for posting.
    """

    def _pngs(prefix: str) -> list[tuple[str, Path]]:
        stamp = _newest_stamp(folder, prefix, league)
        if stamp is None:
            return []
        found = list(folder.rglob(f"{prefix}_{league}_{stamp}_*.png"))
        labelled = [
            (p.stem.split(f"{stamp}_", 1)[-1].replace("_at_", " @ "), p)
            for p in found
        ]
        return sorted(labelled)

    def _captions(prefix: str) -> str | None:
        stamp = _newest_stamp(folder, prefix, league)
        if stamp is None:
            return None
        matches = list(folder.rglob(f"{prefix}_{league}_{stamp}_captions.md"))
        return matches[0].read_text() if matches else None

    record_stamp = _newest_stamp(folder, "recordcard", league)
    record = None
    if record_stamp is not None:
        matches = list(folder.rglob(f"recordcard_{league}_{record_stamp}.png"))
        record = matches[0] if matches else None

    return {
        "model": _pngs("social"),
        "simcheck": _pngs("simcheck"),
        "record": record,
        "model_captions": _captions("social"),
        "simcheck_captions": _captions("simcheck"),
    }


def load_slate_frames(folder: Path, league: str = "nfl") -> dict[str, pd.DataFrame | None]:
    """All the app's frames from a slate folder (or downloaded artifact dir)."""
    lg = re.escape(league)
    return {
        "plays": newest(folder, rf"slate_{lg}_{_STAMP}\.parquet"),
        "props": newest(folder, rf"slate_{lg}_props_{_STAMP}\.parquet"),
        "parlays": newest(folder, rf"slate_{lg}_parlays_{_STAMP}\.parquet"),
        "games_map": newest(folder, rf"games_{lg}_{_STAMP}\.parquet"),
        "projections": newest(folder, rf"projections_{lg}_{_STAMP}\.parquet"),
        "record": newest(folder, rf"record_{lg}_{_STAMP}\.parquet"),
    }
