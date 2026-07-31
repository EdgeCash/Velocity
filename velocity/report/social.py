"""Social model cards — the shareable per-game breakdown, data side.

The public face of a slate run: one card per game carrying **only model
facts** — win probabilities, projected score, fair total, the F5 and
first-inning shape, the simulated run distribution, and a "players to watch"
strip. No odds, no picks, no verdicts: every number is a checkable output of
the same 10,000-game Monte Carlo the slate prices from, so tomorrow's graded
record can be laid directly against today's card.

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

from velocity.models.game_mlb import MLBProjection
from velocity.models.props_mlb import BaseballProps

# Prop markets a watch entry may come from, with display units. Kept to the
# stats a casual reader recognizes on sight.
_WATCH_MARKETS = {
    "pitcher_strikeouts": ("strikeouts", "K"),
    "total_bases": ("total bases", "TB"),
    "hits": ("hits", "H"),
}
_MAX_HISTOGRAM_RUNS = 17  # right tail folds into a "17+" bucket


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
    pmf: Mapping[int, float]  # value → probability, for the mini distribution
    from_board: bool = False
    player_id: str | None = None  # StatsAPI id → the card's headshot

    def fact(self) -> str:
        """"61% to clear 6.5 K (model avg 6.8)" — a statement, not a pick."""
        _, unit = _WATCH_MARKETS.get(self.market, (self.market, self.market))
        return (
            f"{self.p_over:.0%} to clear {self.line:g} {unit} "
            f"(model avg {self.mean:.1f})"
        )


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
    fair_total: float
    f5_fair_total: float
    p_yrfi: float
    total_runs_pmf: Mapping[int, float]  # simulated full-game total runs
    n_sims: int = 0  # simulations behind every number — stated on the card
    watch: Sequence[WatchEntry] = field(default_factory=tuple)
    # The running graded record ("SEASON 41-38 · +6.2U"), carried on every card
    # so each graphic doubles as the receipt. None until a record exists.
    record_line: str | None = None
    # Team context (best-effort, live only): W-L records for the header blocks.
    away_record: str | None = None
    home_record: str | None = None
    # The market strip ("SF +142 · LAD -156 · O/U 8.5") — consensus board
    # numbers stated as fact, broadcast-style; never a recommendation.
    market: str | None = None


def _pmf(samples: np.ndarray, cap: int) -> dict[int, float]:
    """Empirical pmf of an integer sample array, right tail folded into ``cap``."""
    clipped = np.minimum(samples.astype(int), cap)
    counts = np.bincount(clipped, minlength=cap + 1)
    n = int(clipped.shape[0])
    return {value: float(c) / n for value, c in enumerate(counts)}


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
    props: BaseballProps,
    id_to_name: Mapping[str, str],
    line_index: Mapping[tuple[str, str, str], float],
) -> list[WatchEntry]:
    """Every nameable (player, market) the sim produced, at its display line.

    The line is the market's consensus point when the board carries one, else
    the model's own median-anchored half-run line — stated on the card either
    way, so the fact is checkable against the box score.
    """
    from velocity.wagering.props_slate import _normalize

    result = props.result
    entries: list[WatchEntry] = []
    tables = {
        "pitcher_strikeouts": result.pitcher_strikeouts,
        "total_bases": result.batter_total_bases,
        "hits": result.batter_hits,
    }
    for market, table in tables.items():
        for pid, samples in table.items():
            name = id_to_name.get(str(pid))
            if name is None:
                continue  # league-average stand-ins have no real name → skip
            board_line = line_index.get((game_id, market, _normalize(name)))
            if board_line is None and market == "hits":
                # A median-anchored hits line reads the same for every batter
                # ("~25% to clear 1.5 H") — noise, not insight. Board-only.
                continue
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
                    pmf=_pmf(samples, cap=int(max(line + 4, 8))),
                    from_board=board_line is not None,
                    player_id=str(pid),
                )
            )
    return entries


def _select_watch(entries: list[WatchEntry], max_watch: int) -> tuple[WatchEntry, ...]:
    """The sharpest facts, one per player.

    Board-lined entries lead, ranked by distance from 50% at the market's own
    number — prop lines sit near the median, so that distance *is* the model's
    disagreement with the market, stated without ever touching a price. With no
    board, distance from 50% at the model's own line is self-referential, so
    fallback entries rank by substance instead: starters' strikeout facts
    first, then the biggest total-bases threats.
    """

    def _key(entry: WatchEntry) -> tuple[float, ...]:
        if entry.from_board:
            return (0.0, -abs(entry.p_over - 0.5), -entry.mean)
        market_rank = 0.0 if entry.market == "pitcher_strikeouts" else 1.0
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
    projections: Mapping[str, MLBProjection],
    events: pd.DataFrame,
    *,
    id_to_name: Mapping[str, str] | None = None,
    prop_lines: pd.DataFrame | None = None,
    aliases: Mapping[str, str] | None = None,
    max_watch: int = 3,
    record_line: str | None = None,
    team_records: Mapping[str, str] | None = None,
    lines: pd.DataFrame | None = None,
) -> list[SocialCard]:
    """One :class:`SocialCard` per projected event, in board order.

    ``team_records`` maps a club code to its W-L string (best-effort context);
    ``lines`` is the canonical game board, from which each card's market strip
    is condensed.
    """
    from velocity.wagering.live import MLB_TEAM_ALIASES, resolve_team

    alias_map = dict(MLB_TEAM_ALIASES if aliases is None else aliases)
    codes = list(alias_map.values())
    names = dict(id_to_name or {})
    line_index = _prop_lines_index(prop_lines)
    records = dict(team_records or {})

    cards: list[SocialCard] = []
    for event in events.to_dict("records"):
        gid = str(event["game_id"])
        proj = projections.get(gid)
        if proj is None:
            continue
        away_name = str(event["away_team"])
        home_name = str(event["home_team"])
        kickoff = event.get("kickoff")
        result = proj.result
        total_runs = (result.full.home_score + result.full.away_score).astype(int)
        watch = _select_watch(
            _watch_candidates(gid, BaseballProps(result), names, line_index), max_watch
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
                fair_total=float(proj.fair_total()),
                f5_fair_total=float(proj.f5.fair_total()),
                p_yrfi=float(proj.prob_yrfi()),
                total_runs_pmf=_pmf(total_runs, cap=_MAX_HISTOGRAM_RUNS),
                n_sims=int(result.full.home_score.shape[0]),
                watch=watch,
                record_line=record_line,
                away_record=records.get(away_code),
                home_record=records.get(home_code),
                market=market_strip(lines, gid, away_code, home_code),
            )
        )
    return cards


def market_strip(
    lines: pd.DataFrame | None, game_id: str, away_code: str, home_code: str
) -> str | None:
    """Condense a game's board to one broadcast-style line of consensus numbers.

    "SF +142 · LAD -156 · O/U 8.5" — the median moneyline per side and the
    modal total, stated as market fact (the same numbers any broadcast shows),
    never a recommendation. ``None`` when the board has neither.
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
    totals = game.loc[game["market"] == "total", "point"].dropna()
    if not totals.empty:
        parts.append(f"O/U {float(totals.mode().iloc[0]):g}")
    return " · ".join(parts) if parts else None


def distributions_frame(projections: Mapping[str, MLBProjection]) -> pd.DataFrame:
    """Tidy per-game pregame distributions: ``game_id, kind, value, prob``.

    ``kind`` is ``total`` (combined runs) or ``margin`` (home − away). Persisted
    unfolded (full support, exact pmf) so the post-game Sim Check can place the
    actual result at its true percentile; display-side folding happens at
    render time. Every (game, kind) sums to 1.
    """
    rows: list[dict[str, object]] = []
    for gid, proj in projections.items():
        result = proj.result
        total = (result.full.home_score + result.full.away_score).astype(int)
        margin = (result.full.home_score - result.full.away_score).astype(int)
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
        f"projected {card.mu_away:.1f}-{card.mu_home:.1f} (fair total {card.fair_total:.1f}).",
        f"F5 total {card.f5_fair_total:.1f} · first-inning run {card.p_yrfi:.0%}.",
    ]
    lines.extend(f"{entry.player}: {entry.fact()}." for entry in card.watch)
    return "\n".join(lines)
