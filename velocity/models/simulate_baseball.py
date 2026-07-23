"""MLB Monte Carlo — a per-inning / per-PA baseball simulator.

Football scores can be sampled as a bivariate normal over (margin, total); runs
cannot. Baseball scoring is a discrete, skewed, low-count process, and its whole
structure is a sequence of near-isolated pitcher-vs-batter matchups — so it is
simulated the honest way: draw plate-appearance outcomes from the matchup, walk a
base-out state machine, and count the runs.

The engine emits **``GameSim``-compatible** score arrays (see
:mod:`velocity.models.simulate`), which is the point: the existing de-vig / edge /
Kelly / backtest stack prices spread, total and moneyline off a ``GameSim`` and
does not care which sport produced it. So a baseball game yields two of them —
``full`` (final runs) and ``f5`` (runs through five innings, the first-5-innings
market) — and every football pricing helper works on each for free. Per-player
strikeout / total-base sample arrays ride alongside for the props phase.

**Determinism is non-negotiable**, exactly as in the football sim: every draw
comes from a caller-supplied, seeded :class:`numpy.random.Generator`, so the same
inputs and seed reproduce the same runs.

Modeling scope (honest, first-order — refine in the backtest phase):

* **Matchup** — a batter's and the opposing pitcher's five shared per-PA rates
  (K/BB/HBP/HR/in-play) are merged by the odds-ratio (Log5) method against the
  league, then the in-play mass is split by the batter's ball-in-play profile.
* **Advancement** — hits use a standard aggressive model (a single scores runners
  from 2nd and 3rd; a double scores both and sends the batter to 2nd; a triple
  clears the bases). Walks force only. Outs hold the runners (no sac-fly / GIDP
  modeling yet). Extra innings use the ghost-runner-on-2nd rule.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

import numpy as np

from velocity.features.baseball import BIP_OUTCOMES, LEAGUE_PA_RATE, PA_OUTCOMES
from velocity.models.simulate import GameSim

# Outcome indices for the eight-way per-PA distribution. K and OUT_BIP are outs.
K, BB, HBP, HR, SINGLE, DOUBLE, TRIPLE, OUT_BIP = range(8)
N_OUTCOMES = 8
_TOTAL_BASES = {SINGLE: 1, DOUBLE: 2, TRIPLE: 3, HR: 4}
_HIT_OUTCOMES = frozenset({SINGLE, DOUBLE, TRIPLE, HR})
# The reach-base outcomes (everything but a strikeout or a ball-in-play out),
# scaled by the home-field tilt.
_REACH_OUTCOMES = [BB, HBP, HR, SINGLE, DOUBLE, TRIPLE]

# Home-field advantage the production MLB model uses (see BaseballSimConfig.hfa),
# tuned so equal teams give home ~a 53-54% win probability — the MLB norm.
DEFAULT_HFA = 0.02

_LEAGUE_PA_VEC = np.array([LEAGUE_PA_RATE[o] for o in PA_OUTCOMES], dtype=float)


@dataclass(frozen=True)
class Batter:
    """A batter's projected rates: five PA outcomes + the ball-in-play profile."""

    player_id: str
    pa: np.ndarray  # PA_OUTCOMES order (k, bb, hbp, hr, in_play), sums to 1
    bip: np.ndarray  # BIP_OUTCOMES order (single, double, triple, out_bip), sums to 1


@dataclass(frozen=True)
class Pitcher:
    """A pitcher's projected five PA outcome rates (K/BB/HBP/HR/in-play)."""

    player_id: str
    pa: np.ndarray  # PA_OUTCOMES order, sums to 1


@dataclass(frozen=True)
class Team:
    """A batting order (nine batters) and the starting pitcher."""

    lineup: Sequence[Batter]
    pitcher: Pitcher


@dataclass(frozen=True)
class BaseballSimConfig:
    """Simulation size, the extra-innings cap, and the starter workload cap.

    ``starter_outs`` bounds how long a starting pitcher's stats accrue: once a
    starter has recorded that many outs, later innings no longer credit his
    strikeout/out props (a league-average bullpen finishes, at the same rates, so
    the run distribution is unchanged). ``None`` means a complete game — leave it
    unset for run/side/total pricing; set it (~18 = six innings) for realistic
    pitcher counting props.

    ``hfa`` is home-field advantage as a small relative tilt of each batter's
    reach-base outcomes: the home lineup's are scaled up by ``hfa`` and the away
    lineup's down by ``hfa``, so home scores a touch more and away a touch less —
    a margin shift toward home with the total roughly unchanged. ``0.0`` (the
    default) is a neutral, symmetric game; the production model sets ``DEFAULT_HFA``.
    """

    n_sims: int = 10_000
    max_innings: int = 30
    starter_outs: int | None = None
    hfa: float = 0.0

    def __post_init__(self) -> None:
        if self.n_sims <= 0:
            raise ValueError("n_sims must be positive")
        if self.max_innings < 9:
            raise ValueError("max_innings must be at least 9")
        if self.starter_outs is not None and self.starter_outs <= 0:
            raise ValueError("starter_outs must be positive when set")
        if not -1.0 < self.hfa < 1.0:
            raise ValueError("hfa must be in (-1, 1)")


@dataclass(frozen=True)
class BaseballSimResult:
    """The priced distributions plus per-player sample arrays.

    ``full`` and ``f5`` are ordinary :class:`GameSim` objects, so every football
    pricing helper (``p_home_win``, ``prob_home_cover``, ``prob_over``,
    ``fair_spread``, ``fair_total``) applies to each unchanged — ``f5`` simply
    prices the first-5-innings markets. The stat dicts map player id → an array of
    per-simulation counts, the raw material for player props.
    """

    full: GameSim
    f5: GameSim
    batter_total_bases: Mapping[str, np.ndarray] = field(default_factory=dict)
    batter_hits: Mapping[str, np.ndarray] = field(default_factory=dict)
    batter_home_runs: Mapping[str, np.ndarray] = field(default_factory=dict)
    batter_strikeouts: Mapping[str, np.ndarray] = field(default_factory=dict)
    pitcher_strikeouts: Mapping[str, np.ndarray] = field(default_factory=dict)
    pitcher_outs: Mapping[str, np.ndarray] = field(default_factory=dict)


def batter_from_rates(
    player_id: str, pa_rates: Mapping[str, float], bip_profile: Mapping[str, float]
) -> Batter:
    """Build a :class:`Batter` from PA-outcome and ball-in-play rate mappings."""
    pa = np.array([pa_rates[o] for o in PA_OUTCOMES], dtype=float)
    bip = np.array([bip_profile[o] for o in BIP_OUTCOMES], dtype=float)
    return Batter(player_id=player_id, pa=pa, bip=bip)


def pitcher_from_rates(player_id: str, pa_rates: Mapping[str, float]) -> Pitcher:
    """Build a :class:`Pitcher` from a PA-outcome rate mapping."""
    pa = np.array([pa_rates[o] for o in PA_OUTCOMES], dtype=float)
    return Pitcher(player_id=player_id, pa=pa)


def combine_matchup(
    batter_pa: np.ndarray, pitcher_pa: np.ndarray, league_pa: np.ndarray | None = None
) -> np.ndarray:
    """Merge batter and pitcher PA rates by the odds-ratio (Log5) method.

    ``raw_i = batter_i · pitcher_i / league_i``, renormalized to sum to 1. If the
    batter is league-average the result is the pitcher's rates and vice versa —
    the desired behavior. Returns the five-outcome PA vector (``PA_OUTCOMES``).
    """
    league = _LEAGUE_PA_VEC if league_pa is None else league_pa
    raw = batter_pa * pitcher_pa / league
    total = raw.sum()
    if total <= 0:  # pragma: no cover - degenerate inputs
        return league / league.sum()
    return raw / total


def _tilt_offense(dist: np.ndarray, factor: float) -> np.ndarray:
    """Scale a matchup's reach-base outcomes by ``1 + factor`` and renormalize.

    ``factor > 0`` lifts offense (more baserunners and power, fewer outs);
    ``factor < 0`` suppresses it. Outs (K, in-play out) are untouched, so a
    positive tilt raises run expectancy and a negative one lowers it. ``0`` is the
    identity.
    """
    if factor == 0.0:
        return dist
    tilted = dist.copy()
    tilted[_REACH_OUTCOMES] = tilted[_REACH_OUTCOMES] * (1.0 + factor)
    return tilted / tilted.sum()


def _scale_hr(dist: np.ndarray, factor: float) -> np.ndarray:
    """Scale a matchup's home-run outcome by ``factor`` and renormalize.

    ``factor`` is a multiplicative HR park factor (1.0 = neutral, >1 a hitter's
    park); the mass moved on/off HR is absorbed proportionally across the other
    outcomes by the renormalization, so the distribution still sums to 1. Applied
    to both lineups in a game, since both bat in the home park.
    """
    if factor == 1.0:
        return dist
    scaled = dist.copy()
    scaled[HR] = scaled[HR] * factor
    return scaled / scaled.sum()


def matchup_distribution(batter: Batter, pitcher: Pitcher) -> np.ndarray:
    """The eight-way per-PA outcome distribution for a batter vs a pitcher.

    Combines the five shared outcomes, then splits the in-play mass by the
    batter's ball-in-play profile into single/double/triple/out. Sums to 1.
    """
    m5 = combine_matchup(batter.pa, pitcher.pa)
    in_play = m5[4]
    dist = np.empty(N_OUTCOMES, dtype=float)
    dist[K] = m5[0]
    dist[BB] = m5[1]
    dist[HBP] = m5[2]
    dist[HR] = m5[3]
    dist[SINGLE] = in_play * batter.bip[0]
    dist[DOUBLE] = in_play * batter.bip[1]
    dist[TRIPLE] = in_play * batter.bip[2]
    dist[OUT_BIP] = in_play * batter.bip[3]
    return dist


@dataclass
class HalfInning:
    """The result of one half-inning: runs, outs made, and the events for stats."""

    runs: int
    outs: int
    next_index: int
    events: list[tuple[int, int]]  # (lineup slot, outcome index) per plate appearance


def simulate_half_inning(
    cum_matchups: Sequence[np.ndarray],
    start_index: int,
    rng: np.random.Generator,
    *,
    ghost: bool = False,
    runs_to_win: int | None = None,
) -> HalfInning:
    """Bat through ``cum_matchups`` (cumulative per-PA distributions) until 3 outs.

    ``start_index`` is the lineup slot due up; the batting order wraps. ``ghost``
    seeds a runner on second (extra innings). ``runs_to_win`` ends the inning the
    instant that many runs score (a walk-off), before the third out.
    """
    outs = 0
    runs = 0
    on1 = on2 = on3 = False
    if ghost:
        on2 = True
    idx = start_index
    events: list[tuple[int, int]] = []

    while outs < 3:
        slot = idx % len(cum_matchups)
        u = rng.random()
        outcome = int(np.searchsorted(cum_matchups[slot], u, side="right"))
        if outcome >= N_OUTCOMES:  # pragma: no cover - float guard on the top edge
            outcome = OUT_BIP
        events.append((slot, outcome))
        idx += 1

        if outcome == K:
            outs += 1
        elif outcome == OUT_BIP:
            # Sac fly: with fewer than two outs, a runner on third tags and scores.
            if outs < 2 and on3:
                runs += 1
                on3 = False
            outs += 1
        elif outcome in (BB, HBP):
            if on1 and on2 and on3:
                runs += 1  # bases loaded, forced in; stays loaded
            elif on1 and on2:
                on3 = True
            elif on1:
                on2 = True
            on1 = True
        elif outcome == HR:
            runs += 1 + int(on1) + int(on2) + int(on3)
            on1 = on2 = on3 = False
        elif outcome == SINGLE:  # 2nd & 3rd score, 1st -> 2nd, batter -> 1st
            runs += int(on2) + int(on3)
            on1, on2, on3 = True, on1, False
        elif outcome == DOUBLE:  # 2nd & 3rd score, 1st -> 3rd, batter -> 2nd
            runs += int(on2) + int(on3)
            on1, on2, on3 = False, True, on1
        else:  # TRIPLE — everyone scores, batter -> 3rd
            runs += int(on1) + int(on2) + int(on3)
            on1, on2, on3 = False, False, True

        if runs_to_win is not None and runs >= runs_to_win:
            break

    return HalfInning(runs=runs, outs=outs, next_index=idx, events=events)


def _accumulate(
    events: Sequence[tuple[int, int]],
    lineup: Sequence[Batter],
    bat_tb: dict[str, float],
    bat_h: dict[str, float],
    bat_hr: dict[str, float],
    bat_k: dict[str, float],
) -> int:
    """Fold a half-inning's events into per-batter tallies; return pitcher K count."""
    pitcher_k = 0
    for slot, outcome in events:
        pid = lineup[slot].player_id
        if outcome == K:
            bat_k[pid] += 1
            pitcher_k += 1
        elif outcome in _HIT_OUTCOMES:
            bat_h[pid] += 1
            bat_tb[pid] += _TOTAL_BASES[outcome]
            if outcome == HR:
                bat_hr[pid] += 1
    return pitcher_k


def simulate_game(
    home: Team,
    away: Team,
    rng: np.random.Generator,
    config: BaseballSimConfig | None = None,
    *,
    park_hr_factor: float = 1.0,
) -> BaseballSimResult:
    """Simulate ``config.n_sims`` games and return priced distributions + stats.

    The away team bats the top of each inning against the home pitcher and the
    home team the bottom against the away pitcher, with the batting order carried
    across innings. The home team does not bat in the ninth when already ahead;
    ninth-and-later home half-innings end on a walk-off; ties go to ghost-runner
    extra innings up to ``config.max_innings``.

    ``park_hr_factor`` is the home park's multiplicative HR factor (1.0 = neutral),
    applied to both lineups' home-run rate — a hitter's park (>1) lifts the total,
    a pitcher's park (<1) suppresses it.
    """
    config = config or BaseballSimConfig()
    n = config.n_sims

    # Park then home-field advantage. The park HR factor scales both lineups (both
    # bat here); HFA then lifts the home lineup's reach-base outcomes and trims the
    # away lineup's (the home team bats the bottom half of innings).
    hfa = config.hfa
    away_cum = [
        np.cumsum(_tilt_offense(_scale_hr(matchup_distribution(b, home.pitcher), park_hr_factor),
                                -hfa))
        for b in away.lineup
    ]
    home_cum = [
        np.cumsum(_tilt_offense(_scale_hr(matchup_distribution(b, away.pitcher), park_hr_factor),
                                hfa))
        for b in home.lineup
    ]

    home_final = np.zeros(n, dtype=np.int64)
    away_final = np.zeros(n, dtype=np.int64)
    home_f5 = np.zeros(n, dtype=np.int64)
    away_f5 = np.zeros(n, dtype=np.int64)

    all_batters = [b for t in (home, away) for b in t.lineup]
    bat_tb: dict[str, np.ndarray] = {b.player_id: np.zeros(n, np.int64) for b in all_batters}
    bat_h: dict[str, np.ndarray] = {pid: np.zeros(n, np.int64) for pid in bat_tb}
    bat_hr: dict[str, np.ndarray] = {pid: np.zeros(n, np.int64) for pid in bat_tb}
    bat_k: dict[str, np.ndarray] = {pid: np.zeros(n, np.int64) for pid in bat_tb}
    pitchers = (home.pitcher.player_id, away.pitcher.player_id)
    pit_k: dict[str, np.ndarray] = {pid: np.zeros(n, np.int64) for pid in pitchers}
    pit_outs: dict[str, np.ndarray] = {pid: np.zeros(n, np.int64) for pid in pitchers}
    cap = config.starter_outs

    for s in range(n):
        away_runs = home_runs = 0
        away_f5_runs = home_f5_runs = 0
        away_idx = home_idx = 0
        g_tb = dict.fromkeys(bat_tb, 0.0)
        g_h = dict.fromkeys(bat_tb, 0.0)
        g_hr = dict.fromkeys(bat_tb, 0.0)
        g_k = dict.fromkeys(bat_tb, 0.0)
        g_pit_k = dict.fromkeys(pitchers, 0)
        g_pit_outs = dict.fromkeys(pitchers, 0)

        inning = 1
        while True:
            # Top half — away bats vs the home pitcher.
            top = simulate_half_inning(away_cum, away_idx, rng)
            away_idx = top.next_index
            away_runs += top.runs
            if inning <= 5:
                away_f5_runs += top.runs
            hp = home.pitcher.player_id
            k_recorded = _accumulate(top.events, away.lineup, g_tb, g_h, g_hr, g_k)
            if cap is None or g_pit_outs[hp] < cap:  # starter still in the game
                g_pit_k[hp] += k_recorded
                g_pit_outs[hp] += top.outs

            # Bottom half — home bats, unless it is the 9th+ and home already leads.
            home_bats = not (inning >= 9 and home_runs > away_runs)
            if home_bats:
                runs_to_win = (away_runs - home_runs + 1) if inning >= 9 else None
                bottom = simulate_half_inning(home_cum, home_idx, rng, runs_to_win=runs_to_win)
                home_idx = bottom.next_index
                home_runs += bottom.runs
                if inning <= 5:
                    home_f5_runs += bottom.runs
                ap = away.pitcher.player_id
                k_recorded = _accumulate(bottom.events, home.lineup, g_tb, g_h, g_hr, g_k)
                if cap is None or g_pit_outs[ap] < cap:
                    g_pit_k[ap] += k_recorded
                    g_pit_outs[ap] += bottom.outs

            decided = inning >= 9 and away_runs != home_runs
            if decided or inning >= config.max_innings:
                break
            inning += 1

        away_final[s] = away_runs
        home_final[s] = home_runs
        away_f5[s] = away_f5_runs
        home_f5[s] = home_f5_runs
        for pid in bat_tb:
            bat_tb[pid][s] = g_tb[pid]
            bat_h[pid][s] = g_h[pid]
            bat_hr[pid][s] = g_hr[pid]
            bat_k[pid][s] = g_k[pid]
        for pid in pitchers:
            pit_k[pid][s] = g_pit_k[pid]
            pit_outs[pid][s] = g_pit_outs[pid]

    return BaseballSimResult(
        full=GameSim(home_score=home_final.astype(float), away_score=away_final.astype(float)),
        f5=GameSim(home_score=home_f5.astype(float), away_score=away_f5.astype(float)),
        batter_total_bases=bat_tb,
        batter_hits=bat_h,
        batter_home_runs=bat_hr,
        batter_strikeouts=bat_k,
        pitcher_strikeouts=pit_k,
        pitcher_outs=pit_outs,
    )
