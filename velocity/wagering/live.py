"""Live-slate orchestration — a provider snapshot → staked recommendations.

The backtest proved the engine on a line *archive*; this runs the identical
engine on **today's board**. Given one internally-consistent provider snapshot
(events + their book lines, e.g. from The Odds API ``/odds``), it:

1. **canonicalizes sides** — a provider names spread/moneyline sides by team and
   totals by ``Over``/``Under``; :func:`build_slate` speaks ``home``/``away`` and
   ``over``/``under``. Because the event names its own home/away teams in the same
   snapshot, this remap is an exact, reliable lookup — no fuzzy matching.
2. **resolves teams to the model's universe** — the provider spells a team
   (``"Kansas City Chiefs"``) differently from the fitted ratings' key (``"KC"``).
   :func:`resolve_team` bridges that with an alias table plus a normalized
   fallback, and — critically — returns ``None`` rather than guess, so an
   unmatched game is *skipped and reported*, never silently mis-projected.
3. hands projections + canonical lines to :func:`build_slate` in **live mode**
   (``exclude_closing=False``), and returns the staked :class:`BetLog` plus the
   list of games it could not resolve.

The only real-world seam is (2); everything else is deterministic and offline
testable. CLV isn't measured here — that comes later, against the true closing
snapshot from the archive.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping
from typing import Any

import pandas as pd

from velocity.models.game_nfl import GameProjection
from velocity.wagering.bet_log import BetLog
from velocity.wagering.slate import SlateConfig, build_slate

# Full team name (normalized) → nflverse abbreviation, the key the NFL ratings
# use. NCAAF has no such fixed table (250+ teams); it leans on the normalized
# fallback and reports the misses.
NFL_TEAM_ALIASES: dict[str, str] = {
    "arizona cardinals": "ARI",
    "atlanta falcons": "ATL",
    "baltimore ravens": "BAL",
    "buffalo bills": "BUF",
    "carolina panthers": "CAR",
    "chicago bears": "CHI",
    "cincinnati bengals": "CIN",
    "cleveland browns": "CLE",
    "dallas cowboys": "DAL",
    "denver broncos": "DEN",
    "detroit lions": "DET",
    "green bay packers": "GB",
    "houston texans": "HOU",
    "indianapolis colts": "IND",
    "jacksonville jaguars": "JAX",
    "kansas city chiefs": "KC",
    "las vegas raiders": "LV",
    "los angeles chargers": "LAC",
    "los angeles rams": "LA",
    "miami dolphins": "MIA",
    "minnesota vikings": "MIN",
    "new england patriots": "NE",
    "new orleans saints": "NO",
    "new york giants": "NYG",
    "new york jets": "NYJ",
    "philadelphia eagles": "PHI",
    "pittsburgh steelers": "PIT",
    "san francisco 49ers": "SF",
    "seattle seahawks": "SEA",
    "tampa bay buccaneers": "TB",
    "tennessee titans": "TEN",
    "washington commanders": "WAS",
}

_TOTAL_SIDES = {"over": "over", "under": "under"}


def _normalize(name: str) -> str:
    """Lowercase and strip to alphanumerics for tolerant name comparison."""
    return re.sub(r"[^a-z0-9]+", "", str(name).lower())


def resolve_team(
    name: str,
    known_teams: Iterable[str],
    aliases: dict[str, str] | None = None,
) -> str | None:
    """Map a provider team name to the model's rating key, or ``None`` if unsure.

    Resolution order: exact key → alias table (by normalized name) → unique
    normalized match against ``known_teams``. Returning ``None`` on ambiguity or
    a miss is deliberate: a wrong match silently mis-prices a game, so we skip and
    report instead.
    """
    known = list(known_teams)
    known_set = set(known)
    if name in known_set:
        return name

    aliases = NFL_TEAM_ALIASES if aliases is None else aliases
    norm = _normalize(name)
    # Normalize the alias keys too, so "Kansas City Chiefs" and "kansascitychiefs"
    # both resolve regardless of how the table is written.
    aliased = {_normalize(k): v for k, v in aliases.items()}.get(norm)
    if aliased is not None and aliased in known_set:
        return aliased

    # Unique normalized match (handles punctuation/casing drift).
    matches = [team for team in known if _normalize(team) == norm]
    if len(matches) == 1:
        return matches[0]
    return None


def nickname_aliases(
    provider_names: Iterable[str], known_teams: Iterable[str]
) -> dict[str, str]:
    """Provider "School Nickname" names → a school-keyed team universe.

    The Odds API names NCAAF teams with their nickname ("Georgia Bulldogs",
    "Ole Miss Rebels"); CFBD — and therefore the fitted model — keys them by
    school ("Georgia", "Ole Miss"). A provider name resolves to the known team
    whose normalized name is a **prefix** of the provider's; when several known
    schools prefix-match ("Georgia" and "Georgia Southern" against
    "Georgia Southern Eagles"), the longest school name wins. A name with no
    prefix match is simply absent — the caller's resolve path skips and
    reports it, never guesses.
    """
    known_by_norm = {_normalize(team): team for team in known_teams}
    out: dict[str, str] = {}
    for name in provider_names:
        norm = _normalize(str(name))
        candidates = [
            (len(known_norm), team)
            for known_norm, team in known_by_norm.items()
            if known_norm and norm.startswith(known_norm)
        ]
        if candidates:
            out[str(name)] = max(candidates)[1]
    return out


_ST_SUFFIX = re.compile(r"\bSt\.?\b")


def exchange_aliases(
    names_by_code: Mapping[str, str],
    known_teams: Iterable[str],
    fixups: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Venue team code → the model's rating key, resolved from the venue's own names.

    An exchange labels teams with opaque codes (``SJSU``, ``txst``) whose
    meaning is venue-specific — ``sdst`` is South Dakota State on one exchange
    and San Diego State on the other — so a table built here is valid **only**
    for the venue whose payload produced ``names_by_code``, and tables from two
    venues must never be merged.

    Resolution tries, in order: an explicit ``fixups`` entry, the code itself
    (NFL codes are already our rating keys), the venue's display name, and a
    school-prefix match on that name (the college case, where the model keys by
    school and the venue writes "San Jose St."). A code that resolves to
    nothing is simply absent, and the caller skips and reports it rather than
    guessing — a wrong team silently mis-prices a game.
    """
    known = list(known_teams)
    fixups = fixups or {}
    expanded = {code: _ST_SUFFIX.sub("State", str(name)) for code, name in names_by_code.items()}
    prefix_matched = nickname_aliases(expanded.values(), known)

    out: dict[str, str] = {}
    for code, name in expanded.items():
        fixed = fixups.get(code)
        team = (
            (fixed if fixed in known else None)
            or resolve_team(code, known)
            or resolve_team(name, known)
            or prefix_matched.get(name)
        )
        if team is not None:
            out[code] = team
    return out


def apply_team_aliases(
    lines: pd.DataFrame,
    events: pd.DataFrame,
    aliases: Mapping[str, str],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Rewrite one venue's team codes to rating keys in both frames.

    Exchange rows carry venue-specific codes in ``side`` and in the events
    frame's teams. Translating both here — while the venue's own alias table is
    still in scope — lets boards from different venues be concatenated safely;
    afterwards every row speaks the same team language, so codes can no longer
    collide across venues. Over/under sides pass through untouched, and rows
    naming a team that did not resolve are dropped rather than guessed.
    """
    if events.empty:
        return lines.copy(), events.copy()

    mapped_events = events.copy()
    for column in ("home_team", "away_team"):
        mapped_events[column] = mapped_events[column].map(lambda v: aliases.get(str(v)))
    mapped_events = mapped_events.dropna(subset=["home_team", "away_team"]).reset_index(drop=True)

    if lines.empty:
        return lines.copy(), mapped_events

    keep_games = set(mapped_events["game_id"].astype(str))
    mapped_lines = lines[lines["game_id"].astype(str).isin(keep_games)].copy()

    def _side(value: object) -> object:
        raw = str(value)
        if raw.strip().lower() in _TOTAL_SIDES:
            return raw
        return aliases.get(raw)

    mapped_lines["side"] = mapped_lines["side"].map(_side)
    mapped_lines = mapped_lines[mapped_lines["side"].notna()].reset_index(drop=True)
    return mapped_lines, mapped_events


def align_game_ids(
    lines: pd.DataFrame,
    events: pd.DataFrame,
    base_events: pd.DataFrame,
    max_hours: float = 36.0,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Re-key one venue's rows onto a base board's game ids.

    Every venue invents its own game id — The Odds API an event uuid, Kalshi an
    event ticker, Polymarket a slug — so the same game arrives three times under
    three names. Left that way each copy is projected separately and no price is
    ever shopped across venues, which is the whole point of carrying an exchange
    board.

    Matching is on the canonical team pair (so :func:`apply_team_aliases` must
    have run first) plus kickoff proximity. The time tolerance is generous
    because venues disagree about what a game's time even is: Kalshi's tickers
    carry an ET calendar date with no clock, and Polymarket's slugs a UTC date,
    so a prime-time game legitimately lands a day apart across venues. A venue
    game matching no base game is dropped — an unshoppable duplicate is worse
    than a missing row.
    """
    if events.empty or base_events.empty:
        return lines.iloc[0:0].copy(), events.iloc[0:0].copy()

    time_col = "kickoff" if "kickoff" in events.columns else "date"
    venue = events.assign(_when=pd.to_datetime(events[time_col], errors="coerce"))
    base = base_events.assign(_base_when=pd.to_datetime(base_events["kickoff"], errors="coerce"))

    merged = venue.merge(
        base[["game_id", "home_team", "away_team", "_base_when"]].rename(
            columns={"game_id": "_base_game_id"}
        ),
        on=["home_team", "away_team"],
        how="inner",
    )
    if merged.empty:
        return lines.iloc[0:0].copy(), events.iloc[0:0].copy()

    gap = (merged["_when"] - merged["_base_when"]).abs()
    # A venue that supplies no usable time still matches on the team pair; a
    # team pair meets at most once in a season's window, so this is safe.
    within = gap.isna() | (gap <= pd.Timedelta(hours=max_hours))
    merged = merged[within]
    if merged.empty:
        return lines.iloc[0:0].copy(), events.iloc[0:0].copy()

    # Keep the closest base game when a pair somehow meets twice in the window.
    merged = merged.sort_values("_when").drop_duplicates("game_id", keep="first")
    id_map = dict(
        zip(
            merged["game_id"].astype(str),
            merged["_base_game_id"].astype(str),
            strict=True,
        )
    )

    mapped_events = events[events["game_id"].astype(str).isin(id_map)].copy()
    mapped_events["game_id"] = mapped_events["game_id"].astype(str).map(id_map)
    mapped_lines = lines[lines["game_id"].astype(str).isin(id_map)].copy()
    if not mapped_lines.empty:
        mapped_lines["game_id"] = mapped_lines["game_id"].astype(str).map(id_map)
        # line_id embeds the old game id; re-stamp so ids stay unique per venue.
        mapped_lines["line_id"] = (
            mapped_lines["game_id"] + "|" + mapped_lines["line_id"].astype(str)
        )
    return mapped_lines.reset_index(drop=True), mapped_events.reset_index(drop=True)


def canonicalize_sides(lines: pd.DataFrame, events: pd.DataFrame) -> pd.DataFrame:
    """Remap provider side labels to ``home``/``away``/``over``/``under``.

    Spread/moneyline sides carry the team name; a total's sides are ``Over`` /
    ``Under``. Using each event's own home/away names (same snapshot), this is an
    exact per-game lookup. Rows whose side can't be mapped are dropped.
    """
    if lines.empty:
        return lines.copy()
    home_by_game = dict(zip(events["game_id"].astype(str), events["home_team"], strict=False))
    away_by_game = dict(zip(events["game_id"].astype(str), events["away_team"], strict=False))

    def _side(row: Mapping[Any, Any]) -> str | None:
        raw = str(row["side"])
        low = raw.strip().lower()
        if low in _TOTAL_SIDES:
            return _TOTAL_SIDES[low]
        gid = str(row["game_id"])
        if raw == home_by_game.get(gid):
            return "home"
        if raw == away_by_game.get(gid):
            return "away"
        return None

    out = lines.copy()
    out["side"] = [_side(row) for row in out.to_dict("records")]
    return out[out["side"].notna()].reset_index(drop=True)


def neutral_site_map(
    events: pd.DataFrame,
    schedule: pd.DataFrame | None,
    known_teams: Iterable[str],
    aliases: dict[str, str] | None = None,
    *,
    max_hours: float = 48.0,
) -> dict[str, bool]:
    """``game_id`` → whether the league schedule marks that game neutral-site.

    The odds board never says where a game is played, but the league schedule
    does (nflverse ``location``, CFBD ``neutral_site``), and every model wrapper
    already takes ``neutral_site`` — the flag simply never reached them live.
    Priced with home-field nobody has, the international NFL slate and the
    neutral-site college openers are each a few points wrong before a rating is
    consulted (docs/SYSTEM_REVIEW.md §3.2).

    Matching is on the resolved team pair in either orientation (providers
    disagree about which side is "home" at a neutral site) plus kickoff
    proximity. A board game the schedule does not carry is simply absent from
    the map — the caller prices it as it did before, never guesses.
    """
    if schedule is None or schedule.empty or events.empty:
        return {}
    if "neutral_site" not in schedule.columns:
        return {}
    known = list(known_teams)
    rows = schedule.dropna(subset=["home_team", "away_team", "kickoff"])
    index: dict[tuple[str, str], list[tuple[pd.Timestamp, bool]]] = {}
    for rec in rows.to_dict("records"):
        home = resolve_team(str(rec["home_team"]), known, aliases)
        away = resolve_team(str(rec["away_team"]), known, aliases)
        if home is None or away is None:
            continue
        when = pd.to_datetime(rec["kickoff"], errors="coerce")
        if pd.isna(when):
            continue
        flag = bool(rec["neutral_site"]) if pd.notna(rec["neutral_site"]) else False
        index.setdefault((home, away), []).append((when, flag))

    tolerance = pd.Timedelta(hours=max_hours)
    out: dict[str, bool] = {}
    for event in events.to_dict("records"):
        home = resolve_team(str(event["home_team"]), known, aliases)
        away = resolve_team(str(event["away_team"]), known, aliases)
        if home is None or away is None:
            continue
        when = pd.to_datetime(event.get("kickoff"), errors="coerce")
        candidates = index.get((home, away), []) + index.get((away, home), [])
        if not candidates:
            continue
        if pd.isna(when):
            nearest = candidates[0]
        else:
            nearest = min(candidates, key=lambda pair: abs(pair[0] - when))
            if abs(nearest[0] - when) > tolerance:
                continue
        out[str(event["game_id"])] = nearest[1]
    return out


def project_board(
    events: pd.DataFrame,
    project: Callable[[str, str], GameProjection],
    known_teams: Iterable[str],
    aliases: dict[str, str] | None = None,
    neutral_by_game: Mapping[str, bool] | None = None,
) -> tuple[dict[str, GameProjection], list[dict[str, str]]]:
    """Resolve each event's teams and project it; return ``{game_id: proj}`` + skips.

    Factored out of :func:`build_live_slate` so a caller can reuse the very same
    projections (e.g. for a report) without simulating twice. A game whose teams
    don't resolve is skipped and reported. ``neutral_by_game`` (from
    :func:`neutral_site_map`) reaches projectors that accept ``neutral_site``;
    a game absent from it prices with home field, as before.
    """
    import inspect

    known = list(known_teams)
    params = inspect.signature(project).parameters
    # Schedule-aware projectors (rest spots) take the event's kickoff too.
    accepts_kickoff = "kickoff" in params
    accepts_neutral = "neutral_site" in params
    neutral = neutral_by_game or {}
    projections: dict[str, GameProjection] = {}
    unresolved: list[dict[str, str]] = []
    for event in events.to_dict("records"):
        gid = str(event["game_id"])
        home = resolve_team(str(event["home_team"]), known, aliases)
        away = resolve_team(str(event["away_team"]), known, aliases)
        if home is None or away is None:
            unresolved.append(
                {
                    "game_id": gid,
                    "home_team": str(event["home_team"]),
                    "away_team": str(event["away_team"]),
                    "reason": "unresolved home" if home is None else "unresolved away",
                }
            )
            continue
        kwargs: dict[str, object] = {}
        if accepts_kickoff:
            kwargs["kickoff"] = event.get("kickoff")
        if accepts_neutral and neutral.get(gid, False):
            kwargs["neutral_site"] = True
        projections[gid] = project(home, away, **kwargs)  # type: ignore[call-arg]
    return projections, unresolved


def build_live_slate(
    events: pd.DataFrame,
    lines: pd.DataFrame,
    project: Callable[[str, str], GameProjection],
    known_teams: Iterable[str],
    config: SlateConfig | None = None,
    aliases: dict[str, str] | None = None,
) -> tuple[BetLog, list[dict[str, str]]]:
    """Run the wagering engine on one live snapshot; return the log and any skips.

    ``project(home_key, away_key)`` builds a :class:`GameProjection` from the
    fitted model. ``known_teams`` is the model's rating universe (e.g.
    ``ratings.teams``). Games whose teams don't resolve are skipped and returned
    in the second element so the caller can surface them.
    """
    config = config or SlateConfig(exclude_closing=False)
    projections, unresolved = project_board(events, project, known_teams, aliases)

    canonical = canonicalize_sides(lines, events)
    canonical = canonical[canonical["game_id"].astype(str).isin(projections)]
    games_min = events[["game_id", "kickoff"]].copy()
    games_min["game_id"] = games_min["game_id"].astype(str)

    log = build_slate(projections, canonical, games_min, config)
    return log, unresolved


def slate_to_frame(log: BetLog) -> pd.DataFrame:
    """Render a :class:`BetLog` as a readable slate table (one row per staked bet)."""
    rows = [
        {
            "game_id": bet.game_id,
            "market": bet.market,
            "side": bet.side,
            "point": bet.point,
            "book": bet.book,
            "price": bet.price,
            "p_model": round(bet.p_model, 4),
            "p_fair": None if bet.p_fair is None else round(bet.p_fair, 4),
            "edge": None if bet.p_fair is None else round(bet.p_model - bet.p_fair, 4),
            "stake": round(bet.stake, 4),
            "note": bet.note,
        }
        for bet in log
    ]
    cols = ["game_id", "market", "side", "point", "book", "price", "p_model",
            "p_fair", "edge", "stake", "note"]
    return pd.DataFrame(rows, columns=cols)
