"""The bankroll ledger — append-only state for a real, compounding bankroll.

Every stake the system has ever recommended was a fraction of a *fresh*
notional bankroll — ``--bankroll 100`` on every run — so nothing compounded,
no exposure carried from one slate to the next, and the drawdown kill-switch
in :mod:`velocity.wagering.portfolio` had no inputs (docs/WAGERING.md §1.4,
W1). The ledger is that missing state: one parquet, one bankroll, five record
types, never edited in place.

* ``seed`` — the opening bankroll. Written once, by the first run that finds
  the ledger empty (the runner's ``--bankroll`` becomes the seed, not a
  per-run constant).
* ``adjust`` — a deposit, a withdrawal, or a correction, as a signed amount.
  Corrections are new records, so every bankroll number stays reproducible
  from history.
* ``recommended`` — every row of a run's sized card, at the stake the
  portfolio rules recommended (paper rows ride along at zero, with their
  reason). What the model said to do.
* ``placed`` — a bet actually taken: the price, stake, book and number the
  operator got (``scripts/ledger.py place``), or — in the runner's ``auto``
  mode — the recommendation itself, booked at its own terms so the bankroll
  compounds off the card while nobody is placing by hand. A placed row at
  stake zero is a ``skip``: seen, declined, closed.
* ``settled`` — a placed bet graded: ``+stake·b`` on a win, ``−stake`` on a
  loss, nothing on a push, the exchange's 50c rule on a tie
  (:func:`velocity.wagering.bet_log.settle_profit`), summed over every
  placement of the bet. One settled row per bet; re-settling is a no-op.

Derived views are pure functions of the record: the bankroll curve (seed,
adjustments and settlements in the order they were recorded), the current
and peak bankroll, the drawdown between them, open exposure (placed money
with no settlement yet), and P&L by league or market.

A bet's identity across records is ``bet_id`` — league, game, market, side
and player — deliberately without the stamp, so a bet recommended on
Wednesday's card, placed Thursday and settled after Sunday's grading is one
bet. The ledger is private data (it carries paid prices): it lives in the
slate artifact and the site's R2 bucket, never in the repo.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from velocity.wagering.bet_log import Bet, settle_profit

SEED = "seed"
ADJUST = "adjust"
RECOMMENDED = "recommended"
PLACED = "placed"
SETTLED = "settled"
RECORD_TYPES = (SEED, ADJUST, RECOMMENDED, PLACED, SETTLED)
SETTLED_RESULTS = frozenset({"win", "loss", "push", "tie"})

# Game markets ``Bet.grade`` settles from a final score; props settle from
# box scores through the graded slate instead.
_GAME_MARKETS = frozenset({"moneyline", "spread", "total", "team_total_home", "team_total_away"})

LEDGER_COLUMNS = [
    "record_id", "record_type", "recorded_at", "league", "bet_id", "slate_stamp",
    "kind", "game_id", "market", "side", "player", "point", "book", "price",
    "stake", "p_model", "p_fair", "note", "result", "profit",
    "closing_price", "closing_point", "price_clv", "line_clv",
]
_NUMERIC = ("point", "price", "stake", "p_model", "p_fair", "profit",
            "closing_price", "closing_point", "price_clv", "line_clv")
_IDENTITY = ("record_type", "recorded_at", "league", "bet_id", "slate_stamp",
             "stake", "price", "point", "book", "result", "profit", "note")


# How close two prices have to be, as a fraction of the larger implied
# probability, before the same view at two numbers counts as one position.
#
# The discriminator is the *price's* implied probability rather than the
# number, because a book moving a total from 44.5 to 45.5 re-prices to hold
# roughly −110 — the number moves, the implied probability barely does, and it
# is plainly the same bet. A ladder rung deep in the tail sits at a completely
# different implied probability and is plainly not. Relative rather than
# absolute for the reason the E8b gate learned: a 6-point gap is nothing at
# even money and is the whole bet at a tenth.
SAME_POSITION_TOLERANCE = 0.20


def _point_key(point: object) -> str:
    """The number as an id segment — ``44.5``, not ``44.50``; ``""`` for none."""
    value = _clean(point)
    return "" if value is None else f"{float(value):g}"


def bet_id(league: str, game_id: object, market: object, side: object,
           player: object = None, point: object = None) -> str:
    """The bet's identity across records — no stamp, no book, no price.

    The **number** is part of it, because settlement is: a total landing on 30
    wins an under 44.5 and loses an under 25.5, so two rungs that can disagree
    must be able to settle apart. The **venue** is not, because it is not:
    the same number bought at two books is one outcome, and pooling them is
    what gives the settled row its stake-weighted price.

    What the number does *not* decide is whether a new rung gets placed at all.
    A line ticking 44.5 → 45.5 would be a new id here and must not become a
    second bet — that is :data:`SAME_POSITION_TOLERANCE`'s job, on implied
    probability, at the view level. Two jobs, two mechanisms, deliberately.
    """
    who = "" if player is None or (isinstance(player, float) and math.isnan(player)) else player
    return f"{league}|{game_id}|{market}|{side}|{who}|{_point_key(point)}"


def view_id(bet_id_: str) -> str:
    """The bet's *view* — game, market, side — without its number.

    Ids written before the number joined them are already views, so this is
    the key that matches a card's new-format row against a position opened
    under the old scheme.
    """
    parts = str(bet_id_).split("|")
    return "|".join(parts[:5])


def same_position(price_a: object, price_b: object,
                  tolerance: float = SAME_POSITION_TOLERANCE) -> bool:
    """Whether two prices on one view are close enough to be the same bet.

    A missing price on either side answers *yes*: with nothing to compare, the
    safe reading is that the view is already on the books, which is the
    behaviour that held before implied probability was consulted at all.
    """
    from velocity.wagering.odds import american_to_prob

    a, b = _clean(price_a), _clean(price_b)
    if a is None or b is None:
        return True
    try:
        pa, pb = american_to_prob(float(a)), american_to_prob(float(b))
    except ValueError:  # a price outside (−100, 100) is not a price
        return True
    if max(pa, pb) <= 0:
        return True
    return abs(pa - pb) / max(pa, pb) <= tolerance


def _clean(value: object) -> Any:
    """``None`` for missing/NaN, the value otherwise — a parquet null is not a number."""
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    return value


def _utc_naive(when: Any) -> pd.Timestamp:
    stamp = pd.Timestamp(when)
    if stamp.tzinfo is not None:
        stamp = stamp.tz_convert("UTC").tz_localize(None)
    return stamp


def _record_id(row: dict[str, Any]) -> str:
    parts = []
    for key in _IDENTITY:
        value = _clean(row.get(key))
        if isinstance(value, pd.Timestamp):
            value = value.isoformat()
        elif isinstance(value, float):
            value = f"{value:.6f}"
        parts.append("" if value is None else str(value))
    return hashlib.sha1("\x1f".join(parts).encode()).hexdigest()[:16]


def empty_ledger() -> pd.DataFrame:
    return conform(pd.DataFrame(columns=LEDGER_COLUMNS))


def conform(frame: pd.DataFrame) -> pd.DataFrame:
    """Every ledger column present and typed; rows in the order recorded."""
    out = frame.copy()
    for column in LEDGER_COLUMNS:
        if column not in out.columns:
            out[column] = None
    out = out[LEDGER_COLUMNS]
    for column in _NUMERIC:
        out[column] = pd.to_numeric(out[column], errors="coerce").astype("float64")
    out["recorded_at"] = pd.to_datetime(out["recorded_at"], errors="coerce")
    if getattr(out["recorded_at"].dt, "tz", None) is not None:
        out["recorded_at"] = out["recorded_at"].dt.tz_convert("UTC").dt.tz_localize(None)
    for column in ("record_id", "record_type", "league", "bet_id", "slate_stamp", "kind",
                   "game_id", "market", "side", "player", "book", "note", "result"):
        out[column] = out[column].astype(object).where(out[column].notna(), None)
    if len(out):
        out = out.sort_values("recorded_at", kind="stable").reset_index(drop=True)
    return out


def merge_ledgers(*frames: pd.DataFrame | None) -> pd.DataFrame:
    """The union of several copies of the ledger — append-only means a
    record is the same record wherever it was read from."""
    parts = [f for f in frames if f is not None and not f.empty]
    if not parts:
        return empty_ledger()
    merged = pd.concat(parts, ignore_index=True)
    merged = merged.drop_duplicates(subset="record_id", keep="first")
    return conform(merged)


@dataclass(frozen=True)
class LedgerState:
    """The bankroll as the ledger knows it, for the runner and the site."""

    seed: float
    current: float
    peak: float
    drawdown: float
    open_exposure: float
    n_open: int
    n_settled: int
    last_settled_at: pd.Timestamp | None

    def describe(self) -> str:
        last = "" if self.last_settled_at is None else (
            f", last settled {self.last_settled_at:%Y-%m-%d %H:%M}Z")
        return (f"bankroll {self.current:.2f} (seed {self.seed:.2f}, peak {self.peak:.2f}, "
                f"drawdown {self.drawdown:.1%}); open exposure {self.open_exposure:.2f} "
                f"across {self.n_open} bet(s); {self.n_settled} settled{last}")


class Ledger:
    """The append-only ledger and its derived views."""

    def __init__(self, frame: pd.DataFrame | None = None,
                 path: str | Path | None = None) -> None:
        self.frame = empty_ledger() if frame is None else conform(frame)
        self.path = None if path is None else Path(path)

    @classmethod
    def load(cls, path: str | Path) -> Ledger:
        path = Path(path)
        frame = pd.read_parquet(path) if path.exists() else None
        return cls(frame, path)

    def save(self, path: str | Path | None = None) -> Path:
        dest = Path(path) if path is not None else self.path
        if dest is None:
            raise ValueError("no path to save the ledger to")
        dest.parent.mkdir(parents=True, exist_ok=True)
        self.frame.to_parquet(dest, index=False)
        self.path = dest
        return dest

    def __len__(self) -> int:
        return len(self.frame)

    # ---- appends --------------------------------------------------------
    def _append(self, rows: list[dict[str, Any]]) -> pd.DataFrame:
        if not rows:
            return empty_ledger()
        for row in rows:
            row["recorded_at"] = _utc_naive(row["recorded_at"])
            row["record_id"] = _record_id(row)
        new = conform(pd.DataFrame(rows))
        self.frame = merge_ledgers(self.frame, new)
        return new

    @property
    def seeded(self) -> bool:
        return bool((self.frame["record_type"] == SEED).any())

    def seed(self, amount: float, at: object, note: str | None = None) -> bool:
        """Open the bankroll at ``amount``; a no-op once a seed exists."""
        if self.seeded:
            return False
        if not amount > 0:
            raise ValueError("the seed must be positive")
        self._append([{"record_type": SEED, "recorded_at": at, "profit": float(amount),
                       "note": note}])
        return True

    def adjust(self, amount: float, at: object, note: str | None = None) -> None:
        """A deposit (+), a withdrawal (−), or a correction, as a new record."""
        if not self.seeded:
            raise ValueError("seed the ledger before adjusting it")
        self._append([{"record_type": ADJUST, "recorded_at": at, "profit": float(amount),
                       "note": note}])

    def recommend(self, card: pd.DataFrame, *, league: str, stamp: str, at: object) -> int:
        """Append a run's sized card as ``recommended`` rows; returns the count."""
        if card is None or card.empty:
            return 0
        rows = []
        for r in card.to_dict("records"):
            kind = str(r.get("kind") or ("prop" if _clean(r.get("player")) else "game"))
            player = _clean(r.get("player"))
            rows.append({
                "record_type": RECOMMENDED, "recorded_at": at, "league": league,
                "bet_id": bet_id(league, r["game_id"], r["market"], r["side"], player,
                                 r.get("point")),
                "slate_stamp": stamp, "kind": kind,
                "game_id": str(r["game_id"]), "market": str(r["market"]),
                "side": str(r["side"]), "player": None if player is None else str(player),
                "point": _clean(r.get("point")), "book": _clean(r.get("book")),
                "price": _clean(r.get("price")), "stake": float(r.get("stake") or 0.0),
                "p_model": _clean(r.get("p_model")), "p_fair": _clean(r.get("p_fair")),
                "note": _clean(r.get("note")),
            })
        return len(self._append(rows))

    def _known(self, bet_id_: str) -> pd.DataFrame:
        """Every record that carries terms for this bet, oldest first."""
        return self.frame[self.frame["record_type"].isin([RECOMMENDED, PLACED])
                          & (self.frame["bet_id"] == bet_id_)]

    @staticmethod
    def _contract(row: Any) -> tuple[Any, Any]:
        """The contract a row names: its book and its number.

        A :func:`bet_id` is a *view* — this game, this market, this side — and
        deliberately so, or a total ticking from 44.5 to 45.5 would read as a
        new bet and get placed a second time. One view can be bought as many
        different contracts, which is what a ladder is, so terms have to be
        matched on the contract rather than assumed from the view.
        """
        point = _clean(row.get("point") if isinstance(row, dict) else row.point)
        book = _clean(row.get("book") if isinstance(row, dict) else row.book)
        return (None if book is None else str(book),
                None if point is None else round(float(point), 4))

    def _recommendation(self, bet_id_: str, *, book: str | None = None,
                        point: float | None = None) -> dict[str, Any] | None:
        """This bet's latest known terms, for the contract asked for.

        With ``book``/``point`` given, the newest record naming that contract;
        without, the newest record of any. The caller is responsible for not
        asking the bare question when the answer is ambiguous — see
        :meth:`place`.
        """
        known = self._known(bet_id_)
        if known.empty:
            return None
        if book is not None or point is not None:
            want = (None if book is None else str(book),
                    None if point is None else round(float(point), 4))
            rows = [r for r in known.to_dict("records")
                    if all(w is None or c == w
                           for c, w in zip(self._contract(r), want, strict=True))]
            if rows:
                return {str(k): v for k, v in rows[-1].items()}
            # Nothing recommended at those terms — the operator took a number
            # the card never listed. The newest record still supplies the
            # identity fields; :meth:`place` is what decides that a *price*
            # must not be inherited across a contract that does not match.
            return None if known.empty else dict(known.iloc[-1])
        return dict(known.iloc[-1])

    def place(  # noqa: PLR0913 - a ticket has this many terms
        self,
        bet_id_: str | None,
        stake: float,
        *,
        at: object,
        price: float | None = None,
        book: str | None = None,
        point: float | None = None,
        note: str | None = None,
        fields: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Record a bet taken at the operator's terms (stake 0 = a skip).

        Terms not given come from the latest recommendation of the same bet;
        a bet the ledger never recommended needs ``fields`` (league, game_id,
        market, side, optional player/kind) to be placed at all.

        A ``bet_id`` names a *view*, not a contract, so one bet can have been
        recommended as several — a sportsbook's 44.5 and an exchange rung at
        25.5 are the same view of the same game. When that has happened and no
        terms are supplied, this refuses rather than reaching for the newest:
        guessing there booked a −110 bet at 44.5 as a +900 longshot at 25.5,
        which is a wrong ledger, a wrong bankroll and a wrong CLV all at once.
        Pass ``book``/``point`` (or ``fields``) to say which one was taken.
        """
        if bet_id_ is None:
            if not fields:
                raise ValueError("a bet needs a bet_id or its fields")
            bet_id_ = bet_id(fields["league"], fields["game_id"], fields["market"],
                             fields["side"], fields.get("player"),
                             point if point is not None else fields.get("point"))
        # An id without a number still resolves: a position opened before the
        # number joined the id, or an operator typing the short form off an
        # older to-do. Only when the view names exactly one bet, so this can
        # never quietly pick between two rungs.
        asked = (book if book is not None else (fields or {}).get("book"),
                 point if point is not None else (fields or {}).get("point"))
        known = self._known(bet_id_)
        if known.empty:
            want_view = view_id(bet_id_)
            all_known = self.frame[self.frame["record_type"].isin([RECOMMENDED, PLACED])]
            # A boolean *list* selects columns on an empty frame, not rows.
            on_view = all_known[all_known["bet_id"].map(
                lambda b: view_id(b) == want_view).astype(bool)]
            same_view = set(on_view["bet_id"])
            named = {r["bet_id"] for r in on_view.to_dict("records")
                     if all(w is None or c == w for c, w in
                            zip(self._contract(r),
                                self._contract(dict(zip(("book", "point"), asked, strict=True))),
                                strict=True))} if asked != (None, None) else set()
            if len(same_view) == 1:
                bet_id_ = same_view.pop()
            elif len(named) == 1:
                # The short form plus the terms names exactly one contract.
                bet_id_ = named.pop()
            elif same_view:
                # Several contracts answer to this view and nothing picks one.
                # That is the ambiguity the message below exists for, not an
                # unknown bet.
                known = on_view
            if known.empty:
                known = self._known(bet_id_)
        offered = {self._contract(r) for r in known.to_dict("records")}
        if stake > 0 and len(offered) > 1:
            shown = ", ".join(
                f"{bk or '?'} @ {pt if pt is not None else '—'}"
                for bk, pt in sorted(offered, key=lambda c: (str(c[0]), str(c[1])))
            )
            if asked == (None, None):
                raise ValueError(
                    f"{bet_id_!r} has been recommended as more than one contract "
                    f"({shown}); pass book= and point= to say which was taken"
                )
            if price is None and self._contract(dict(zip(("book", "point"), asked,
                                                         strict=True))) not in offered:
                raise ValueError(
                    f"{bet_id_!r} was never recommended at those terms and has more "
                    f"than one contract on record ({shown}); pass price= as well"
                )
        # Pick from the records already resolved above rather than looking the
        # id up again — the short form may have widened to a whole view, and
        # re-querying it would come back empty.
        rec = None
        if not known.empty:
            want = self._contract(dict(zip(("book", "point"), asked, strict=True)))
            matches = [r for r in known.to_dict("records")
                       if all(w is None or c == w
                              for c, w in zip(self._contract(r), want, strict=True))]
            chosen = matches[-1] if matches else known.to_dict("records")[-1]
            rec = {str(k): v for k, v in chosen.items()}
        if rec is None and not fields:
            raise ValueError(f"unknown bet {bet_id_!r}: pass its fields to place an unlisted bet")
        base: dict[str, Any] = dict(rec or {})
        base.update(fields or {})
        if not self.seeded:
            raise ValueError("seed the ledger before placing bets")
        if stake < 0:
            raise ValueError("a placed stake cannot be negative")
        price_ = price if price is not None else _clean(base.get("price"))
        if stake > 0 and price_ is None:
            raise ValueError("a placed bet needs a price")
        player = _clean(base.get("player"))
        row = {
            "record_type": PLACED, "recorded_at": at, "league": base.get("league"),
            "bet_id": bet_id_, "slate_stamp": _clean(base.get("slate_stamp")),
            "kind": base.get("kind") or ("prop" if player else "game"),
            "game_id": base.get("game_id"), "market": base.get("market"),
            "side": base.get("side"), "player": player,
            "point": point if point is not None else _clean(base.get("point")),
            "book": book if book is not None else _clean(base.get("book")),
            "price": price_, "stake": float(stake),
            "p_model": _clean(base.get("p_model")), "p_fair": _clean(base.get("p_fair")),
            "note": note if note is not None else ("skipped" if stake == 0 else None),
        }
        self._append([row])
        return row

    def skip(self, bet_id_: str, *, at: object, note: str | None = None) -> dict[str, Any]:
        """Decline a recommendation: closed at stake zero, never open."""
        return self.place(bet_id_, 0.0, at=at, note=note or "skipped")

    def settle(self, results: pd.DataFrame | None, *, at: object,
               stamp: str | None = None) -> pd.DataFrame:
        """Settle open bets from graded ``results`` (``bet_id`` + ``result``).

        Profit is booked at the placed stakes and prices, summed over the
        bet's placements; CLV columns ride along when the results carry them.
        Pending or unknown results leave the bet open; a bet already settled
        is skipped, so re-running a grade changes nothing.
        """
        if results is None or results.empty or "bet_id" not in results.columns:
            return empty_ledger()
        open_ = self.open_bets()
        if open_.empty:
            return empty_ledger()
        placements = self._placements()
        open_ids = set(open_["bet_id"])
        # A position opened before the number joined the id is keyed by its
        # view alone. Today's grading recomputes the id *with* the number, so
        # the two no longer match — resolve the old one rather than leave a
        # real open bet unsettled and stripped of its CLV. Only when the view
        # holds exactly one open id, so this can never pick between rungs.
        by_view: dict[str, list[str]] = {}
        for open_id in open_ids:
            by_view.setdefault(view_id(open_id), []).append(open_id)
        rows = []
        for r in results.drop_duplicates(subset="bet_id").to_dict("records"):
            result = str(r.get("result") or "")
            target = r["bet_id"]
            if target not in open_ids:
                candidates = by_view.get(view_id(target), [])
                if len(candidates) != 1:
                    continue
                target = candidates[0]
            if result not in SETTLED_RESULTS:
                continue
            legs = placements[placements["bet_id"] == target]
            profit = float(sum(
                settle_profit(result, float(leg["stake"]), float(leg["price"]), leg["book"])
                for leg in legs.to_dict("records")
            ))
            rows.append(self._settled_row(legs, result, profit, at, stamp, r))
        return self._append(rows)

    def settle_from_finals(self, finals: pd.DataFrame | None, *, at: object,
                           league: str | None = None, stamp: str | None = None) -> pd.DataFrame:
        """Settle open *game* bets straight from final scores (``Bet.grade``).

        The safety net under :meth:`settle`: a bet placed off an earlier card
        that the graded slate no longer carries still closes once its game
        has a final. Props stay open — they need box scores, not a score.
        """
        if finals is None or finals.empty:
            return empty_ledger()
        scores: dict[str, tuple[float, float]] = {}
        for g in finals.to_dict("records"):
            hs, as_ = _clean(g.get("home_score")), _clean(g.get("away_score"))
            if hs is not None and as_ is not None:
                scores[str(g["game_id"])] = (float(hs), float(as_))
        open_ = self.open_bets()
        placements = self._placements()
        rows = []
        for bet in open_.to_dict("records"):
            if league is not None and bet.get("league") != league:
                continue
            if bet.get("market") not in _GAME_MARKETS or _clean(bet.get("player")):
                continue
            final = scores.get(str(bet.get("game_id")))
            if final is None:
                continue
            legs = placements[placements["bet_id"] == bet["bet_id"]]
            graded = []
            for leg in legs.to_dict("records"):
                ticket = Bet(game_id=str(leg["game_id"]), market=str(leg["market"]),
                             side=str(leg["side"]), book=str(leg.get("book") or ""),
                             price=float(leg["price"]), stake=float(leg["stake"]),
                             p_model=float(_clean(leg.get("p_model")) or 0.5),
                             point=_clean(leg.get("point")))
                graded.append((float(leg["stake"]), *ticket.grade(*final)))
            profit = float(sum(p for _, _, p in graded))
            result = max(graded, key=lambda g: g[0])[1]  # the largest placement's grade
            rows.append(self._settled_row(legs, result, profit, at, stamp, {}))
        return self._append(rows)

    @staticmethod
    def _settled_row(legs: pd.DataFrame, result: str, profit: float, at: object,
                     stamp: str | None, extra: dict[Any, Any]) -> dict[str, Any]:
        first = legs.iloc[-1]
        stake = float(legs["stake"].sum())
        # The stake-weighted price the bet was actually taken at.
        price = float((legs["price"] * legs["stake"]).sum() / stake) if stake > 0 else None
        return {
            "record_type": SETTLED, "recorded_at": at, "league": first["league"],
            "bet_id": first["bet_id"], "slate_stamp": stamp or _clean(first["slate_stamp"]),
            "kind": first["kind"], "game_id": first["game_id"], "market": first["market"],
            "side": first["side"], "player": _clean(first["player"]),
            "point": _clean(first["point"]), "book": _clean(first["book"]),
            "price": price, "stake": stake, "p_model": _clean(first["p_model"]),
            "p_fair": _clean(first["p_fair"]), "result": result, "profit": profit,
            "closing_price": _clean(extra.get("closing_price")),
            "closing_point": _clean(extra.get("closing_point")),
            "price_clv": _clean(extra.get("price_clv")),
            "line_clv": _clean(extra.get("line_clv")),
            "note": _clean(extra.get("note")),
        }

    # ---- views ----------------------------------------------------------
    def _placements(self) -> pd.DataFrame:
        placed = self.frame[self.frame["record_type"] == PLACED]
        return placed[placed["stake"] > 0]

    def settled_ids(self) -> set[str]:
        return set(self.frame.loc[self.frame["record_type"] == SETTLED, "bet_id"])

    def placed_bets(self) -> pd.DataFrame:
        """One row per placed bet (stake > 0): total stake, weighted price, terms."""
        placements = self._placements()
        if placements.empty:
            return pd.DataFrame(columns=["bet_id", "league", "kind", "game_id", "market", "side",
                                         "player", "point", "book", "price", "stake",
                                         "placed_at", "settled"])
        rows = []
        settled = self.settled_ids()
        for bid, legs in placements.groupby("bet_id", sort=False):
            last = legs.iloc[-1]
            stake = float(legs["stake"].sum())
            rows.append({
                "bet_id": bid, "league": last["league"], "kind": last["kind"],
                "game_id": last["game_id"], "market": last["market"], "side": last["side"],
                "player": _clean(last["player"]), "point": _clean(last["point"]),
                "book": _clean(last["book"]),
                "price": float((legs["price"] * legs["stake"]).sum() / stake),
                "stake": stake, "placed_at": legs["recorded_at"].max(),
                "settled": bid in settled,
            })
        return pd.DataFrame(rows)

    def open_bets(self) -> pd.DataFrame:
        bets = self.placed_bets()
        return bets[~bets["settled"]].reset_index(drop=True) if not bets.empty else bets

    def open_exposure(self) -> float:
        open_ = self.open_bets()
        return float(open_["stake"].sum()) if not open_.empty else 0.0

    def seed_amount(self) -> float:
        seeds = self.frame.loc[self.frame["record_type"] == SEED, "profit"]
        return float(seeds.iloc[0]) if len(seeds) else 0.0

    def curve(self) -> pd.DataFrame:
        """The bankroll after every money event, in the order recorded."""
        money = self.frame[self.frame["record_type"].isin([SEED, ADJUST, SETTLED])].copy()
        if money.empty:
            return pd.DataFrame(columns=["recorded_at", "record_type", "league", "bet_id",
                                         "market", "result", "amount", "bankroll"])
        money["amount"] = money["profit"].fillna(0.0)
        money["bankroll"] = money["amount"].cumsum()
        return money[["recorded_at", "record_type", "league", "bet_id", "market", "result",
                      "amount", "bankroll"]].reset_index(drop=True)

    def current_bankroll(self) -> float:
        curve = self.curve()
        return float(curve["bankroll"].iloc[-1]) if len(curve) else 0.0

    def peak_bankroll(self) -> float:
        curve = self.curve()
        return float(curve["bankroll"].max()) if len(curve) else 0.0

    def drawdown(self) -> float:
        peak = self.peak_bankroll()
        return max(0.0, 1.0 - self.current_bankroll() / peak) if peak > 0 else 0.0

    def pnl(self, by: tuple[str, ...] = ("league",)) -> pd.DataFrame:
        """Settled P&L grouped by ``by`` (league, market, kind…)."""
        settled = self.frame[self.frame["record_type"] == SETTLED]
        columns = [*by, "bets", "wins", "losses", "staked", "profit", "roi"]
        if settled.empty:
            return pd.DataFrame(columns=columns)
        rows = []
        for keys, part in settled.groupby(list(by), sort=True, dropna=False):
            keys = keys if isinstance(keys, tuple) else (keys,)
            staked = float(part["stake"].sum())
            profit = float(part["profit"].sum())
            rows.append({
                **dict(zip(by, keys, strict=True)),
                "bets": len(part),
                "wins": int((part["result"] == "win").sum()),
                "losses": int((part["result"] == "loss").sum()),
                "staked": staked, "profit": profit,
                "roi": profit / staked if staked > 0 else float("nan"),
            })
        return pd.DataFrame(rows, columns=columns)

    def latest_recommendations(self, league: str | None = None) -> pd.DataFrame:
        """The newest card per league, with each row's ``bet_id`` and whether
        it has been placed, skipped or settled — the operator's to-do list."""
        rec = self.frame[self.frame["record_type"] == RECOMMENDED]
        if league is not None:
            rec = rec[rec["league"] == league]
        if rec.empty:
            return rec.assign(status=pd.Series(dtype=object))
        newest = rec.groupby("league")["slate_stamp"].transform("max")
        rec = rec[rec["slate_stamp"] == newest]
        placed = self.frame[self.frame["record_type"] == PLACED]
        placed_ids = set(placed.loc[placed["stake"] > 0, "bet_id"])
        skipped_ids = set(placed.loc[placed["stake"] == 0, "bet_id"]) - placed_ids
        settled = self.settled_ids()
        # Prices still open on each *view*, so a new number on a view already
        # held can be judged against what is holding it rather than against its
        # own id. This is what lets a genuinely different rung through while a
        # line that merely ticked stays one bet.
        open_now = self.open_bets()
        open_prices: dict[str, list[Any]] = {}
        for r in open_now.to_dict("records"):
            open_prices.setdefault(view_id(r["bet_id"]), []).append(_clean(r.get("price")))

        def status(bid: str, stake: float, price: object) -> str:
            if bid in settled:
                return "settled"
            if bid in placed_ids:
                return "placed"
            if bid in skipped_ids:
                return "skipped"
            if not stake > 0:
                return "paper"
            held = open_prices.get(view_id(bid), [])
            if any(same_position(price, other) for other in held):
                return "placed"
            return "open"

        rec = rec.assign(status=[
            status(b, s, p) for b, s, p in
            zip(rec["bet_id"], rec["stake"], rec["price"], strict=True)
        ])
        return rec.reset_index(drop=True)

    def holding(self, league: str, game_id: object, market: object, side: object,
                player: object = None, *, price: object = None) -> dict[str, Any] | None:
        """The open bet that already covers this row, or ``None``.

        One place decides "is this view already on the books?", so the runner's
        card and the operator's to-do cannot drift apart on it. Matching is on
        the **view** — the number is not part of the question — and then on
        implied probability, so a line that ticked is the position already held
        while a rung priced somewhere else entirely is not
        (:data:`SAME_POSITION_TOLERANCE`).
        """
        want = view_id(bet_id(league, game_id, market, side, player))
        for row in self.open_bets().to_dict("records"):
            if view_id(row["bet_id"]) != want:
                continue
            if same_position(price, row.get("price")):
                return {str(k): v for k, v in row.items()}
        return None

    def state(self) -> LedgerState:
        settled = self.frame[self.frame["record_type"] == SETTLED]
        last = settled["recorded_at"].max() if len(settled) else None
        open_ = self.open_bets()
        return LedgerState(
            seed=self.seed_amount(), current=self.current_bankroll(),
            peak=self.peak_bankroll(), drawdown=self.drawdown(),
            open_exposure=self.open_exposure(), n_open=len(open_), n_settled=len(settled),
            last_settled_at=None if last is None or pd.isna(last) else pd.Timestamp(last),
        )


def results_from_graded(
    league: str,
    games: pd.DataFrame | None,
    props: pd.DataFrame | None,
) -> pd.DataFrame:
    """Graded slate frames → the ``bet_id`` + ``result`` rows :meth:`Ledger.settle` takes.

    Game rows key by (game, market, side); prop rows add the player. CLV
    columns ride along where the grade attached a close.
    """
    columns = ["bet_id", "result", "closing_price", "closing_point", "price_clv", "line_clv"]
    rows = []
    for frame, is_prop in ((games, False), (props, True)):
        if frame is None or frame.empty or "result" not in frame.columns:
            continue
        for r in frame.to_dict("records"):
            player = _clean(r.get("player")) if is_prop else None
            rows.append({
                "bet_id": bet_id(league, r.get("game_id"), r.get("market"), r.get("side"),
                                 player, r.get("point")),
                "result": r.get("result"),
                **{c: _clean(r.get(c)) for c in columns[2:]},
            })
    return pd.DataFrame(rows, columns=columns)
