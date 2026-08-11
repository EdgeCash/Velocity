"""Social model cards — the shareable per-game breakdown, data side.

The public face of a slate run: one card per game carrying **only model
facts** — win probabilities, projected score, fair spread and total, the
simulated total-points distribution, and a "players to watch" strip. No odds,
no picks, no verdicts: every number is a checkable output of the same Monte
Carlo the slate prices from, so tomorrow's graded record can be laid directly
against today's card.

The one quietly sharp element: **players to watch are chosen where the model
most disagrees with the market's prop line** (when a board is available) — but
the card states only the model's probability at that line, never the price.
A reader who knows what a line is can do the rest; the card never does it for
them.

Pure and offline-testable; rendering lives in :mod:`social_png`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from velocity.models.game_nfl import GameProjection
from velocity.models.props_football import FootballPropSim

# Prop markets a watch entry may come from, with display units. Kept to the
# stats a casual reader recognizes on sight.
_WATCH_MARKETS = {
    "pass_yards": ("pass yards", "PASS YDS"),
    "rush_yards": ("rush yards", "RUSH YDS"),
    "receiving_yards": ("receiving yards", "REC YDS"),
    "receptions": ("receptions", "REC"),
}


@dataclass(frozen=True)
class WatchEntry:
    """One players-to-watch row: a named model fact at a specific line.

    ``from_board`` marks a line taken from the market's prop board (the
    disagreement-bearing case) versus the model's own median-anchored line
    (the no-board fallback, where distance from 50% is self-referential).
    """

    player: str
    market: str
    line: float
    p_over: float
    mean: float
    from_board: bool = False

    def fact(self) -> str:
        """"61% to clear 249.5 PASS YDS (model avg 262)" — a statement, not a pick."""
        _, unit = _WATCH_MARKETS.get(self.market, (self.market, self.market))
        avg = f"{self.mean:.1f}" if self.mean < 20 else f"{self.mean:.0f}"
        return f"{self.p_over:.0%} to clear {self.line:g} {unit} (model avg {avg})"


@dataclass(frozen=True)
class SocialCard:
    """Everything the renderer needs for one game's graphic."""

    game_id: str
    away_name: str
    home_name: str
    away_code: str
    home_code: str
    kickoff: pd.Timestamp | None
    p_home_win: float
    mu_away: float
    mu_home: float
    fair_spread: float  # fair home spread (negative = home favored)
    fair_total: float
    total_points_pmf: Mapping[int, float]  # simulated full-game total points
    n_sims: int = 0  # simulations behind every number — stated on the card
    watch: Sequence[WatchEntry] = field(default_factory=tuple)
    # The running graded record ("SEASON 41-38 · +6.2U"), carried on every card
    # so each graphic doubles as the receipt. None until a record exists.
    record_line: str | None = None
    # The market strip ("BUF +125 · KC -145 · KC -2.5 · O/U 47.5") — consensus
    # board numbers stated as fact, broadcast-style; never a recommendation.
    market: str | None = None

    def spread_label(self) -> str:
        """The fair line as a team-anchored string ("KC -2.7", or "PK")."""
        if abs(self.fair_spread) < 0.05:
            return "PK"
        if self.fair_spread < 0:  # home favored
            return f"{self.home_code} {self.fair_spread:+.1f}"
        return f"{self.away_code} {-self.fair_spread:+.1f}"


def _pmf(samples: np.ndarray) -> dict[int, float]:
    """Empirical pmf of an integer sample array (full support, exact).

    Display-side tail trimming happens at render time (`social_png._trim_pmf`)
    — the data layer never folds, so the persisted card facts stay exact.
    """
    ints = np.maximum(samples.astype(int), 0)
    values, counts = np.unique(ints, return_counts=True)
    n = int(ints.shape[0])
    return {int(v): float(c) / n for v, c in zip(values, counts, strict=True)}


def _prop_lines_index(prop_lines: pd.DataFrame | None) -> dict[tuple[str, str, str], float]:
    """``(game_id, market, normalized player) → line point`` from a prop board.

    Multiple books/points collapse to the modal point — the market's consensus
    number, which is the honest line to state a probability at.
    """
    if prop_lines is None or prop_lines.empty:
        return {}
    from velocity.wagering.props_slate import _normalize  # same name normalization

    index: dict[tuple[str, str, str], float] = {}
    grouped = prop_lines.groupby(
        [prop_lines["game_id"].astype(str), "market", prop_lines["player"].map(_normalize)]
    )["point"]
    for (gid, market, player), points in grouped:
        index[(str(gid), str(market), str(player))] = float(points.mode().iloc[0])
    return index


def _watch_candidates(
    game_id: str,
    props: FootballPropSim,
    key_to_name: Mapping[str, str],
    line_index: Mapping[tuple[str, str, str], float],
) -> list[WatchEntry]:
    """Every nameable (player, market) the sim produced, at its display line.

    The line is the market's consensus point when the board carries one, else
    the model's own median-anchored half-point line — stated on the card either
    way, so the fact is checkable against the box score.
    """
    from velocity.wagering.props_slate import _normalize

    entries: list[WatchEntry] = []
    for (key, market), samples in props.samples.items():
        if market not in _WATCH_MARKETS:
            continue
        name = key_to_name.get(str(key))
        if name is None:
            continue
        board_line = line_index.get((game_id, market, _normalize(name)))
        line = (
            board_line
            if board_line is not None
            else float(np.floor(np.median(samples)) + 0.5)
        )
        if line < 0.5:
            continue  # a degenerate line states nothing
        entries.append(
            WatchEntry(
                player=name,
                market=market,
                line=float(line),
                p_over=float(np.mean(samples > line)),
                mean=float(np.mean(samples)),
                from_board=board_line is not None,
            )
        )
    return entries


def _select_watch(entries: list[WatchEntry], max_watch: int) -> tuple[WatchEntry, ...]:
    """The sharpest facts, one per player.

    Board-lined entries lead, ranked by distance from 50% at the market's own
    number — prop lines sit near the median, so that distance *is* the model's
    disagreement with the market, stated without ever touching a price. With no
    board, distance from 50% at the model's own line is self-referential, so
    fallback entries rank by substance instead: passing volume first, then the
    biggest yardage projections.
    """

    def _key(entry: WatchEntry) -> tuple[float, ...]:
        if entry.from_board:
            return (0.0, -abs(entry.p_over - 0.5), -entry.mean)
        market_rank = 0.0 if entry.market == "pass_yards" else 1.0
        return (1.0, market_rank, -entry.mean)

    seen: set[str] = set()
    picked: list[WatchEntry] = []
    for entry in sorted(entries, key=_key):
        if entry.player in seen:
            continue
        seen.add(entry.player)
        picked.append(entry)
        if len(picked) >= max_watch:
            break
    return tuple(picked)


def build_social_cards(
    projections: Mapping[str, GameProjection],
    events: pd.DataFrame,
    *,
    props_by_game: Mapping[str, FootballPropSim] | None = None,
    key_to_name: Mapping[str, str] | None = None,
    prop_lines: pd.DataFrame | None = None,
    aliases: Mapping[str, str] | None = None,
    max_watch: int = 3,
    record_line: str | None = None,
    lines: pd.DataFrame | None = None,
) -> list[SocialCard]:
    """One :class:`SocialCard` per projected event, in board order.

    ``props_by_game`` + ``key_to_name`` power the watch strip (both optional —
    cards render without them); ``lines`` is the canonical game board, from
    which each card's market strip is condensed.
    """
    from velocity.wagering.live import NFL_TEAM_ALIASES, resolve_team

    alias_map = dict(NFL_TEAM_ALIASES if aliases is None else aliases)
    codes = list(alias_map.values())
    names = dict(key_to_name or {})
    props_map = dict(props_by_game or {})
    line_index = _prop_lines_index(prop_lines)

    cards: list[SocialCard] = []
    for event in events.to_dict("records"):
        gid = str(event["game_id"])
        proj = projections.get(gid)
        if proj is None:
            continue
        away_name = str(event["away_team"])
        home_name = str(event["home_team"])
        kickoff = event.get("kickoff")
        total_points = proj.sim.total
        watch: tuple[WatchEntry, ...] = ()
        props = props_map.get(gid)
        if props is not None:
            watch = _select_watch(
                _watch_candidates(gid, props, names, line_index), max_watch
            )
        away_code = resolve_team(away_name, codes, alias_map) or away_name
        home_code = resolve_team(home_name, codes, alias_map) or home_name
        cards.append(
            SocialCard(
                game_id=gid,
                away_name=away_name,
                home_name=home_name,
                away_code=away_code,
                home_code=home_code,
                kickoff=None if pd.isna(kickoff) else pd.Timestamp(kickoff),
                p_home_win=float(proj.p_home_win()),
                mu_away=float(proj.mu_away),
                mu_home=float(proj.mu_home),
                fair_spread=float(proj.fair_spread()),
                fair_total=float(proj.fair_total()),
                total_points_pmf=_pmf(total_points),
                n_sims=int(proj.sim.home_score.shape[0]),
                watch=watch,
                record_line=record_line,
                market=market_strip(lines, gid, away_code, home_code),
            )
        )
    return cards


def market_strip(
    lines: pd.DataFrame | None, game_id: str, away_code: str, home_code: str
) -> str | None:
    """Condense a game's board to one broadcast-style line of consensus numbers.

    "BUF +125 · KC -145 · KC -2.5 · O/U 47.5" — the median moneyline per side,
    the modal home spread, and the modal total, stated as market fact (the same
    numbers any broadcast shows), never a recommendation. ``None`` when the
    board has none of them.
    """
    if lines is None or lines.empty:
        return None
    game = lines[lines["game_id"].astype(str) == str(game_id)]
    if game.empty:
        return None
    parts: list[str] = []
    ml = game[game["market"] == "moneyline"]
    for side, code in (("away", away_code), ("home", home_code)):
        prices = ml.loc[ml["side"] == side, "price"]
        if not prices.empty:
            parts.append(f"{code} {int(prices.median()):+d}")
    spreads = game.loc[
        (game["market"] == "spread") & (game["side"] == "home"), "point"
    ].dropna()
    if not spreads.empty:
        parts.append(f"{home_code} {float(spreads.mode().iloc[0]):+g}")
    totals = game.loc[game["market"] == "total", "point"].dropna()
    if not totals.empty:
        parts.append(f"O/U {float(totals.mode().iloc[0]):g}")
    return " · ".join(parts) if parts else None


def distributions_frame(projections: Mapping[str, GameProjection]) -> pd.DataFrame:
    """Tidy per-game pregame distributions: ``game_id, kind, value, prob``.

    ``kind`` is ``total`` (combined points) or ``margin`` (home − away).
    Persisted unfolded (full support, exact pmf) so the post-game Sim Check can
    place the actual result at its true percentile; display-side folding
    happens at render time. Every (game, kind) sums to 1.
    """
    rows: list[dict[str, object]] = []
    for gid, proj in projections.items():
        total = proj.sim.total.astype(int)
        margin = proj.sim.margin.astype(int)
        n = int(total.shape[0])
        for kind, samples in (("total", total), ("margin", margin)):
            values, counts = np.unique(samples, return_counts=True)
            rows.extend(
                {"game_id": str(gid), "kind": kind, "value": int(v), "prob": float(c) / n}
                for v, c in zip(values, counts, strict=True)
            )
    return pd.DataFrame(rows, columns=["game_id", "kind", "value", "prob"])


def caption(card: SocialCard) -> str:
    """Post copy for one card: plain model facts, no odds, no imperatives."""
    favorite = card.home_code if card.p_home_win >= 0.5 else card.away_code
    p_fav = max(card.p_home_win, 1.0 - card.p_home_win)
    lines = [
        f"{card.away_code} @ {card.home_code} — model: {favorite} {p_fav:.0%}, "
        f"projected {card.mu_away:.1f}-{card.mu_home:.1f} "
        f"(fair line {card.spread_label()}, fair total {card.fair_total:.1f}).",
    ]
    lines.extend(f"{entry.player}: {entry.fact()}." for entry in card.watch)
    return "\n".join(lines)
