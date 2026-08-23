"""Social model cards — the shareable per-game breakdown, data side.

The public face of a slate run: one **MARKET vs MODEL** card per game. The
market's consensus numbers sit on top as the benchmark; the model's numbers
sit directly under them in equal type; the delta between the two is the
content. A lean is stated only where the disagreement clears a fixed,
published threshold — everything else renders "no edge", because a board
where every cell has a play is a tout sheet, and the empty leans are what
make the highlighted ones credible.

The props strip repeats the same micro-grammar: **players to watch are chosen
where the model most disagrees with the market's prop line** (when a board is
available) — the market's line and the model's number, side by side, never a
price and never an imperative.

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
    "pitcher_strikeouts": ("pitcher Ks", "Ks"),
    "player_shots_on_goal": ("shots on goal", "SOG"),
    "player_rebounds": ("rebounds", "REB"),
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
    # Set when the slate actually staked this prop: the play detail the strip
    # states beside its PLAY pill ("OVER · -115 · 1.4u"). None = a watch fact.
    play: str | None = None

    def fact(self) -> str:
        """"61% to clear 249.5 PASS YDS (model avg 262)" — a statement, not a pick."""
        _, unit = _WATCH_MARKETS.get(self.market, (self.market, self.market))
        avg = f"{self.mean:.1f}" if self.mean < 20 else f"{self.mean:.0f}"
        return f"{self.p_over:.0%} to clear {self.line:g} {unit} (model avg {avg})"


@dataclass(frozen=True)
class PlayCall:
    """One suggested wager — the model's staked position on a game market.

    A staked slate bet reduced to what a reader acts on: the position, the
    number, the price/book it was shopped to, the stake, and (when the
    intelligence layer ran) its conviction tier. ``edge`` is the model-vs-
    devigged-market probability gap that qualified it. Lives here (not in
    :mod:`deepdive`) because the hero card wears PLAY badges for the same
    calls the deep dive's verdict band lists.
    """

    market: str
    side: str
    point: float | None
    price: float
    book: str
    stake: float
    edge: float | None = None
    tier: str | None = None

    def position(self, away_code: str, home_code: str) -> str:
        """Just the position ("DET -3.5" / "OVER 47.5") — the badge text."""
        if self.market == "moneyline":
            who = home_code if self.side == "home" else away_code
            return f"{who} ML"
        if self.market == "spread":
            who = home_code if self.side == "home" else away_code
            return f"{who} {self.point:+g}" if self.point is not None else f"{who} spread"
        if self.market == "total":
            return f"{self.side.upper()} {self.point:g}" if self.point is not None \
                else self.side.upper()
        if self.market in ("team_total_home", "team_total_away"):
            who = home_code if self.market.endswith("home") else away_code
            return f"{who} TT {self.side.upper()} {self.point:g}" \
                if self.point is not None else f"{who} team total {self.side}"
        return f"{self.market} {self.side}"

    def label(self, away_code: str, home_code: str) -> str:
        """"DET -3.5 · -110 (bookA) · 2.1u" — the position as a bettor writes it."""
        bits = [self.position(away_code, home_code),
                f"{self.price:+.0f} ({self.book})", f"{self.stake:.1f}u"]
        if self.tier:
            bits.append(f"tier {self.tier}")
        return " · ".join(bits)


@dataclass(frozen=True)
class MarketView:
    """One game's consensus market numbers, as numerics the matrix can lay out.

    ``spread_home`` is the modal home spread point (negative = home favored),
    moneylines are median American prices per side, ``total`` the modal O/U
    point. ``books`` and ``captured`` feed the trust footer — a market number
    without a source and a timestamp is just an assertion.
    """

    spread_home: float | None = None
    total: float | None = None
    ml_away: int | None = None
    ml_home: int | None = None
    books: int = 0
    captured: pd.Timestamp | None = None

    def implied_home_prob(self) -> float | None:
        """The market's de-vigged home win probability (two-way multiplicative)."""
        if self.ml_away is None or self.ml_home is None:
            return None
        from velocity.wagering.devig import devig

        return float(devig([self.ml_away, self.ml_home])[1])


# Published lean thresholds — a disagreement below these renders "no edge".
SPREAD_EDGE_PTS = 2.5
TOTAL_EDGE_PTS = 3.0
WIN_EDGE_PROB = 0.07


@dataclass(frozen=True)
class EdgeCall:
    """One market's verdict strip: a lean label when the threshold fires."""

    fired: bool
    label: str  # "DET +6.5" / "OVER 51.5" / "" — the lean at the market number
    detail: str  # "model diff 4.5 pts" / "no edge"


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
    # The same board as numerics — powers the MARKET vs MODEL matrix.
    market_view: MarketView | None = None
    # Explicit brand colors (hex) for the win split — set for leagues without
    # a fixed club table (NCAAF); None falls back to the NFL table / neutrals.
    away_color: str | None = None
    home_color: str | None = None
    # The slate's staked plays for this game — the matrix wears a PLAY badge
    # on each market carrying one. Empty when the slate didn't run (backtests,
    # bare rebuilds) or the model passed.
    plays: Sequence[PlayCall] = field(default_factory=tuple)

    def spread_label(self) -> str:
        """The fair line as a team-anchored string ("KC -2.7", or "PK")."""
        if abs(self.fair_spread) < 0.05:
            return "PK"
        if self.fair_spread < 0:  # home favored
            return f"{self.home_code} {self.fair_spread:+.1f}"
        return f"{self.away_code} {-self.fair_spread:+.1f}"

    def edges(self) -> dict[str, EdgeCall]:
        """The matrix's verdict row: one :class:`EdgeCall` per market.

        A lean is always stated **at the market's own number** (the bettable
        thing), and fires only past the published thresholds. Missing market
        numbers yield unfired calls with an em-dash detail — absence of a
        board is not an edge.
        """
        view = self.market_view or MarketView()
        none = EdgeCall(False, "", "—")
        out = {"spread": none, "total": none, "win": none}

        if view.spread_home is not None:
            diff = self.fair_spread - view.spread_home
            if diff >= SPREAD_EDGE_PTS:  # model likes home less than the market
                out["spread"] = EdgeCall(
                    True, f"{self.away_code} {-view.spread_home:+g}",
                    f"model diff {abs(diff):.1f} pts")
            elif diff <= -SPREAD_EDGE_PTS:
                out["spread"] = EdgeCall(
                    True, f"{self.home_code} {view.spread_home:+g}",
                    f"model diff {abs(diff):.1f} pts")
            else:
                out["spread"] = EdgeCall(False, "", "no edge")
        if view.total is not None:
            diff = self.fair_total - view.total
            if abs(diff) >= TOTAL_EDGE_PTS:
                side = "OVER" if diff > 0 else "UNDER"
                out["total"] = EdgeCall(
                    True, f"{side} {view.total:g}", f"model diff {abs(diff):.1f} pts")
            else:
                out["total"] = EdgeCall(False, "", "no edge")
        implied = view.implied_home_prob()
        if implied is not None:
            diff = self.p_home_win - implied
            if abs(diff) >= WIN_EDGE_PROB:
                code = self.home_code if diff > 0 else self.away_code
                out["win"] = EdgeCall(
                    True, f"{code} ML", f"model diff {abs(diff):.0%}")
            else:
                out["win"] = EdgeCall(False, "", "no edge")
        return out


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
    team_colors: Mapping[str, str] | None = None,
    plays_by_game: Mapping[str, Sequence[PlayCall]] | None = None,
    watch_by_game: Mapping[str, Sequence[WatchEntry]] | None = None,
) -> list[SocialCard]:
    """One :class:`SocialCard` per projected event, in board order.

    ``props_by_game`` + ``key_to_name`` power the watch strip (both optional —
    cards render without them); ``lines`` is the canonical game board, from
    which each card's market strip is condensed. ``team_colors`` (display code
    → hex) carries brand colors for leagues without a fixed club table.
    ``plays_by_game`` maps game_id → the slate's staked :class:`PlayCall`
    tuples, worn as PLAY badges on the matrix. ``watch_by_game`` supplies
    pre-built :class:`WatchEntry` tuples per game — the path for leagues
    whose prop model isn't the football sim (MLB pitcher Ks) — and takes
    precedence over the sim-built watch for those games.
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
        supplied = (watch_by_game or {}).get(gid)
        props = props_map.get(gid)
        if supplied is not None:
            watch = tuple(supplied)[:max_watch]
        elif props is not None:
            watch = _select_watch(
                _watch_candidates(gid, props, names, line_index), max_watch
            )
        away_code = resolve_team(away_name, codes, alias_map) or away_name
        home_code = resolve_team(home_name, codes, alias_map) or home_name
        colors = dict(team_colors or {})
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
                market_view=market_view(lines, gid),
                away_color=colors.get(away_code),
                home_color=colors.get(home_code),
                plays=tuple((plays_by_game or {}).get(gid, ())),
            )
        )
    return cards


def market_view(lines: pd.DataFrame | None, game_id: str) -> MarketView | None:
    """One game's board condensed to numerics: the matrix's MARKET row.

    Same consensus statistics as :func:`market_strip` (median moneylines,
    modal spread/total points), plus the book count and the newest quote
    timestamp for the trust footer. ``None`` when the board carries nothing
    for the game.
    """
    if lines is None or lines.empty:
        return None
    game = lines[lines["game_id"].astype(str) == str(game_id)]
    if game.empty:
        return None
    ml = game[game["market"] == "moneyline"]
    prices: dict[str, int | None] = {}
    for side in ("away", "home"):
        quoted = ml.loc[ml["side"] == side, "price"].dropna()
        prices[side] = None if quoted.empty else int(quoted.median())
    spreads = game.loc[
        (game["market"] == "spread") & (game["side"] == "home"), "point"
    ].dropna()
    totals = game.loc[game["market"] == "total", "point"].dropna()
    captured = None
    if "timestamp" in game.columns:
        stamps = pd.to_datetime(game["timestamp"], errors="coerce").dropna()
        captured = None if stamps.empty else pd.Timestamp(stamps.max())
    view = MarketView(
        spread_home=None if spreads.empty else float(spreads.mode().iloc[0]),
        total=None if totals.empty else float(totals.mode().iloc[0]),
        ml_away=prices["away"],
        ml_home=prices["home"],
        books=int(game["book"].nunique()) if "book" in game.columns else 0,
        captured=captured,
    )
    if (view.spread_home, view.total, view.ml_away, view.ml_home) == (None,) * 4:
        return None
    return view


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
    """Post copy for one card: market vs model stated as fact, no imperatives."""
    favorite = card.home_code if card.p_home_win >= 0.5 else card.away_code
    p_fav = max(card.p_home_win, 1.0 - card.p_home_win)
    lines = [
        f"{card.away_code} @ {card.home_code} — model: {favorite} {p_fav:.0%}, "
        f"projected {card.mu_away:.1f}-{card.mu_home:.1f} "
        f"(fair line {card.spread_label()}, fair total {card.fair_total:.1f}).",
    ]
    if card.market:
        lines.append(f"Market: {card.market}.")
    if card.plays:
        calls = "; ".join(p.label(card.away_code, card.home_code)
                          for p in card.plays)
        lines.append(f"The play: {calls}.")
    leans = [
        f"{call.label} ({call.detail})"
        for call in card.edges().values() if call.fired
    ]
    lines.append("Model lean: " + ("; ".join(leans) + "." if leans
                                   else "no edge on this board."))
    lines.extend(
        (f"PLAY — {entry.player} ({entry.play}): {entry.fact()}."
         if entry.play else f"{entry.player}: {entry.fact()}.")
        for entry in card.watch
    )
    return "\n".join(lines)
