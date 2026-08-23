"""Live slate runner — today's board → staked bet recommendations.

Ties the whole system together: fit the projection model on committed history,
pull the current board from The Odds API (or a saved snapshot for offline runs),
and run the identical wagering engine the backtest used to produce a staked slate
of recommended bets, plus any games it couldn't resolve to the model's teams.

    # offline, from a saved Odds API /odds payload:
    python scripts/run_live_slate.py --league nfl --data datasets/nfl \
        --snapshot-file snap.json

    # live (needs THE_ODDS_API in the environment):
    THE_ODDS_API=... python scripts/run_live_slate.py --league nfl --data datasets/nfl

This does not place bets — it prints the slate for a human to act on. CLV is
measured later, against the closing snapshot from the archive.
"""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
from velocity.features.scores import fit_scores_ratings
from velocity.ingest.local import load_games
from velocity.ingest.theoddsapi import extract_events, normalize_odds_events
from velocity.models.game_nfl import GameProjection
from velocity.models.game_scores import ScoresGameModel, ScoresModelConfig
from velocity.models.simulate import SimConfig
from velocity.report.slate_xlsx import (
    export_slate_workbook,
    plays_display,
    projections_display,
    props_display,
)
from velocity.util.seed import make_rng
from velocity.wagering.live import canonicalize_sides, project_board, slate_to_frame
from velocity.wagering.slate import SlateConfig, build_slate


def _find_games(folder: Path) -> Path:
    for ext in (".parquet", ".pq", ".csv"):
        candidate = folder / f"games{ext}"
        if candidate.exists():
            return candidate
    raise SystemExit(f"need a games file in {folder}/ to fit the model")


def _load_snapshot(args: argparse.Namespace) -> object:
    if args.snapshot_file:
        return json.loads(Path(args.snapshot_file).read_text())
    from velocity.ingest.theoddsapi import TheOddsAPIClient  # network path

    client = TheOddsAPIClient.from_env()
    return client.odds_payload(args.league)


def _find_plays(folder: Path) -> Path | None:
    for ext in (".parquet", ".pq", ".csv"):
        candidate = folder / f"plays{ext}"
        if candidate.exists():
            return candidate
    return None


def _epa_ratings_rows(ratings: object, plays_per_game: float) -> list[dict]:
    """Per-team rows from an EPA fit, converted to points per game."""
    rows = []
    for team in ratings.teams:  # type: ignore[attr-defined]
        off = ratings.offense.get(team, 0.0) * plays_per_game  # type: ignore[attr-defined]
        # Defense is "EPA allowed vs average" — negative is good.
        dfn = ratings.defense.get(team, 0.0) * plays_per_game  # type: ignore[attr-defined]
        rows.append({"team": team, "off": off, "def": dfn, "net": off - dfn})
    return rows


def _scores_ratings_rows(ratings: object) -> list[dict]:
    """Per-team rows from a scores fit (already in points/runs per game)."""
    rows = []
    for team in ratings.teams:  # type: ignore[attr-defined]
        off = ratings.offense.get(team, 0.0)  # type: ignore[attr-defined]
        dfn = ratings.defense.get(team, 0.0)  # type: ignore[attr-defined]
        rows.append({"team": team, "off": off, "def": dfn, "net": off - dfn})
    return rows


def _ratings_frame(league: str, model: object, scores_model: object) -> pd.DataFrame:
    """The power-ratings table behind the live fit: team · off · def · net.

    ``off``/``def`` are deviations from league average in the league's
    natural per-game scale (points; runs for MLB; points per 100
    possessions for the basketball leagues, which also carry ``pace``).
    ``def`` is points *allowed* vs average, so negative is good and
    ``net = off − def`` is the expected margin against an average
    opponent on a neutral floor.
    """
    from velocity.backtest.lab import (
        BlendedGameModel,
        PaceEfficiencyModel,
        StarterAwareModel,
    )

    pace: dict[str, float] = {}
    if isinstance(model, PaceEfficiencyModel):
        rows = _scores_ratings_rows(model.eff_model.ratings)
        pace = {t: model.pace_league + 2.0 * model.pace_dev.get(t, 0.0)
                for t in model.eff_model.ratings.teams}
        scale = "pts/100 poss"
    elif isinstance(model, StarterAwareModel):
        rows = _scores_ratings_rows(model.ratings)
        scale = "runs/gm (ex-starter)"
    elif isinstance(model, BlendedGameModel):
        # The 50/50 college blend: each half converted to points first.
        epa = {r["team"]: r for r in _epa_ratings_rows(
            model.primary.ratings, plays_per_game=65.0)}
        rows = []
        for row in _scores_ratings_rows(model.secondary.ratings):
            half = epa.get(row["team"], {"off": 0.0, "def": 0.0, "net": 0.0})
            rows.append({"team": row["team"],
                         "off": 0.5 * row["off"] + 0.5 * half["off"],
                         "def": 0.5 * row["def"] + 0.5 * half["def"],
                         "net": 0.5 * row["net"] + 0.5 * half["net"]})
        scale = "pts/gm (blend)"
    else:
        ratings = getattr(scores_model, "ratings", None) or getattr(
            model, "ratings", None)
        if ratings is None:
            return pd.DataFrame()
        rows = _scores_ratings_rows(ratings)
        scale = "runs/gm" if league == "mlb" else "pts/gm"

    frame = pd.DataFrame(rows)
    if frame.empty:
        return frame
    frame["pace"] = frame["team"].map(pace).astype(float) if pace else float("nan")
    frame["scale"] = scale
    frame = frame.sort_values("net", ascending=False).reset_index(drop=True)
    frame["rank"] = frame.index + 1
    for col in ("off", "def", "net", "pace"):
        frame[col] = frame[col].astype(float).round(2)
    return frame


def _build_projection(
    args: argparse.Namespace,
) -> tuple[Callable[[str, str], GameProjection], list[str], pd.DataFrame]:
    """Fit the league's promoted ratings from the committed data → ``(project, teams)``.

    NFL: the recency-weighted EPA fit (docs/MODEL_LAB.md — Brier 0.2234 vs
    0.2343 for the schedule-only fit over 2014–2025), trained on the trailing
    four seasons exactly as validated; falls back to the scores fit when the
    data folder carries no plays file. NCAAF: the scores fit.
    """
    if not args.data:
        raise SystemExit(f"--data is required for {args.league} (a folder with a games file)")
    folder = Path(args.data)
    plays_path = _find_plays(folder) if args.league == "nfl" else None

    if plays_path is not None:
        from velocity.features.team import (
            DEFAULT_RECENCY_HALF_LIFE,
            fit_qb_ratings,
            fit_ratings,
            recency_weights,
        )
        from velocity.ingest.local import load_plays
        from velocity.models.game_nfl import NFLGameModel, NFLModelConfig

        plays = load_plays(plays_path)
        cutoff = int(plays["season"].max()) - 3
        plays = plays[plays["season"] >= cutoff]
        weights = recency_weights(plays, DEFAULT_RECENCY_HALF_LIFE)
        if "passer_player_id" in plays.columns and plays["passer_player_id"].notna().any():
            # The promoted fit (docs/MODEL_LAB.md Round 3): QB decomposed out
            # of the offense, detected starter priced back in at projection.
            ratings: object = fit_qb_ratings(plays, weights=weights)
            kind = "QB-adjusted recency EPA"
        else:  # plays without passer identity (older datasets, fixtures)
            ratings = fit_ratings(plays, weights=weights)
            kind = "recency-weighted EPA"
        nfl_model = NFLGameModel(ratings, NFLModelConfig(sim=SimConfig(n_sims=args.n_sims)))  # type: ignore[arg-type]
        print(f"NFL ratings: {kind} fit on {len(plays)} plays "
              f"(seasons {cutoff}+, half-life {DEFAULT_RECENCY_HALF_LIFE:g} wks)")

        # Rest spots (docs/MODEL_LAB.md Round 4): bye +1.0 / short week −1.0 on
        # top of the fit — small, consistent across every tested grid.
        from velocity.backtest.lab import RestAdjustedModel

        schedule = load_games(_find_games(folder), league="nfl")
        rest_model = RestAdjustedModel(nfl_model, schedule)

        # Wind on totals (Round 5 constants, live forecast): best-effort — a
        # failed forecast fetch just leaves totals unadjusted.
        model: object = rest_model
        try:
            if args.snapshot_file:  # offline runs (tests/CI) skip the network
                raise RuntimeError("offline snapshot run")
            from velocity.backtest.lab import WeatherAdjustedModel
            from velocity.features.weather import forecast_frame

            forecast = forecast_frame(days=max(args.max_days, 1) + 1)
            if not forecast.empty:
                model = WeatherAdjustedModel(rest_model, forecast,  # type: ignore[arg-type]
                                             points_per_mph=0.30)
                print(f"wind forecast: {len(forecast)} stadium-days fetched")
        except Exception as exc:  # noqa: BLE001 - weather is a nicety live
            print(f"wind forecast skipped ({exc})")

        def project_epa(
            home: str, away: str, kickoff: object = None
        ) -> GameProjection:
            return model.project(  # type: ignore[attr-defined,return-value]
                home, away, rng=make_rng(), kickoff=kickoff
            )

        nfl_ratings = pd.DataFrame(_epa_ratings_rows(ratings, plays_per_game=63.0))
        nfl_ratings["pace"] = float("nan")
        nfl_ratings["scale"] = "pts/gm (EPA)"
        nfl_ratings = nfl_ratings.sort_values("net", ascending=False).reset_index(drop=True)
        nfl_ratings["rank"] = nfl_ratings.index + 1
        for col in ("off", "def", "net"):
            nfl_ratings[col] = nfl_ratings[col].round(2)
        return project_epa, list(ratings.teams), nfl_ratings

    games = load_games(_find_games(folder), league=args.league)
    # Per-league outcome-noise calibration. Football's constants are the
    # lab-validated ones; MLB (runs) and WNBA (points) use the leagues'
    # historical margin/total sigmas — content-surface defaults, honest but
    # not yet lab-tuned (their datasets carry no closing lines to tune on).
    sims = {
        "ncaaf": SimConfig(sd_margin=17.0, sd_total=16.0, n_sims=args.n_sims),
        "mlb": SimConfig(sd_margin=3.2, sd_total=4.6, n_sims=args.n_sims),
        "wnba": SimConfig(sd_margin=12.5, sd_total=15.0, n_sims=args.n_sims),
        # NCAAB: walk-forward residual sds (docs/BUILD_NCAAB.md N2).
        "ncaab": SimConfig(sd_margin=13.0, sd_total=18.5, n_sims=args.n_sims),
    }
    sim = sims.get(args.league, SimConfig(n_sims=args.n_sims))
    # NCAAF: λ=10 promoted by the college lab; MLB: λ=100 promoted by the
    # summer lab (docs/MODEL_LAB.md MLB Round 1 — heavy shrinkage wins in a
    # league whose true team spread is small). WNBA: recency half-life 8
    # week-buckets promoted (WNBA Round 1 — an interior optimum, 12/16/24
    # all worse). NCAAB: λ=0.5 — 360 conference-clustered teams leave the
    # fit compressed at heavier penalties (BUILD_NCAAB.md N2's compression
    # finding). The NFL scores path is only a no-plays fallback and keeps
    # the default.
    ridge = {"ncaaf": 10.0, "mlb": 100.0, "wnba": 10.0, "ncaab": 0.5}.get(
        args.league, 25.0)
    recency_hl = {"wnba": 8.0, "ncaab": 6.0}.get(args.league)
    weights = None
    if recency_hl is not None:
        from velocity.features.scores import scores_recency_weights

        weights = scores_recency_weights(games, recency_hl)
    scores_model = ScoresGameModel(
        fit_scores_ratings(games, ridge_lambda=ridge, weights=weights),
        ScoresModelConfig(sim=sim),
    )
    model: object = scores_model
    kind = f"scores fit (λ={ridge:g})" + (
        f", recency-{recency_hl:g}" if recency_hl is not None else "")

    box_file = folder / "team_box.parquet"
    if args.league in ("wnba", "ncaab") and box_file.exists():
        # The promoted basketball configurations: WNBA pace×efficiency with
        # recency-8 (docs/MODEL_LAB.md WNBA Round 2); NCAAB pace×efficiency
        # with recency-6 plus the Torvik pseudo-games prior at K=6
        # (docs/BUILD_NCAAB.md N2 — prior-k6, the Brier winner that
        # replicated at 80k games in N3). Best-effort: any failure keeps the
        # recency scores fit above.
        try:
            from velocity.backtest.lab import fit_pace_efficiency, wnba_pace_frame

            pace = wnba_pace_frame(pd.read_parquet(box_file))
            fit_games = games
            prior_note = ""
            torvik_file = folder / "torvik.parquet"
            if args.league == "ncaab" and torvik_file.exists():
                from velocity.ingest.ncaab import torvik_pseudo_games

                teams = set(games["home_team"]) | set(games["away_team"])
                pseudo_games, pseudo_pace = torvik_pseudo_games(
                    pd.read_parquet(torvik_file), teams,
                    cutoff=pd.to_datetime(games["kickoff"]).max(), k=6,
                )
                if not pseudo_games.empty:
                    fit_games = pd.concat([games, pseudo_games], ignore_index=True)
                    pace = pd.concat([pace, pseudo_pace], ignore_index=True)
                    prior_note = f" + Torvik prior ({len(pseudo_games)} pseudo-games)"
            model = fit_pace_efficiency(
                fit_games, pace, sim, ridge_lambda=ridge, half_life=recency_hl
            )
            kind = (f"pace×efficiency (λ={ridge:g}, recency-{recency_hl:g})"
                    f"{prior_note}, {len(pace)} games' possessions")
        except Exception as exc:  # noqa: BLE001 - never blocks the slate
            print(f"pace×efficiency skipped: {exc}")

    starters_file = folder / "starters.parquet"
    if args.league == "mlb" and starters_file.exists():
        # The promoted MLB configuration (docs/MODEL_LAB.md MLB Round 2): the
        # starter decomposition at q=160 on the λ=100 team fit — Brier 0.2449
        # vs 0.2463 team-only, calibration error halved. Today's probables
        # come from the keyless statsapi schedule (public pregame knowledge);
        # a game with no announced probable prices starter-neutral, which
        # collapses to the team fit. Best-effort: any failure keeps the
        # scores fit above.
        try:
            from datetime import date, timedelta

            from build_mlb_pitching import fetch_probables
            from velocity.backtest.lab import StarterAwareModel, mlb_starter_frame
            from velocity.features.team import fit_qb_ratings

            played = games.dropna(subset=["home_score", "away_score"])
            ratings = fit_qb_ratings(
                mlb_starter_frame(played, pd.read_parquet(starters_file)),
                ridge_lambda=ridge, qb_lambda=160.0, min_dropbacks=6,
            )
            today = date.today()
            lookup = fetch_probables(str(today), str(today + timedelta(days=1)))
            named = sum(1 for h, a in lookup.values() if h or a)
            model = StarterAwareModel(ratings, lookup, sim)
            kind = (f"starter decomposition (λ={ridge:g}, q=160), "
                    f"{named} games with probables")
        except Exception as exc:  # noqa: BLE001 - the SP layer never blocks the slate
            print(f"starter decomposition skipped: {exc}")

    ncaaf_plays = folder / "plays.parquet"
    if args.league == "ncaaf" and ncaaf_plays.exists():
        # The promoted college configuration (docs/MODEL_LAB.md NCAAF Round
        # 2): a 50/50 blend of the EPA fit (CFBD ppa, λ=50 on compressed
        # cells) and the scores fit above — Brier 0.1949 vs 0.1976 for the
        # scores fit alone, the best calibration recorded for college.
        from velocity.backtest.lab import BlendedGameModel, compress_plays
        from velocity.features.team import fit_ratings
        from velocity.ingest.local import load_plays
        from velocity.models.game_nfl import NFLGameModel, NFLModelConfig

        plays = load_plays(ncaaf_plays)
        cells = compress_plays(plays)
        epa_model = NFLGameModel(
            fit_ratings(cells, ridge_lambda=50.0, weights=cells["n"].astype(float)),
            NFLModelConfig(base_points=28.5, plays_per_game=65.0,
                           hfa_points=2.5, sim=sim),
        )
        model = BlendedGameModel(epa_model, scores_model, 0.5, sim)
        kind = f"EPA×scores blend (λ50/λ{ridge:g}, w=0.5) on {len(plays)} plays"

    print(f"{args.league.upper()} ratings: {kind}, {len(games)} games")

    def project(home: str, away: str) -> GameProjection:
        return model.project(home, away, rng=make_rng())  # type: ignore[attr-defined,return-value]

    return project, list(scores_model.ratings.teams), _ratings_frame(
        args.league, model, scores_model)


def main() -> None:
    parser = argparse.ArgumentParser(description="Live slate of staked recommendations")
    parser.add_argument("--league", choices=["nfl", "ncaaf", "mlb", "wnba", "ncaab"],
                        required=True)
    parser.add_argument("--data", help="folder with a games file to fit the model")
    parser.add_argument("--snapshot-file", help="saved Odds API /odds JSON (offline mode)")
    parser.add_argument("--n-sims", type=int, default=10_000)
    parser.add_argument("--min-edge", type=float, default=0.02)
    parser.add_argument("--bankroll", type=float, default=100.0)
    # The August board carries the whole season's games at stale opening
    # numbers (the first live run priced 272 NFL events and "staked" 20x the
    # bankroll). A slate is this week's games: only events kicking off inside
    # the window are priced, staked, and carded.
    parser.add_argument("--max-days", type=float, default=6.0,
                        help="only price games kicking off within this many days (0 = all)")
    # NCAAF selectivity, in POINTS of total disagreement — the edge exactly as
    # backtested (docs/BACKTEST_NCAAF.md): flat totals 51.6%, but 52.8% when the
    # model differs from the number by ≥4 points (5,477 bets, positive in 7 of 10
    # seasons) and 53.4% at ≥6. Applies to full-game totals only; NCAAF spreads
    # showed no edge at any threshold, so nothing is bet there on this cut.
    parser.add_argument("--ncaaf-total-edge", type=float, default=4.0,
                        help="NCAAF: min points of total disagreement to bet (0 = off)")
    # Team totals — the censored-score derivative (docs/EDGE_RESEARCH.md §2.2).
    # Books derive them linearly from total+spread, ignoring the zero floor on
    # scores; the sim's floored scores price that mass correctly. Offline, rows
    # already in the snapshot are priced automatically; live, football boards
    # need a per-event fetch (The Odds API serves team_totals per event only).
    # The censoring study on our own closes (backtest/lab.py
    # team_total_censoring_study) found the mean bias (+0.7–1.0 pts at low
    # implied totals) but no >52.4% over-rate on *derived* numbers, so the
    # disagreement gate defaults to off — the EV gate still applies, and the
    # threshold gets calibrated once banked team-total closes accumulate.
    parser.add_argument("--team-totals", action=argparse.BooleanOptionalAction, default=True,
                        help="fetch + price team totals on live football boards")
    parser.add_argument("--team-total-edge", type=float, default=0.0,
                        help="min points of team-total disagreement to bet (0 = EV gate only)")
    # Player props: priced only when a FantasyPros projections snapshot is
    # supplied (the collect-fantasypros artifact) — the prop model simulates
    # correlated player outcomes from those consensus means. The board comes
    # from --prop-lines-file (a banked collect-football-props parquet) or, live,
    # from The Odds API's per-event endpoint. NFL-first: FP team codes resolve
    # through the NFL alias table; unresolved teams are skipped, never guessed.
    parser.add_argument("--fp-projections",
                        help="FantasyPros projections parquet (enables the prop slate)")
    parser.add_argument("--prop-lines-file",
                        help="banked PropLines parquet (offline prop board)")
    # Confidence calibration for props. 1.0 = the raw model: deliberately
    # untuned until the football prop backtest's shrink sweep picks the values
    # (docs/FOOTBALL_CUTOVER.md Phase 3) — the MLB numbers do not carry over.
    parser.add_argument("--prop-shrink", type=float, default=1.0,
                        help="prop confidence shrink toward 0.5 (1.0 = raw model)")
    parser.add_argument("--exclude-props", default="",
                        help="comma-separated prop markets to skip; '' bets all")
    # The intelligence layer (velocity/intel, docs/INTEL.md): every bet that
    # cleared the EV gate is judged against the game's context — unit matchups,
    # recent form, rest, and the injury report — and tiered into argued pick
    # sets. It confirms, demotes, or vetoes; it never promotes a bet the model
    # didn't like and never touches stakes.
    parser.add_argument("--intel", action=argparse.BooleanOptionalAction, default=True,
                        help="judge qualifying bets against stats/form/rest/injuries")
    parser.add_argument("--injuries-file",
                        help="normalized injuries parquet (the collect_fantasypros "
                             "artifact) — enables availability vetoes")
    # Pick'em board: the slip-EV engine over the same prop sim + prop lines
    # (velocity/wagering/pickem_slate) — book-fair marginals, model
    # correlation. Slips below the EV floor are simply not persisted.
    parser.add_argument("--carousel", action="store_true",
                        help="stamp matchup cards with n/total slide badges "
                             "(post the slate as one ordered thread)")
    parser.add_argument("--pickem-top", type=int, default=8,
                        help="max ranked pick'em slips to persist (0 disables)")
    parser.add_argument("--pickem-min-ev", type=float, default=1.0,
                        help="min expected return multiple for a slip (1.0 = breakeven)")
    # Leg probabilities near the payout breakevens (54-58%) are exactly where
    # the devig methods disagree; the worst-case default publishes a leg only
    # when multiplicative, Shin, and power all clear it (docs/EDGE_RESEARCH.md
    # §4). Operators also tax same-game correlation now (reduced payouts /
    # blocked combos), so slips are capped at two legs per game by default and
    # flagged when any two legs share one.
    parser.add_argument("--pickem-devig", default="worst_case",
                        choices=["multiplicative", "additive", "shin", "power", "worst_case"],
                        help="leg-probability devig (worst_case = every method must agree)")
    parser.add_argument("--pickem-max-per-game", type=int, default=2,
                        help="max legs of one slip sharing a game (0 = no cap)")
    # Parlays: legs come only from bets that already cleared the single-bet gate, are
    # priced sim-exactly (correlated within a game, independent across), and must
    # clear their own higher EV bar. Same-game combos are flagged — books reprice
    # correlated SGPs below the product payout, so their EV is an upper bound.
    parser.add_argument("--parlay-max-legs", type=int, default=3,
                        help="max legs per parlay (0 disables parlays)")
    parser.add_argument("--parlay-min-ev", type=float, default=0.05,
                        help="min combined EV per unit for a parlay to be recommended")
    parser.add_argument("--max-parlays", type=int, default=5,
                        help="max parlays to recommend per slate")
    # Portfolio sizing (docs/WAGERING.md W2, docs/EDGE_RESEARCH.md §1.2): the
    # game slate and the prop slate are sized in two independent passes that
    # never see each other, so a game's correlated exposure can stack past any
    # sane aggregate. This stage routes the whole card through
    # portfolio.size_portfolio — correlation de-scaling within each game, the
    # per-game cap, and an aggregate slate cap — and persists the combined,
    # sized card as portfolio_{league}_{stamp}.parquet. The kill-switch stays
    # unreachable until the W1 ledger supplies bankroll state.
    parser.add_argument("--portfolio", action=argparse.BooleanOptionalAction, default=True,
                        help="size the combined card through the portfolio rules")
    parser.add_argument("--max-slate-fraction", type=float, default=0.25,
                        help="aggregate cap: max fraction of bankroll staked per slate")
    parser.add_argument("--out", help="folder to persist the slate parquet (private, not git)")
    args = parser.parse_args()

    now = datetime.now(UTC)
    generated_at = pd.Timestamp(now).tz_localize(None)

    if args.out:
        Path(args.out).mkdir(parents=True, exist_ok=True)

    project, known_teams, ratings_frame = _build_projection(args)

    payload = _load_snapshot(args)
    lines = normalize_odds_events(payload)
    events = extract_events(payload)
    # Live football boards: team totals ride the per-event endpoint. Best-effort
    # — a failed fetch just leaves the three main markets on the board.
    if (args.team_totals and not args.snapshot_file
            and args.league in ("nfl", "ncaaf")):
        try:
            from velocity.ingest.theoddsapi import TheOddsAPIClient

            team_lines = TheOddsAPIClient.from_env().team_totals(args.league)
            if not team_lines.empty:
                lines = pd.concat([lines, team_lines], ignore_index=True)
                print(f"team totals: {len(team_lines)} lines joined the board")
        except Exception as exc:  # noqa: BLE001 - an optional derivative fetch
            print(f"team totals skipped: {exc}")
    n_board = len(events)
    if args.max_days > 0 and not events.empty:
        kickoff = pd.to_datetime(events["kickoff"], errors="coerce")
        window = (kickoff >= generated_at - pd.Timedelta(hours=6)) & (
            kickoff <= generated_at + pd.Timedelta(days=args.max_days)
        )
        events = events[window].reset_index(drop=True)
    print(f"=== Live slate: {args.league.upper()} — {len(events)} of {n_board} board "
          f"games inside the {args.max_days:g}-day window ===")

    frame = pd.DataFrame()
    projections: dict = {}
    canonical = pd.DataFrame()
    game_log = None
    if events.empty:
        print("no games on the board (off-season or empty snapshot)")
    else:
        # NCAAF bets totals on points of disagreement (the backtested cut); NFL
        # leaves it off and gates on probability edge alone.
        total_edge = args.ncaaf_total_edge if args.league == "ncaaf" else 0.0
        cfg = SlateConfig(
            exclude_closing=False, min_edge=args.min_edge, starting_bankroll=args.bankroll,
            min_total_disagreement=total_edge,
            min_team_total_disagreement=args.team_total_edge,
        )
        if total_edge > 0.0:
            print(f"NCAAF totals filter: model must differ from the number by "
                  f"≥ {total_edge:g} points")
        # Project once, then price off those projections (reused for the workbook).
        # College: the provider names carry nicknames ("Georgia Bulldogs",
        # "Duke Blue Devils") while the fitted model keys by school ("Georgia",
        # "Duke") — bridge by prefix match.
        aliases = None
        if args.league in ("ncaaf", "ncaab"):
            from velocity.wagering.live import nickname_aliases

            provider_names = set(events["home_team"]) | set(events["away_team"])
            aliases = nickname_aliases(provider_names, known_teams)
        projections, unresolved = project_board(events, project, known_teams, aliases)
        canonical = canonicalize_sides(lines, events)
        canonical = canonical[canonical["game_id"].astype(str).isin(projections)]
        games_min = events[["game_id", "kickoff"]].copy()
        games_min["game_id"] = games_min["game_id"].astype(str)
        game_log = build_slate(projections, canonical, games_min, cfg)
        frame = slate_to_frame(game_log)

        if frame.empty:
            print("no bets cleared the edge threshold.")
        else:
            shown = frame.assign(stake_pct=(frame["stake"] / args.bankroll * 100).round(2))
            with pd.option_context("display.width", 160, "display.max_columns", None):
                print(f"\n{len(shown)} recommended bets (stake as % of {args.bankroll:.0f}):")
                print(shown.to_string(index=False))
            print(f"\ntotal staked: {frame['stake'].sum():.2f}")

        if unresolved:
            print(f"\n{len(unresolved)} game(s) skipped — teams not in the model's universe:")
            for u in unresolved:
                print(f"  {u['away_team']} @ {u['home_team']} ({u['reason']})")

    # Player-prop slate — priced off the FantasyPros-driven correlated prop sim
    # when a projections snapshot is supplied. Best-effort: never breaks the
    # game slate.
    props_frame = None
    props_by_game: dict = {}
    key_to_name: dict[str, str] = {}
    prop_lines_used: pd.DataFrame | None = None
    if args.fp_projections and projections and not events.empty:
        props_frame, props_by_game, key_to_name, prop_lines_used = _prop_slate(
            args, events, projections, now, generated_at
        )

    # Pick'em board — the slip-EV engine over the same prop sim + prop lines.
    # Book-fair marginals, model correlation (velocity/wagering/pickem_slate).
    # Best-effort: never breaks the game or prop slates.
    if props_by_game and prop_lines_used is not None:
        _pickem_slate(args, props_by_game, prop_lines_used, now, generated_at)

    # Parlay slate — combine the qualifying single game bets into sim-exact
    # correlated parlays. Works offline too. Prop legs stay out deliberately:
    # the game sim and the prop sim are independent draws, so a mixed same-game
    # ticket would index-pair unrelated sample arrays — the exact phantom
    # correlation the parlay engine's docstring warns against. They join once
    # the two sims share a draw.
    if projections and args.parlay_max_legs >= 2:
        _parlay_slate(args, projections, game_log, now, generated_at)

    # Portfolio sizing — the combined card through correlation de-scaling and
    # the aggregate slate cap. Best-effort; the per-slate parquets keep their
    # solo-Kelly stakes for backtest comparability.
    if args.portfolio and (not frame.empty or (props_frame is not None
                                               and not props_frame.empty)):
        _portfolio_card(args, frame, props_frame, now, generated_at)

    # Intelligence layer — judge every qualifying bet against the game's
    # evidence and emit tiered, argued pick sets. Best-effort like every
    # surface after the game slate: a failure never breaks the slate. The
    # convictions feed the deep-dive verdict band's tier chips and rationale.
    convictions = None
    if args.intel and projections and args.data:
        convictions = _intel_layer(
            args, events, projections, game_log, props_frame, now, generated_at
        )

    if args.out:
        out_dir = Path(args.out)
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = now.strftime("%Y%m%dT%H%M%SZ")
        persisted = frame.assign(league=args.league, generated_at=generated_at)
        parquet = out_dir / f"slate_{args.league}_{stamp}.parquet"
        persisted.to_parquet(parquet, index=False)
        print(f"\nwrote {len(persisted)} slate rows to {parquet}")
        # The power-ratings table behind the fit — the site's Ratings page.
        if not ratings_frame.empty:
            ratings_frame.assign(league=args.league, generated_at=generated_at).to_parquet(
                out_dir / f"ratings_{args.league}_{stamp}.parquet", index=False
            )
            print(f"wrote {len(ratings_frame)} team ratings")
        # Persist the game→teams+kickoff map so a later grader can join the
        # schedule feed's finals (a different id space) back onto these Odds-API
        # game ids.
        if not events.empty:
            games_cols = ["game_id", "home_team", "away_team", "kickoff"]
            games_map = events[games_cols].assign(league=args.league)
            games_map.to_parquet(out_dir / f"games_{args.league}_{stamp}.parquet", index=False)
        # Persist the per-game model numbers (win %, projected score, fair lines)
        # — the matchup-card data the plays app renders without re-simulating.
        if projections:
            proj_frame = _projections_frame(projections)
            proj_frame.assign(league=args.league, generated_at=generated_at).to_parquet(
                out_dir / f"projections_{args.league}_{stamp}.parquet", index=False
            )
            # Pregame total/margin distributions — tomorrow's Sim Check pins the
            # actual result at its true percentile on these.
            from velocity.report.social import distributions_frame

            distributions_frame(projections).assign(league=args.league).to_parquet(
                out_dir / f"distributions_{args.league}_{stamp}.parquet", index=False
            )
        _write_workbook(out_dir, stamp, args, events, projections, frame, props_frame,
                        generated_at)
        # Social market-vs-model cards — one shareable X-frame PNG per game plus
        # a captions file of post copy. Best-effort, like every report surface.
        # NFL identity = club codes + logos; NCAAF identity = school
        # abbreviation + official colors only (no marks — licensing posture,
        # see report/assets.py).
        if projections:
            _write_social_cards(
                args, events, projections, canonical, props_by_game, key_to_name,
                prop_lines_used, stamp,
                game_log=game_log, convictions=convictions,
            )


def _portfolio_card(
    args: argparse.Namespace,
    frame: pd.DataFrame,
    props_frame: pd.DataFrame | None,
    now: datetime,
    generated_at: pd.Timestamp,
) -> None:
    """Size the combined game + prop card through the portfolio rules.

    One correlation group per game (a spread, its total, its team totals, and
    its props share a group), the per-game cap, and the aggregate slate cap.
    Prints the exposure summary and persists the sized combined card; the
    per-slate parquets keep their solo-Kelly stakes.
    """
    try:
        from velocity.wagering.portfolio import BetCandidate, PortfolioConfig, size_portfolio

        parts = []
        if not frame.empty:
            parts.append(frame.assign(kind="game"))
        if props_frame is not None and not props_frame.empty:
            parts.append(props_frame.assign(kind="prop"))
        card = pd.concat(parts, ignore_index=True, sort=False)
        candidates = [
            BetCandidate(
                key=str(i),
                stake_fraction=float(row["stake"]) / args.bankroll,
                group=str(row["game_id"]),
            )
            for i, row in enumerate(card.to_dict("records"))
        ]
        config = PortfolioConfig(max_portfolio_fraction=args.max_slate_fraction)
        sized = size_portfolio(candidates, args.bankroll, config)
        card["stake_solo"] = card["stake"]
        card["stake"] = [round(sized[str(i)], 4) for i in range(len(card))]

        solo_total = float(card["stake_solo"].sum())
        total = float(card["stake"].sum())
        per_game = card.groupby("game_id")["stake"].sum().sort_values(ascending=False)
        print(f"\n=== Portfolio-sized card — {len(card)} bets across "
              f"{card['game_id'].nunique()} games ===")
        print(f"solo-Kelly total {solo_total:.2f} → sized total {total:.2f} "
              f"({total / args.bankroll:.1%} of bankroll, cap "
              f"{args.max_slate_fraction:.0%}; correlated same-game exposure "
              f"de-scaled at ρ={config.group_correlation:g})")
        top = ", ".join(f"{gid}: {amt:.2f}" for gid, amt in per_game.head(3).items())
        print(f"largest game exposures — {top}")

        if args.out:
            stamp = now.strftime("%Y%m%dT%H%M%SZ")
            dest = Path(args.out) / f"portfolio_{args.league}_{stamp}.parquet"
            card.assign(league=args.league, generated_at=generated_at).to_parquet(
                dest, index=False
            )
            print(f"wrote the sized card to {dest}")
    except Exception as exc:  # noqa: BLE001 - sizing never breaks the slates
        print(f"portfolio sizing skipped: {exc}")


def _intel_layer(  # noqa: PLR0913 - the orchestration seam takes the slate's parts
    args: argparse.Namespace,
    events: pd.DataFrame,
    projections: dict,
    game_log: object,
    props_frame: pd.DataFrame | None,
    now: datetime,
    generated_at: pd.Timestamp,
) -> list | None:
    """Judge every qualifying bet against its game's context (velocity/intel).

    Builds the context library point-in-time (``as_of`` = this run) from the
    same committed datasets the model fit on, plus the optional injuries
    snapshot, then assesses the game slate and the prop slate and prints the
    tiered pick sets. Persists ``intel_{league}_{stamp}.parquet`` beside the
    slate. A missing dataset or snapshot only narrows the evidence (signals
    abstain); any failure leaves the slate untouched.
    """
    try:
        from velocity.intel import (
            ContextLibrary,
            assess_bets,
            build_pick_sets,
            default_game_signals,
            default_prop_signals,
            intel_frame,
            render_pick_sets,
        )
        from velocity.wagering.bet_log import Bet

        folder = Path(args.data)
        games = load_games(_find_games(folder), league=args.league)
        plays_path = _find_plays(folder)
        plays = None
        if plays_path is not None:
            from velocity.ingest.local import load_plays

            plays = load_plays(plays_path)
        injuries = None
        if args.injuries_file:
            injuries = pd.read_parquet(args.injuries_file)
            n_out = int(injuries["is_out"].sum()) if "is_out" in injuries.columns else 0
            print(f"\nintel: injuries snapshot loaded ({n_out} genuine outs)")
        else:
            print("\nintel: no injuries snapshot (--injuries-file) — availability "
                  "signals abstain")
        lib = ContextLibrary.build(games, plays, injuries, as_of=generated_at)

        kickoffs = {
            str(gid): kick
            for gid, kick in zip(
                events["game_id"].astype(str),
                pd.to_datetime(events["kickoff"], errors="coerce"),
                strict=True,
            )
        }
        contexts = {
            str(gid): lib.context_for(
                str(gid), proj.away_team, proj.home_team, kickoffs.get(str(gid))
            )
            for gid, proj in projections.items()
        }

        game_bets = list(game_log) if game_log is not None else []  # type: ignore[call-overload]
        prop_bets: list[Bet] = []
        if props_frame is not None and not props_frame.empty:
            for row in props_frame.to_dict("records"):
                point = row.get("point")
                fair = row.get("p_fair")
                prop_bets.append(Bet(
                    game_id=str(row["game_id"]), market=str(row["market"]),
                    side=str(row["side"]), book=str(row["book"]),
                    price=float(row["price"]), stake=float(row["stake"]),
                    p_model=float(row["p_model"]),
                    point=None if point is None or pd.isna(point) else float(point),
                    player=str(row["player"]),
                    p_fair=None if fair is None or pd.isna(fair) else float(fair),
                ))
        if not game_bets and not prop_bets:
            return None

        # The prop-matchup signal orients by the player's team; the FantasyPros
        # snapshot already carries model team codes for every priced player.
        player_teams: dict[str, str] = {}
        if prop_bets and args.fp_projections:
            fp = pd.read_parquet(args.fp_projections)
            if {"player_name", "team"} <= set(fp.columns):
                named = fp.dropna(subset=["player_name", "team"])
                named = named.drop_duplicates(subset=["player_name"])
                player_teams = {
                    str(r["player_name"]): str(r["team"])
                    for r in named.to_dict("records")
                }

        convictions = assess_bets(game_bets, contexts, default_game_signals())
        convictions += assess_bets(prop_bets, contexts, default_prop_signals(player_teams))
        sets = build_pick_sets(convictions)
        print("\n" + render_pick_sets(
            sets, heading=f"{args.league.upper()} intelligence card"
        ))
        skipped = len(game_bets) + len(prop_bets) - len(convictions)
        if skipped:
            print(f"{skipped} bet(s) had no game context — not assessed, never guessed")

        if args.out and convictions:
            stamp = now.strftime("%Y%m%dT%H%M%SZ")
            dest = Path(args.out) / f"intel_{args.league}_{stamp}.parquet"
            intel_frame(convictions).assign(
                league=args.league, generated_at=generated_at
            ).to_parquet(dest, index=False)
            print(f"wrote {len(convictions)} intel rows to {dest}")
        return convictions
    except Exception as exc:  # noqa: BLE001 - the intel layer never breaks the slate
        print(f"intel layer skipped: {exc}")
        return None


def _write_workbook(  # noqa: PLR0913 - a report writer with several inputs
    out_dir: Path,
    stamp: str,
    args: argparse.Namespace,
    events: pd.DataFrame,
    projections: dict,
    frame: pd.DataFrame,
    props_frame: pd.DataFrame | None,
    generated_at: pd.Timestamp,
) -> None:
    """Write the slate as a formatted workbook alongside the parquet (best-effort)."""
    try:
        proj_disp = projections_display(projections, events)
        plays_disp = plays_display(frame, events, args.bankroll)
        props_disp = (
            props_display(props_frame, events, args.bankroll)
            if props_frame is not None and not props_frame.empty
            else None
        )
        dest = out_dir / f"slate_{args.league}_{stamp}.xlsx"
        export_slate_workbook(
            dest, proj_disp, plays_disp, props_disp,
            league=args.league, generated_at=str(generated_at), bankroll=args.bankroll,
        )
        print(f"wrote workbook to {dest}")
    except Exception as exc:  # noqa: BLE001 - the workbook is a convenience, never fatal
        print(f"workbook export skipped: {exc}")


def _projections_frame(projections: dict) -> pd.DataFrame:
    """One row of model numbers per projected game, for the plays app's cards."""
    rows = []
    for gid, proj in projections.items():
        rows.append({
            "game_id": str(gid),
            "away": proj.away_team,
            "home": proj.home_team,
            "n_sims": int(proj.sim.home_score.shape[0]),
            "mu_away": round(float(proj.mu_away), 2),
            "mu_home": round(float(proj.mu_home), 2),
            "p_home_win": round(float(proj.p_home_win()), 4),
            "fair_spread": round(float(proj.fair_spread()), 2),
            "fair_total": round(float(proj.fair_total()), 2),
        })
    return pd.DataFrame(rows)


def _prop_slate(
    args: argparse.Namespace,
    events: pd.DataFrame,
    projections: dict,
    now: datetime,
    generated_at: pd.Timestamp,
) -> tuple[pd.DataFrame | None, dict, dict[str, str], pd.DataFrame | None]:
    """Price the prop board off the FantasyPros-driven correlated sim.

    Returns ``(frame, props_by_game, key_to_name, prop_lines)`` — the persisted
    prop-slate frame (it carries the raw ``p_model``/``p_fair`` per bet, which
    is exactly what the shrink-sweep backtest replays) plus the per-game sims
    and the name index, which the social cards' watch strip reuses. Empty
    results when the board or projections don't materialize.
    """
    try:
        from velocity.models.props_football import game_props, name_index_from_fp
        from velocity.wagering.live import NFL_TEAM_ALIASES, resolve_team
        from velocity.wagering.props_slate import build_prop_slate, prop_slate_to_frame

        fp = pd.read_parquet(args.fp_projections)
        if "league" in fp.columns:
            fp = fp[fp["league"].astype(str) == args.league]
        if fp.empty:
            print(f"prop slate skipped: no {args.league} rows in {args.fp_projections}")
            return None, {}, {}, None
        from velocity.dfs.pipeline import is_season_long

        if is_season_long(fp):
            # Season totals would price a 4,800-yard passing prop as a weekly
            # mean — refuse rather than misprice (same guard as the DFS lineup).
            print("prop slate skipped: FP snapshot is season-long (week 0); "
                  "weekly props can't be priced from season totals")
            return None, {}, {}, None

        if args.prop_lines_file:
            prop_lines = pd.read_parquet(args.prop_lines_file)
        elif args.snapshot_file:
            print("prop slate skipped: offline run needs --prop-lines-file")
            return None, {}, {}, None
        else:
            from velocity.ingest.theoddsapi import TheOddsAPIClient

            prop_lines = TheOddsAPIClient.from_env().player_props(args.league)
        if prop_lines.empty:
            print("prop slate: no prop lines on the board")
            return None, {}, {}, None

        codes = sorted(set(NFL_TEAM_ALIASES.values()))
        fp_teams = set(fp["team"].astype(str))
        props_by_game: dict[str, object] = {}
        for event in events.to_dict("records"):
            gid = str(event["game_id"])
            if gid not in projections:
                continue
            home = resolve_team(str(event["home_team"]), codes, NFL_TEAM_ALIASES)
            away = resolve_team(str(event["away_team"]), codes, NFL_TEAM_ALIASES)
            if home not in fp_teams or away not in fp_teams:
                continue  # a team FP doesn't cover is skipped, never guessed
            props_by_game[gid] = game_props(fp, home, away, make_rng(),
                                            _prop_config(args))
        if not props_by_game:
            print("prop slate: no games matched FantasyPros team coverage")
            return None, {}, {}, None

        key_to_name = {}
        for r in fp.drop_duplicates(subset=["player_name"]).to_dict("records"):
            from velocity.models.props_football import player_key

            key_to_name[player_key(r.get("player_id"), r.get("player_name"))] = str(
                r.get("player_name")
            )
        log, unresolved = build_prop_slate(
            props_by_game,
            prop_lines,
            name_index_from_fp(fp),
            config=SlateConfig(
                exclude_closing=False, min_edge=args.min_edge,
                starting_bankroll=args.bankroll,
                prob_shrink=args.prop_shrink,
                exclude_markets=frozenset(
                    m.strip() for m in args.exclude_props.split(",") if m.strip()
                ),
            ),
        )
        frame = prop_slate_to_frame(log)
        print(f"\n=== {args.league.upper()} props — {len(prop_lines)} lines, "
              f"{len(frame)} recommended ===")
        if not frame.empty:
            with pd.option_context("display.width", 160, "display.max_columns", None):
                print(frame.to_string(index=False))
        if unresolved:
            print(f"{len(unresolved)} prop player(s) unresolved (skipped, never guessed)")
        if args.out:
            stamp = now.strftime("%Y%m%dT%H%M%SZ")
            dest = Path(args.out) / f"slate_{args.league}_props_{stamp}.parquet"
            frame.assign(league=args.league, generated_at=generated_at).to_parquet(
                dest, index=False
            )
            print(f"wrote {len(frame)} prop rows to {dest}")
        return frame, props_by_game, key_to_name, prop_lines
    except Exception as exc:  # noqa: BLE001 - the prop slate never breaks the game slate
        print(f"prop slate skipped: {exc}")
        return None, {}, {}, None


def _pickem_slate(
    args: argparse.Namespace,
    props_by_game: dict,
    prop_lines: pd.DataFrame,
    now: datetime,
    generated_at: pd.Timestamp,
) -> None:
    """Build and persist the pick'em legs board + ranked slips (best-effort).

    Legs pair the devigged book probability (the staking marginal) with the
    correlated prop sim; slips are ranked by calibrated-correlation EV. Two
    parquets join the slate artifact: ``slate_<lg>_pickem_legs_*`` (the whole
    qualifying board, phone-readable in the app) and ``slate_<lg>_pickem_*``
    (the ranked slips over the EV floor — empty is a result, not a failure).
    """
    if args.pickem_top <= 0:
        return
    try:
        from velocity.models.props_football import name_index_from_fp
        from velocity.wagering.pickem_slate import build_pickem_board

        fp = pd.read_parquet(args.fp_projections)
        if "league" in fp.columns:
            fp = fp[fp["league"].astype(str) == args.league]
        legs, slips = build_pickem_board(
            props_by_game, prop_lines, name_index_from_fp(fp),
            min_ev=args.pickem_min_ev, top=args.pickem_top,
            devig_method=args.pickem_devig,
            max_per_game=args.pickem_max_per_game or None,
        )
        print(f"\n=== {args.league.upper()} pick'em — {len(legs)} qualifying legs, "
              f"{len(slips)} slips over EV {args.pickem_min_ev:g} ===")
        if not slips.empty:
            with pd.option_context("display.width", 200, "display.max_columns", None):
                print(slips.to_string(index=False))
        if args.out:
            out_dir = Path(args.out)
            out_dir.mkdir(parents=True, exist_ok=True)
            stamp = now.strftime("%Y%m%dT%H%M%SZ")
            legs.assign(league=args.league, generated_at=generated_at).to_parquet(
                out_dir / f"slate_{args.league}_pickem_legs_{stamp}.parquet", index=False
            )
            slips.assign(league=args.league, generated_at=generated_at).to_parquet(
                out_dir / f"slate_{args.league}_pickem_{stamp}.parquet", index=False
            )
            print(f"wrote {len(legs)} pick'em legs + {len(slips)} slips")
    except Exception as exc:  # noqa: BLE001 - pick'em never breaks the slates
        print(f"pick'em board skipped: {exc}")


def _prop_config(args: argparse.Namespace):  # type: ignore[no-untyped-def]
    from velocity.models.props_football import FootballPropConfig

    return FootballPropConfig(n_sims=args.n_sims)


def _write_social_cards(  # noqa: PLR0913 - a report writer with several inputs
    args: argparse.Namespace,
    events: pd.DataFrame,
    projections: dict,
    canonical: pd.DataFrame,
    props_by_game: dict,
    key_to_name: dict[str, str],
    prop_lines: pd.DataFrame | None,
    stamp: str,
    *,
    game_log: object = None,
    convictions: list | None = None,
) -> None:
    """Render the per-game social model cards + captions into the out folder.

    The season record chain (written by the grading step, which runs before the
    slate build) puts the receipt line on every card. Logos are fetched into a
    hidden cache under the out folder (never uploaded). Best-effort: a card
    problem never breaks the slate.
    """
    try:
        from velocity.report.daily_record import season_record_line
        from velocity.report.social import build_social_cards
        from velocity.report.social_png import render_cards

        record_line = None
        try:
            chain = sorted(Path(args.out).glob(f"cumulative_record_{args.league}_*.parquet"))
            if chain:
                record_line = season_record_line(pd.read_parquet(chain[-1]))
        except Exception as exc:  # noqa: BLE001 - the record line is optional decoration
            print(f"record line skipped: {exc}")
        asset_dir = Path(args.out) / ".assets"  # hidden: outside the upload globs
        aliases = None
        team_colors = None
        code_to_team: dict[str, str] = {}
        if args.league == "ncaaf":
            aliases, team_colors, code_to_team = _ncaaf_identity(events, asset_dir)
        elif args.league in ("mlb", "wnba", "ncaab"):
            # Non-NFL identity: abbreviation + brand color blocks, no marks —
            # the NCAAF licensing posture (velocity/report/league_identity).
            # NCAAB has no curated table yet, so teams fall back to neutral
            # trigram codes — cards render, just uncolored.
            from velocity.report.league_identity import league_identity

            provider_names = sorted(
                set(events["home_team"].astype(str))
                | set(events["away_team"].astype(str))
            )
            aliases, team_colors, code_to_team = league_identity(
                args.league, provider_names
            )
        # max_watch=6: the hero card renders its top three; the deep dive
        # carries the full six.
        cards = build_social_cards(
            projections, events,
            props_by_game=props_by_game, key_to_name=key_to_name,
            prop_lines=prop_lines, record_line=record_line, lines=canonical,
            aliases=aliases, team_colors=team_colors, max_watch=6,
        )
        paths = render_cards(cards, Path(args.out), stamp,
                             asset_dir=asset_dir, league=args.league,
                             number_slides=args.carousel)
        print(f"wrote {len(paths)} social card(s) to {args.out}")
        # game_id → path maps (render_cards returns paths index-aligned with
        # cards) — the sheet composer joins the deep dives onto these below.
        card_by_game = {str(card.game_id): path
                        for card, path in zip(cards, paths, strict=True)}
        dive_by_game: dict[str, Path] = {}

        # Deep Dive companions — the analytical page behind each matchup card
        # (form/EPA table, margin vs the market, extended props). Best-effort
        # like everything else on this surface.
        try:
            from velocity.ingest.local import load_games, load_plays
            from velocity.report.deepdive import build_deep_dives
            from velocity.report.deepdive_png import render_deep_dives

            games = load_games(_find_games(Path(args.data)), league=args.league)
            plays_path = _find_plays(Path(args.data)) if args.league == "nfl" else None
            plays = load_plays(plays_path) if plays_path is not None else None
            starters = probables = None
            if args.league == "mlb":
                # The reference-genre pitcher row: each probable's banked
                # line. Best-effort — a fetch failure just omits the row.
                try:
                    from datetime import date, timedelta

                    from build_mlb_pitching import fetch_probables

                    sp_file = Path(args.data) / "starters.parquet"
                    if sp_file.exists():
                        starters = pd.read_parquet(sp_file)
                        today = date.today()
                        probables = fetch_probables(
                            str(today), str(today + timedelta(days=1))
                        )
                except Exception as exc:  # noqa: BLE001 - cosmetic row only
                    print(f"probable-pitcher row skipped: {exc}")
            # The verdict band: the slate's staked plays (tier-chipped when
            # the intel layer ran) and the intel evidence lines for the WHY
            # snippet — one per game, its highest-conviction unvetoed bet.
            from velocity.report.deepdive import plays_from_bets

            tiers: dict[tuple[str, str, str], str] = {}
            why_signals: dict[str, list[str]] = {}
            if convictions:
                best: dict[str, float] = {}
                for c in convictions:
                    bet = c.bet
                    if bet.player is not None:
                        continue
                    gid = str(bet.game_id)
                    tiers[(gid, bet.market, bet.side)] = c.tier
                    if not c.vetoed and c.score > best.get(gid, -1.0):
                        best[gid] = c.score
                        why_signals[gid] = [s.rationale for s in c.signals[:2]]
            plays_by_game = (
                plays_from_bets(game_log.bets, tiers=tiers)  # type: ignore[attr-defined]
                if game_log is not None else {}
            )
            dives = build_deep_dives(cards, projections, games, plays,
                                     plays_by_game=plays_by_game,
                                     why_signals=why_signals,
                                     team_names=code_to_team,
                                     starters=starters, probables=probables)
            dive_paths = render_deep_dives(dives, Path(args.out), stamp,
                                           asset_dir=asset_dir, league=args.league)
            print(f"wrote {len(dive_paths)} deep dive card(s) to {args.out}")
            dive_by_game = {str(dive.card.game_id): path
                            for dive, path in zip(dives, dive_paths, strict=True)}
        except Exception as exc:  # noqa: BLE001 - the companion never blocks the card run
            print(f"deep dives skipped: {exc}")

        # The sheet: ONE all-inclusive graphic per game (card + deep dive
        # stacked), the artifact's only pregame PNG — the intermediate
        # renders and the deep-dive captions are consumed into it.
        from velocity.report.sheet_png import compose_sheets

        sheets = compose_sheets(card_by_game, dive_by_game, Path(args.out))
        social_captions = Path(args.out) / f"social_{args.league}_{stamp}_captions.md"
        if social_captions.exists():
            social_captions.rename(
                Path(args.out) / f"sheet_{args.league}_{stamp}_captions.md")
        for stale in (*card_by_game.values(), *dive_by_game.values()):
            stale.unlink(missing_ok=True)
        dive_captions = Path(args.out) / f"deepdive_{args.league}_{stamp}_captions.md"
        dive_captions.unlink(missing_ok=True)
        print(f"composed {len(sheets)} sheet(s)")
        manifest = [{"game_id": gid, "kind": "sheet", "file": path.name}
                    for gid, path in sheets.items()]
        if manifest:
            pd.DataFrame(manifest).assign(league=args.league).to_parquet(
                Path(args.out) / f"cardindex_{args.league}_{stamp}.parquet",
                index=False,
            )
    except Exception as exc:  # noqa: BLE001 - a report surface, never breaks the slate
        print(f"social cards skipped: {exc}")


def _ncaaf_identity(
    events: pd.DataFrame, asset_dir: Path
) -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    """Provider-name → school-abbreviation aliases, abbreviation → color map,
    and abbreviation → school (the datasets' team key, for the deep dive).

    Built from the cached CFBD identity table (``CFBD_API_KEY``); provider
    names bridge to schools via the same nickname-prefix logic the slate
    resolution uses. Any name the table can't place falls back to itself —
    the card renders the provider name at a smaller size rather than guessing.
    No logos anywhere on this path (school marks are licensed; abbreviation +
    colors are plain facts).
    """
    from velocity.report.assets import ncaaf_team_index
    from velocity.wagering.live import nickname_aliases

    provider_names = {
        str(n) for n in
        pd.concat([events["away_team"], events["home_team"]]).dropna()
    }
    aliases = {name: name for name in provider_names}
    team_colors: dict[str, str] = {}
    code_to_team: dict[str, str] = {}
    meta = ncaaf_team_index(os.environ.get("CFBD_API_KEY"), asset_dir)
    if meta:
        to_school = nickname_aliases(sorted(provider_names), sorted(meta))
        for provider, school in to_school.items():
            aliases[provider] = meta[school].abbreviation
        team_colors = {
            m.abbreviation: m.color for m in meta.values() if m.color
        }
        code_to_team = {m.abbreviation: school for school, m in meta.items()}
    return aliases, team_colors, code_to_team


def _parlay_slate(
    args: argparse.Namespace,
    projections: dict,
    game_log: object | None,
    now: datetime,
    generated_at: pd.Timestamp,
) -> None:
    """Build, print, and persist the parlay slate from the day's qualifying bets."""
    try:
        from velocity.wagering.parlay import (
            ParlayConfig,
            build_parlays,
            parlay_slate_to_frame,
        )

        candidates = list(game_log or [])
        if not candidates:
            return
        results_by_game = {str(gid): proj.sim for gid, proj in projections.items()}
        game_labels = {
            str(gid): f"{proj.away_team}@{proj.home_team}"
            for gid, proj in projections.items()
        }
        tickets = build_parlays(
            candidates,
            results_by_game,
            bankroll=args.bankroll,
            game_labels=game_labels,
            config=ParlayConfig(
                max_legs=max(args.parlay_max_legs, 2),
                min_ev=args.parlay_min_ev,
                max_parlays=args.max_parlays,
            ),
        )
        frame = parlay_slate_to_frame(tickets)
        print(f"\n=== {args.league.upper()} parlays — {len(candidates)} candidate legs, "
              f"{len(frame)} recommended ===")
        if frame.empty:
            print("no parlay cleared the combined-EV bar.")
        else:
            with pd.option_context("display.width", 200, "display.max_columns", None):
                print(frame.to_string(index=False))
            print("note: same_game=True payouts assume the product price; books "
                  "reprice correlated SGPs, so treat that EV as an upper bound.")
        if args.out and not frame.empty:
            dest = (
                Path(args.out)
                / f"slate_{args.league}_parlays_{now.strftime('%Y%m%dT%H%M%SZ')}.parquet"
            )
            frame.assign(league=args.league, generated_at=generated_at).to_parquet(
                dest, index=False
            )
            print(f"wrote {len(frame)} parlay rows to {dest}")
    except Exception as exc:  # noqa: BLE001 - parlays are additive; never break the slate
        print(f"parlay slate skipped: {exc}")


if __name__ == "__main__":
    main()
