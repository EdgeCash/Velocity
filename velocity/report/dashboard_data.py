"""Discover and shape persisted slate outputs for the dashboard — pure, offline.

The live runner (``scripts/run_live_slate.py``) writes, per run, into its ``--out``
folder:

* ``slate_{league}_{stamp}.parquet`` — the game-market plays,
* ``slate_{league}_props_{stamp}.parquet`` — the player-prop plays,
* ``games_{league}_{stamp}.parquet`` — game_id → teams + kickoff, and
* ``cards_{league}_{stamp}.html`` — the rendered matchup cards page.

The Streamlit dashboard (``dashboard/app.py``) is a *viewer* over that folder — no
network, no model, no credits. This module is the pure layer it sits on: find the
runs in a directory, group the four files by run, and shape the plays for display
(join human matchup labels, American-odds price, edge %, stake %). All of it is
plain pandas so it unit-tests without importing Streamlit.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pandas as pd

# stamp is the runner's UTC filename tag, e.g. 20260726T143000Z
_STAMP = r"(?P<stamp>\d{8}T\d{6}Z)"
_GAME_RE = re.compile(rf"^slate_(?P<league>[a-z]+)_{_STAMP}\.parquet$")
_PROPS_RE = re.compile(rf"^slate_(?P<league>[a-z]+)_props_{_STAMP}\.parquet$")
_GAMES_RE = re.compile(rf"^games_(?P<league>[a-z]+)_{_STAMP}\.parquet$")
_CARDS_RE = re.compile(rf"^cards_(?P<league>[a-z]+)_{_STAMP}\.html$")


@dataclass(frozen=True)
class SlateSet:
    """The files a single live-slate run produced, grouped. Any path may be ``None``."""

    league: str
    stamp: str
    generated_at: datetime
    game_path: Path | None = None
    props_path: Path | None = None
    games_path: Path | None = None
    cards_path: Path | None = None

    @property
    def label(self) -> str:
        """Human label for a run picker, e.g. '2026-07-26 14:30 UTC · mlb'."""
        return f"{self.generated_at:%Y-%m-%d %H:%M} UTC · {self.league}"


def _parse_stamp(stamp: str) -> datetime:
    return datetime.strptime(stamp, "%Y%m%dT%H%M%SZ")


def discover_slates(data_dir: Path) -> list[SlateSet]:
    """Scan ``data_dir`` for live-slate runs, newest first.

    Groups the game / props / games-map / cards files by ``(league, stamp)`` so each
    run is one :class:`SlateSet`. A run missing some files (e.g. no props, no cards)
    still appears — the viewer degrades per section. Recurses, so an extracted Actions
    artifact (each run in its own subfolder) is found wherever it lands.
    """
    if not data_dir.exists():
        return []
    runs: dict[tuple[str, str], dict[str, Path]] = {}
    for path in data_dir.rglob("*"):
        if not path.is_file():
            continue
        name = path.name
        for key, rx in (("props", _PROPS_RE), ("game", _GAME_RE),
                        ("games", _GAMES_RE), ("cards", _CARDS_RE)):
            m = rx.match(name)
            if m:
                runs.setdefault((m["league"], m["stamp"]), {})[key] = path
                break  # props_ must be tried before game_ (game_ regex would also match)

    sets = [
        SlateSet(
            league=league, stamp=stamp, generated_at=_parse_stamp(stamp),
            game_path=paths.get("game"), props_path=paths.get("props"),
            games_path=paths.get("games"), cards_path=paths.get("cards"),
        )
        for (league, stamp), paths in runs.items()
    ]
    return sorted(sets, key=lambda s: s.stamp, reverse=True)


def american(price: float | None) -> str:
    """Render a decimal-or-american price as a signed American string ('+120', '-110')."""
    if price is None or pd.isna(price):
        return "—"
    p = float(price)
    return f"{int(p):+d}" if p != 0 else "0"


def _matchup_labels(games_map: pd.DataFrame | None) -> dict[str, str]:
    if games_map is None or games_map.empty:
        return {}
    return {
        str(r["game_id"]): f"{r['away_team']} @ {r['home_team']}"
        for r in games_map.to_dict("records")
    }


def shape_plays(
    frame: pd.DataFrame | None,
    games_map: pd.DataFrame | None,
    *,
    bankroll: float,
    player: bool = False,
) -> pd.DataFrame:
    """Shape a persisted plays frame for display: matchup label, %s, stake %, sorted.

    Works for both the game slate and the prop slate (``player=True`` keeps the
    player column). Edge/model/fair become percentages, price becomes American, stake
    a share of ``bankroll``. Highest-edge plays first. An empty/absent frame yields an
    empty display frame (no error), so the viewer just shows "no plays".
    """
    cols = ["matchup", "market", "side", "point", "book", "price",
            "model_%", "fair_%", "edge_%", "stake", "stake_%"]
    if player:
        cols.insert(1, "player")
    if frame is None or frame.empty:
        return pd.DataFrame(columns=cols)

    labels = _matchup_labels(games_map)
    df = frame.copy()
    out = pd.DataFrame()
    out["matchup"] = df["game_id"].astype(str).map(lambda g: labels.get(g, g))
    if player:
        out["player"] = df.get("player")
    out["market"] = df["market"]
    out["side"] = df["side"]
    out["point"] = df["point"]
    out["book"] = df["book"]
    out["price"] = df["price"].map(american)
    out["model_%"] = (df["p_model"] * 100).round(1)
    out["fair_%"] = (df["p_fair"] * 100).round(1) if "p_fair" in df else pd.NA
    out["edge_%"] = (df["edge"] * 100).round(2) if "edge" in df else pd.NA
    out["stake"] = df["stake"].round(2)
    out["stake_%"] = (df["stake"] / bankroll * 100).round(2) if bankroll else pd.NA
    sort_key = "edge_%" if out["edge_%"].notna().any() else "stake"
    return out[cols].sort_values(sort_key, ascending=False, na_position="last").reset_index(
        drop=True
    )


def slate_summary(games: pd.DataFrame | None, props: pd.DataFrame | None) -> dict[str, float]:
    """Headline counts for a run: game plays, prop plays, and total staked."""
    g = 0 if games is None else len(games)
    p = 0 if props is None else len(props)
    staked = 0.0
    for f in (games, props):
        if f is not None and not f.empty and "stake" in f:
            staked += float(f["stake"].sum())
    return {"game_plays": g, "prop_plays": p, "total_staked": round(staked, 2)}
