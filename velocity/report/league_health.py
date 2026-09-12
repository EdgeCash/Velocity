"""Is this league dark because it is out of season, or because a feed broke?

The slate prints ``no games on the board (off-season or empty snapshot)`` and
leaves it there, which is two very different situations wearing one sentence.
The WNBA made the difference concrete: its 2026 regular season ended on 31
August and twelve days later neither the committed schedule nor the live
wehoop release carried a single postseason row — while in 2024 and 2025 the
playoffs had started **two days** after the regular season did. Nothing in the
pipeline said so, because nothing looked.

What separates the two cases is already on disk. The league's own committed
schedule says whether games are coming, when the last one was, and — the part
that actually settles it — whether this league was playing on this date in the
seasons already banked. A sport that played on 12 September in each of the
last two years and has nothing today is not off-season.

Pure functions of frames; offline-testable, no network.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

# How far either side of today to look when asking "was this league playing on
# this date in previous years?" A fortnight absorbs a schedule that shifts by a
# week between seasons without absorbing a whole missing round of playoffs.
SEASON_WINDOW_DAYS = 14
# Days without a game before an in-season league is worth reporting on. Long
# enough to clear an all-star break or the gap before a postseason, short
# enough that a stalled feed is caught within a cycle.
QUIET_DAYS = 7


@dataclass(frozen=True)
class LeagueHealth:
    """What the committed schedule says about why a league has no board."""

    league: str
    upcoming: int  # games scheduled ahead of now
    days_quiet: float | None  # since the last game, None when the frame is empty
    last_game: pd.Timestamp | None
    played_on_this_date_before: int  # prior seasons with a game near today
    prior_seasons: int

    @property
    def suspicious(self) -> bool:
        """True when the silence does not look like an off-season.

        Three conditions together, and all three are needed: nothing is
        scheduled ahead, the league has been quiet for a while, and it was
        playing at this point in the calendar in at least half the seasons
        already banked.
        """
        if self.upcoming > 0 or self.days_quiet is None:
            return False
        if self.days_quiet < QUIET_DAYS:
            return False
        return (self.prior_seasons > 0
                and self.played_on_this_date_before * 2 >= self.prior_seasons)

    def describe(self) -> str:
        """One line for the run log, saying which of the two cases this is.

        Careful about what it claims. The committed frame holds **played**
        games only (``refresh_datasets.py`` keeps it that way), so ``upcoming``
        is zero for most leagues most of the time and says nothing on its own
        — only the quiet stretch and the prior seasons carry a verdict.
        """
        if self.days_quiet is None:
            return f"{self.league}: the committed schedule is empty"
        when = "—" if self.last_game is None else self.last_game.date().isoformat()
        if self.upcoming > 0:
            return (f"{self.league}: {self.upcoming} game(s) scheduled ahead — "
                    f"an empty board is the odds feed, not the league")
        days = int(self.days_quiet)
        quiet = f"last game {when} ({days} day{'' if days == 1 else 's'} ago)"
        if self.suspicious:
            return (f"{self.league}: {quiet}, and it was playing on this date in "
                    f"{self.played_on_this_date_before} of {self.prior_seasons} "
                    f"banked season(s) — the SCHEDULE FEED has published nothing "
                    f"beyond it")
        if days < QUIET_DAYS:
            return f"{self.league}: {quiet} — the schedule is current"
        if self.prior_seasons:
            return (f"{self.league}: {quiet}; the {self.prior_seasons} banked "
                    f"season(s) were idle around this date too — reads as the "
                    f"off-season")
        return f"{self.league}: {quiet}, with no earlier season to compare against"


def league_health(games: pd.DataFrame, now: pd.Timestamp, league: str) -> LeagueHealth:
    """Read the committed schedule for why this league has no board."""
    if games is None or games.empty or "kickoff" not in games.columns:
        return LeagueHealth(league, 0, None, None, 0, 0)
    frame = games.copy()
    frame["kickoff"] = pd.to_datetime(frame["kickoff"], errors="coerce")
    frame = frame.dropna(subset=["kickoff"])
    if frame.empty:
        return LeagueHealth(league, 0, None, None, 0, 0)

    now = pd.Timestamp(now)
    upcoming = int((frame["kickoff"] > now).sum())
    past = frame[frame["kickoff"] <= now]
    last_game = pd.Timestamp(past["kickoff"].max()) if not past.empty else None
    days_quiet = None if last_game is None else float((now - last_game).days)

    # Was this league playing at this point of the calendar in earlier years?
    # Compared on the day of the year so a season's own dates do not matter.
    played_before = 0
    prior = 0
    if "season" in frame.columns:
        frame = frame.assign(
            _season=pd.to_numeric(frame["season"], errors="coerce")).dropna(
            subset=["_season"])
    if "_season" in frame.columns and not frame.empty:
        this_season = float(frame["_season"].max())
        earlier = {float(s) for s in frame["_season"].unique() if float(s) < this_season}
        near_today = (frame["kickoff"].dt.dayofyear - now.dayofyear).abs()
        playing = {float(s) for s
                   in frame.loc[near_today <= SEASON_WINDOW_DAYS, "_season"].unique()}
        prior = len(earlier)
        played_before = len(earlier & playing)
    return LeagueHealth(league, upcoming, days_quiet, last_game, played_before, prior)
