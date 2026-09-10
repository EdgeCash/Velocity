"""Assemble the Evidence site's data dir from a slate-artifact folder.

The site (``site/``, docs/DASHBOARD_RESEARCH.md §6) is a static Evidence
build over parquet. This script is the seam between the pipeline's
stamped artifact families and the site's stable table names: it finds the
**latest stamp per league** for each frame kind, joins what the pages
need pre-joined, and writes one parquet per table into
``site/sources/velocity/data/``.

    python scripts/build_site_data.py --slate-dir artifacts/slate

Everything is best-effort per league: a league with no artifacts simply
contributes no rows. The output dir is gitignored — slate frames are
paid-odds-derived and never enter git; the built site deploys only to the
Access-gated private host (public split is a later phase).
"""

from __future__ import annotations

import argparse
import re
import shutil
from pathlib import Path

import pandas as pd

LEAGUES = ("nfl", "ncaaf", "mlb", "wnba", "ncaab", "nhl")
_STAMP = r"(\d{8}T\d{6}Z)"

# Evidence's source runner writes no parquet at all for a query that returns
# zero rows, and the build then dies reading the missing extraction ("too
# small to be a Parquet file"). So an absent family ships exactly one
# sentinel row, and every page query filters `league != '__none__'`.
SENTINEL_LEAGUE = "__none__"

# The "what's live" transparency block, mirrored from the plays app.
from sys import path as _sys_path  # noqa: E402

_sys_path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))


def newest(folder: Path, pattern: str) -> Path | None:
    """Lexicographically-last match — the stamp format sorts chronologically."""
    matches = sorted(p for p in folder.rglob("*") if re.fullmatch(pattern, p.name))
    return matches[-1] if matches else None


def latest_frame(folder: Path, prefix: str, league: str) -> pd.DataFrame | None:
    """The newest ``{prefix}_{stamp}.parquet`` under ``folder``, or None.

    ``{league}`` in the prefix is substituted, so ``slate_{league}_props``
    finds the props family and ``record_{league}`` the record family.
    """
    stem = prefix.format(league=league)
    path = newest(folder, rf"{re.escape(stem)}_{_STAMP}\.parquet")
    if path is None:
        return None
    frame = pd.read_parquet(path)
    stamp = re.search(_STAMP, path.name)
    frame["league"] = league
    frame["stamp"] = stamp.group(1) if stamp else ""
    return frame


def collect(folder: Path, kind: str) -> pd.DataFrame:
    """Latest frame per league for ``kind``, concatenated (empty if none).

    ``kind`` is either a bare family (``record`` → ``record_{league}``) or a
    prefix template containing ``{league}``.
    """
    prefix = kind if "{league}" in kind else kind + "_{league}"
    frames = [f for lg in LEAGUES if (f := latest_frame(folder, prefix, lg)) is not None]
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def build_board(slate_dir: Path) -> pd.DataFrame:
    """The Today board: slate rows joined with games and projections."""
    slate = collect(slate_dir, "slate")
    games = collect(slate_dir, "games")
    projections = collect(slate_dir, "projections")
    intel = collect(slate_dir, "intel")
    if slate.empty or games.empty:
        return pd.DataFrame()
    board = slate.merge(
        games[["game_id", "home_team", "away_team", "kickoff"]].drop_duplicates("game_id"),
        on="game_id", how="left",
    )
    if not projections.empty:
        board = board.merge(
            projections[["game_id", "p_home_win", "mu_home", "mu_away",
                         "fair_spread", "fair_total"]].drop_duplicates("game_id"),
            on="game_id", how="left",
        )
    keys = ["game_id", "market", "side"]
    if not intel.empty and "tier" in intel.columns:
        cols = [*keys, "tier", "conviction"]
        if "rationale" in intel.columns:
            cols.append("rationale")
        game_intel = intel[intel["player"].isna()] if "player" in intel.columns else intel
        tiers = game_intel.drop_duplicates(subset=keys)[cols]
        board = board.merge(tiers, on=keys, how="left")
    # The number to bet is the portfolio-sized stake, not the solo-Kelly one
    # the slate parquet keeps for backtest comparability (docs/WAGERING.md
    # §6). Sized stakes join by bet identity; a row the portfolio never saw
    # (paper, or a run before sizing) shows zero.
    portfolio = collect(slate_dir, "portfolio")
    if not portfolio.empty and "stake" in portfolio.columns:
        game_rows = portfolio[portfolio["kind"] == "game"] if "kind" in portfolio.columns \
            else portfolio
        sized = (game_rows.drop_duplicates(subset=keys)[[*keys, "stake"]]
                 .rename(columns={"stake": "stake_sized"}))
        board = board.merge(sized, on=keys, how="left")
    else:
        board["stake_sized"] = float("nan")
    board["stake_sized"] = board["stake_sized"].fillna(0.0)
    from velocity.store.schema import LADDER_BOOKS

    board["venue"] = board["book"].astype(str).str.lower().map(
        lambda b: b if b in LADDER_BOOKS else "sportsbook")
    if "note" not in board.columns:
        board["note"] = None
    return board


def build_publish(slate_dir: Path) -> pd.DataFrame:
    """The publish gate's verdicts, joined with matchup names.

    Every candidate the gate judged that run — the published few AND every
    held-back row with the exact rule that stopped it — so the plays page
    leads with the calls and shows the discipline behind them instead of
    the full raw board.
    """
    audit = collect(slate_dir, "publish")
    games = collect(slate_dir, "games")
    if audit.empty:
        return audit
    if not games.empty:
        audit = audit.merge(
            games[["game_id", "home_team", "away_team", "kickoff"]]
            .drop_duplicates("game_id"),
            on="game_id", how="left",
        )
    portfolio = collect(slate_dir, "portfolio")
    if not portfolio.empty and "stake" in portfolio.columns:
        keys = ["game_id", "market", "side"]
        sized = (portfolio.drop_duplicates(subset=keys)[[*keys, "stake"]]
                 .rename(columns={"stake": "stake_sized"}))
        audit = audit.merge(sized, on=keys, how="left")
    else:
        audit["stake_sized"] = float("nan")
    audit["stake_sized"] = audit["stake_sized"].fillna(0.0)
    return audit


def build_clv_by_market(record: pd.DataFrame) -> pd.DataFrame:
    """Per-league, per-market CLV with the trust flag (``eval.metrics``).

    The one number the doctrine says to read per market: spreads, totals
    and moneylines close efficiently enough that beating the close is
    skill; props and team totals do not, and their rows carry
    ``clv_trusted = False`` so the page says "judge on P/L" instead of
    averaging them into a headline (docs/WAGERING.md §6).
    """
    from velocity.eval.metrics import clv_by_market

    if record.empty or "market" not in record.columns:
        return pd.DataFrame()
    settled = record[record["result"].isin(["win", "loss", "push"])]
    frames = []
    for league, part in settled.groupby("league", sort=True):
        table = clv_by_market(part)
        if table.empty:
            continue
        table["league"] = str(league)
        table["units"] = [
            float(pd.to_numeric(part.loc[part["market"] == m, "profit"],
                                errors="coerce").fillna(0.0).sum())
            for m in table["market"]
        ]
        frames.append(table)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def build_bankroll(ledger_path: Path | None) -> dict[str, pd.DataFrame]:
    """The ledger's three site tables: the bankroll now, its curve, the open bets.

    ``bankroll`` is one row — seed, current, peak, drawdown, open exposure,
    whether the kill-switch would halt the next card (30% from the peak,
    the constitutional threshold) and how the bets got on the books
    (``mode``: auto-booked at the recommended terms, or placed by hand).
    Every table is empty without a ledger; the pages then say so.
    """
    empty = {"bankroll": pd.DataFrame(), "bankroll_curve": pd.DataFrame(),
             "ledger_open": pd.DataFrame()}
    if ledger_path is None or not Path(ledger_path).exists():
        return empty
    from velocity.wagering.ledger import PLACED, Ledger
    from velocity.wagering.portfolio import PortfolioConfig, should_halt

    ledger = Ledger.load(ledger_path)
    if not ledger.seeded:
        return empty
    state = ledger.state()
    threshold = PortfolioConfig().max_drawdown_fraction
    placed = ledger.frame[ledger.frame["record_type"] == PLACED]
    notes = placed["note"].astype(str)
    mode = ("auto" if len(placed) and notes.str.startswith("auto").all()
            else "manual" if len(placed) else "none")
    settled = ledger.pnl(("league",))
    bankroll = pd.DataFrame([{
        "seed": state.seed, "current": state.current, "peak": state.peak,
        "drawdown": state.drawdown, "open_exposure": state.open_exposure,
        "open_bets": int(state.n_open), "settled_bets": int(state.n_settled),
        "halted": bool(should_halt(state.current, state.peak, threshold)),
        "halt_threshold": threshold, "mode": mode,
        "staked": float(settled["staked"].sum()) if not settled.empty else 0.0,
        "profit": float(settled["profit"].sum()) if not settled.empty else 0.0,
        "as_of": (None if state.last_settled_at is None
                  else pd.Timestamp(state.last_settled_at)),
        "league": "all",
    }])
    # Seed and adjustment rows belong to no league; "all" keeps them on the
    # curve through the pages' sentinel filter.
    curve = ledger.curve()
    if not curve.empty:
        curve["league"] = curve["league"].where(curve["league"].notna(), "all")
    open_ = ledger.open_bets()
    if not open_.empty:
        open_ = open_.drop(columns=["settled"])
    return {"bankroll": bankroll, "bankroll_curve": curve, "ledger_open": open_}


def build_exposure(portfolio: pd.DataFrame) -> pd.DataFrame:
    """One row per league: what the sized card puts at risk against its cap.

    ``bankroll``/``slate_cap`` ride on the card since the runner started
    writing them; an older card falls back to the runner's defaults (100
    units, 25%) — the same numbers it was sized against.
    """
    if portfolio.empty or "stake" not in portfolio.columns:
        return pd.DataFrame()
    rows = []
    for league, part in portfolio.groupby("league", sort=True):
        stake = pd.to_numeric(part["stake"], errors="coerce").fillna(0.0)
        solo = (pd.to_numeric(part["stake_solo"], errors="coerce").fillna(0.0)
                if "stake_solo" in part.columns else stake)
        bankroll = (float(pd.to_numeric(part["bankroll"], errors="coerce").dropna().iloc[0])
                    if "bankroll" in part.columns
                    and pd.to_numeric(part["bankroll"], errors="coerce").notna().any()
                    else 100.0)
        cap = (float(pd.to_numeric(part["slate_cap"], errors="coerce").dropna().iloc[0])
               if "slate_cap" in part.columns
               and pd.to_numeric(part["slate_cap"], errors="coerce").notna().any()
               else 0.25)
        games = part["game_id"].nunique() if "game_id" in part.columns else 0
        rows.append({
            "league": str(league),
            "bets": int((stake > 0).sum()),
            "games": int(games),
            "stake_sized": float(stake.sum()),
            "stake_solo": float(solo.sum()),
            "bankroll": bankroll,
            "cap_fraction": cap,
            "cap_units": bankroll * cap,
            "exposure": float(stake.sum()) / bankroll if bankroll else float("nan"),
            "stamp": str(part["stamp"].iloc[0]) if "stamp" in part.columns else "",
        })
    return pd.DataFrame(rows)


def build_units(record: pd.DataFrame) -> pd.DataFrame:
    """Per-day settled profit and running units per league, for the record chart."""
    if record.empty or "result" not in record.columns:
        return pd.DataFrame()
    settled = record[record["result"].isin(["win", "loss", "push"])].copy()
    if settled.empty:
        return pd.DataFrame()
    # Real graded frames carry profit as object dtype (pending rows mix None
    # in upstream) — coerce before any cython op.
    settled["profit"] = pd.to_numeric(settled["profit"], errors="coerce").fillna(0.0)
    # Sized profit where the chain carries it; a row graded before sizing
    # counts at its solo stake so the sized line stays continuous.
    sized = (pd.to_numeric(settled["profit_sized"], errors="coerce")
             if "profit_sized" in settled.columns else pd.Series(float("nan"), index=settled.index))
    settled["profit_sized"] = sized.fillna(settled["profit"])
    settled["slate_date"] = pd.to_datetime(settled["slate_date"]).dt.date
    daily = (settled.groupby(["league", "slate_date"], as_index=False)
             .agg(profit=("profit", "sum"), profit_sized=("profit_sized", "sum"),
                  bets=("profit", "size")))
    daily = daily.sort_values(["league", "slate_date"])
    daily["units"] = daily.groupby("league")["profit"].cumsum()
    daily["units_sized"] = daily.groupby("league")["profit_sized"].cumsum()
    return daily


# The rendered card families (velocity.report.*_png). Matchup-keyed kinds
# carry `_{AWAY}_at_{HOME}` in the filename; recordcard is one per league.
# `sheet` is the one all-inclusive pregame graphic (card + deep dive
# composed); social/deepdive stay listed so older artifacts still surface.
# `grid` is the weekend broadcast grid (one per league-day, no matchup key).
CARD_KINDS = ("grid", "sheet", "social", "deepdive", "simcheck", "recordcard")


def build_ratings(slate_dir: Path, prev_dir: Path | None) -> pd.DataFrame:
    """The power-ratings table, with movement vs the previous run's export.

    ``rank_prev``/``net_prev`` come from the newest older-stamped ratings
    frame per league found under ``prev_dir`` (the downloaded previous
    artifacts); first runs simply have no movement columns populated.
    """
    ratings = collect(slate_dir, "ratings")
    if ratings.empty:
        return ratings
    ratings["rank_prev"] = float("nan")
    ratings["net_prev"] = float("nan")
    if prev_dir is None or not prev_dir.exists():
        return ratings
    for league in ratings["league"].unique():
        stamp = ratings.loc[ratings["league"] == league, "stamp"].iloc[0]
        pattern = rf"ratings_{re.escape(league)}_{_STAMP}\.parquet"
        older = sorted(p for p in prev_dir.rglob("*.parquet")
                       if re.fullmatch(pattern, p.name) and p.name
                       < f"ratings_{league}_{stamp}.parquet")
        if not older:
            continue
        prev = pd.read_parquet(older[-1])
        rank_prev = dict(zip(prev["team"], prev["rank"].astype(float), strict=True))
        net_prev = dict(zip(prev["team"], prev["net"].astype(float), strict=True))
        mask = ratings["league"] == league
        teams = ratings.loc[mask, "team"]
        ratings.loc[mask, "rank_prev"] = [
            float(rank_prev.get(t, float("nan"))) for t in teams]
        ratings.loc[mask, "net_prev"] = [
            float(net_prev.get(t, float("nan"))) for t in teams]
    return ratings


def build_line_moves(slate_dir: Path, odds_dir: Path | None) -> pd.DataFrame:
    """Open → now consensus line per board key, from the hourly odds archive.

    For every (game_id, market, side) on the current board: the median
    point/price across books in the earliest archived snapshot that carries
    the game (``open``) and in the latest one (``now``). Empty when the
    archive is absent (local runs) or holds none of the board's games.
    """
    board_games = collect(slate_dir, "games")
    if board_games.empty or odds_dir is None or not odds_dir.exists():
        return pd.DataFrame()
    snapshots = sorted(odds_dir.rglob("odds_lines_*.parquet"))
    if not snapshots:
        return pd.DataFrame()
    from velocity.wagering.live import canonicalize_sides

    game_ids = set(board_games["game_id"].astype(str))
    frames = []
    for path in snapshots:
        snap = pd.read_parquet(path)
        snap = snap[snap["game_id"].astype(str).isin(game_ids)]
        if not snap.empty:
            frames.append(snap)
    if not frames:
        return pd.DataFrame()
    lines = canonicalize_sides(pd.concat(frames, ignore_index=True), board_games)
    if lines.empty:
        return pd.DataFrame()
    stamp_col = "collected_at" if "collected_at" in lines.columns else "timestamp"
    lines[stamp_col] = pd.to_datetime(lines[stamp_col])
    keys = ["game_id", "market", "side"]
    # Price consensus in decimal space — a plain median of American odds
    # straddling ±100 lands in the invalid gap.
    from velocity.wagering.odds import consensus_american

    per_snap = (lines.groupby([*keys, stamp_col], as_index=False)
                .agg(point=("point", "median"),
                     price=("price", consensus_american)))
    per_snap = per_snap.sort_values(stamp_col)
    opens = per_snap.groupby(keys, as_index=False).first()
    nows = per_snap.groupby(keys, as_index=False).last()
    moves = opens.merge(nows, on=keys, suffixes=("_open", "_now"))
    moves = moves.rename(columns={f"{stamp_col}_open": "seen_open",
                                  f"{stamp_col}_now": "seen_now"})
    moves["game_id"] = moves["game_id"].astype(str)
    league_by_game = dict(zip(board_games["game_id"].astype(str),
                              board_games["league"], strict=True))
    moves["league"] = moves["game_id"].map(league_by_game)
    return moves


def build_injuries(fp_dir: Path | None) -> pd.DataFrame:
    """The newest banked injuries snapshot (NFL — the one league FP serves)."""
    if fp_dir is None or not fp_dir.exists():
        return pd.DataFrame()
    path = newest(fp_dir, rf"fp_injuries_{_STAMP}\.parquet")
    if path is None:
        return pd.DataFrame()
    frame = pd.read_parquet(path)
    keep = ["player_name", "team", "position", "status", "is_out", "league"]
    frame = frame[[c for c in keep if c in frame.columns]].copy()
    if "league" not in frame.columns:
        frame["league"] = "nfl"
    return frame


def build_weather(slate_dir: Path) -> pd.DataFrame:
    """Kickoff-hour forecast for outdoor NFL/MLB games (Open-Meteo, free).

    Covered venues get a row with ``covered=True`` and no forecast numbers;
    unmapped teams and failed fetches contribute nothing. Entirely
    best-effort — offline runs return empty.
    """
    from velocity.report.venues import venue_for

    games = collect(slate_dir, "games")
    if games.empty:
        return pd.DataFrame()
    rows = []
    for rec in games.to_dict("records"):
        venue = venue_for(str(rec["league"]), str(rec["home_team"]))
        if venue is None:
            continue
        row = {"game_id": str(rec["game_id"]), "league": rec["league"],
               "covered": venue.covered, "temp_f": float("nan"),
               "wind_mph": float("nan"), "precip_pct": float("nan")}
        if not venue.covered:
            forecast = _kickoff_forecast(venue.lat, venue.lon, rec["kickoff"])
            if forecast is not None:
                row.update(forecast)
        rows.append(row)
    return pd.DataFrame(rows)


def _kickoff_forecast(lat: float, lon: float, kickoff: object) -> dict | None:
    """The Open-Meteo hourly values nearest the kickoff hour, or None."""
    import json
    from urllib.request import urlopen

    when = pd.to_datetime(kickoff, errors="coerce")
    if pd.isna(when):
        return None
    if when.tzinfo is not None:
        when = when.tz_localize(None)
    url = ("https://api.open-meteo.com/v1/forecast"
           f"?latitude={lat}&longitude={lon}"
           "&hourly=temperature_2m,wind_speed_10m,precipitation_probability"
           "&temperature_unit=fahrenheit&wind_speed_unit=mph"
           "&timezone=UTC&forecast_days=7")
    try:
        with urlopen(url, timeout=10) as resp:
            data = json.load(resp)
        hours = pd.to_datetime(data["hourly"]["time"])
        idx = int((hours - when).abs().argmin())
        if abs((hours[idx] - when).total_seconds()) > 6 * 3600:
            return None  # kickoff outside the forecast horizon
        return {
            "temp_f": float(data["hourly"]["temperature_2m"][idx]),
            "wind_mph": float(data["hourly"]["wind_speed_10m"][idx]),
            "precip_pct": float(data["hourly"]["precipitation_probability"][idx]),
        }
    except Exception:  # noqa: BLE001 - a weather nicety, never blocks the build
        return None


def _card_captions(folder: Path, stem: str) -> dict[str, str]:
    """``{kind}_{league}_{stamp}_captions.md`` parsed into AWAY @ HOME → text."""
    path = next(iter(folder.rglob(f"{stem}_captions.md")), None)
    if path is None:
        return {}
    captions: dict[str, str] = {}
    for block in path.read_text().split("\n---\n"):
        block = block.strip()
        head = block.split(" — ", 1)[0].strip()
        if head:
            captions[head] = block
    return captions


def collect_cards(slate_dir: Path, static_out: Path) -> pd.DataFrame:
    """Copy the newest-stamp card PNGs per (kind, league) into the site.

    The PNGs land in ``static_out`` (served at ``/cards/<name>``) and the
    returned manifest gives the Graphics page its gallery rows, with the
    social caption attached where the captions file carries the matchup.
    """
    static_out.mkdir(parents=True, exist_ok=True)
    for stale in static_out.glob("*.png"):
        stale.unlink()
    rows = []
    for kind in CARD_KINDS:
        for league in LEAGUES:
            pattern = rf"{kind}_{league}_{_STAMP}(_.+)?\.png"
            found = sorted(
                {p.name: p for p in slate_dir.rglob("*.png")
                 if re.fullmatch(pattern, p.name)}.values(),
                key=lambda p: p.name,
            )
            if not found:
                continue
            stamp_match = re.search(_STAMP, found[-1].name)
            stamp = stamp_match.group(1) if stamp_match else ""
            batch = [p for p in found if stamp in p.name]
            captions = _card_captions(slate_dir, f"{kind}_{league}_{stamp}")
            for path in batch:
                match = re.fullmatch(
                    rf"{kind}_{league}_{stamp}_(.+)_at_(.+)\.png", path.name)
                away, home = match.groups() if match else ("", "")
                shutil.copy2(path, static_out / path.name)
                rows.append({
                    "kind": kind, "league": league, "stamp": stamp,
                    "file": path.name, "away": away, "home": home,
                    "caption": captions.get(f"{away} @ {home}", ""),
                })
    cards = pd.DataFrame(rows)
    if cards.empty:
        return cards
    # game_id from the runner's card manifests (cardindex_* — every stamp,
    # since the grader and the slate build write separate manifests).
    indices = [pd.read_parquet(p) for p in slate_dir.rglob("*.parquet")
               if re.fullmatch(rf"cardindex_.+_{_STAMP}\.parquet", p.name)]
    if indices:
        by_file = {str(r["file"]): str(r["game_id"])
                   for frame in indices for r in frame.to_dict("records")}
        cards["game_id"] = cards["file"].map(by_file).fillna("")
    else:
        cards["game_id"] = ""
    return cards


def sentinel_frame(schema: dict[str, object]) -> pd.DataFrame:
    """One filterable placeholder row matching ``schema``.

    Dates get a real timestamp (Evidence downcasts all-null date columns to
    Float64, which would change the extracted column type); everything else
    is inert. The ``league`` column always exists and carries the marker.
    """
    def value(col: str, dtype: object) -> object:
        # The game id carries the marker too: the matchup template's crawl
        # seed (/matchup/__none__) then finds its one row and renders empty
        # states instead of an empty-dataset error.
        if col in ("league", "game_id"):
            return SENTINEL_LEAGUE
        if dtype is str:
            return ""
        if dtype is int:
            return 0
        if dtype is float:
            return float("nan")
        if dtype is bool:
            return False
        return pd.Timestamp("2000-01-01")

    row = {col: value(col, dtype) for col, dtype in schema.items()}
    return pd.DataFrame([row]).astype(schema)  # type: ignore[arg-type]


def model_config_frame(slate_dir: Path | None = None) -> pd.DataFrame:
    """The per-league "what's live" block.

    From each run's own ``config_{league}_{stamp}.parquet`` — the runner
    writes what it actually did (fit, anchoring, filters, ceilings, paper
    posture) so the page cannot drift from the code. The hand-kept table in
    the retired plays app is the fallback for artifacts that predate it.
    """
    if slate_dir is not None:
        live = collect(slate_dir, "config")
        if not live.empty:
            return live[["league", "label", "detail"]]
    try:
        from format_plays import MODEL_CONFIG  # type: ignore[import-not-found]
    except Exception:  # noqa: BLE001 - the site renders without the block
        return pd.DataFrame()
    rows = []
    for league, entries in MODEL_CONFIG.items():
        for label, detail in entries:
            rows.append({"league": league, "label": label, "detail": detail})
    return pd.DataFrame(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="Assemble the site's data dir")
    parser.add_argument("--slate-dir", default="artifacts/slate")
    parser.add_argument("--out", default="site/sources/velocity/data")
    parser.add_argument("--cards-out", default="site/static/cards")
    parser.add_argument("--prev-dir", default="artifacts/previous",
                        help="previous runs' artifacts (ratings movement)")
    parser.add_argument("--odds-dir", default="artifacts/odds",
                        help="hourly odds snapshots (line movement)")
    parser.add_argument("--fp-dir", default="artifacts/fp",
                        help="FantasyPros artifacts (injuries panel)")
    parser.add_argument("--no-weather", action="store_true",
                        help="skip the Open-Meteo forecast fetch")
    parser.add_argument("--ledger", default=None,
                        help="the bankroll ledger parquet (docs/WAGERING.md W1)")
    args = parser.parse_args()

    slate_dir = Path(args.slate_dir)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    tables: dict[str, pd.DataFrame] = {
        "board": build_board(slate_dir),
        "games": collect(slate_dir, "games"),
        "projections": collect(slate_dir, "projections"),
        "distributions": collect(slate_dir, "distributions"),
        "record": collect(slate_dir, "record"),
        "cumulative_record": collect(slate_dir, "cumulative_record"),
        "props": collect(slate_dir, "slate_{league}_props"),
        "dfs_lineup": collect(slate_dir, "dfs_lineup"),
        "dfs_showdown": collect(slate_dir, "dfs_showdown"),
        "dfs_tiered": collect(slate_dir, "dfs_tiered"),
        "dfs_gpp": collect(slate_dir, "dfs_gpp"),
        "portfolio": collect(slate_dir, "portfolio"),
        "publish": build_publish(slate_dir),
        "parlays": collect(slate_dir, "slate_{league}_parlays"),
        "model_config": model_config_frame(slate_dir),
        "exposure": build_exposure(collect(slate_dir, "portfolio")),
        "market_health": collect(slate_dir, "monitor"),
        "cards": collect_cards(slate_dir, Path(args.cards_out)),
        "ratings": build_ratings(slate_dir, Path(args.prev_dir)),
        "line_moves": build_line_moves(slate_dir, Path(args.odds_dir)),
        "injuries": build_injuries(Path(args.fp_dir)),
        "weather": (pd.DataFrame() if args.no_weather
                    else build_weather(slate_dir)),
    }
    season = (tables["cumulative_record"] if not tables["cumulative_record"].empty
              else tables["record"])
    tables["units"] = build_units(season)
    tables["clv_by_market"] = build_clv_by_market(season)
    tables.update(build_bankroll(None if args.ledger is None else Path(args.ledger)))

    # An absent family still writes a typed one-row sentinel frame so every
    # page's SQL parses AND every source query returns a row (see
    # SENTINEL_LEAGUE); the pages filter the sentinel and render empty states.
    schemas: dict[str, dict[str, object]] = {
        "board": {"game_id": str, "market": str, "side": str, "point": float,
                  "book": str, "price": float, "p_model": float, "p_fair": float,
                  "edge": float, "stake": float, "league": str, "stamp": str,
                  "home_team": str, "away_team": str,
                  "kickoff": "datetime64[ns]", "p_home_win": float,
                  "mu_home": float, "mu_away": float, "fair_spread": float,
                  "fair_total": float, "tier": str, "conviction": float,
                  "rationale": str, "stake_sized": float, "venue": str, "note": str},
        "parlays": {"legs": str, "n_legs": int, "price": float, "decimal": float,
                    "p_win": float, "ev": float, "same_game": bool, "stake": float,
                    "legs_json": str, "league": str, "stamp": str},
        "games": {"game_id": str, "home_team": str, "away_team": str,
                  "kickoff": "datetime64[ns]", "league": str, "stamp": str},
        "projections": {"game_id": str, "away": str, "home": str, "n_sims": int,
                        "mu_away": float, "mu_home": float, "p_home_win": float,
                        "fair_spread": float, "fair_total": float,
                        "league": str, "stamp": str},
        "distributions": {"game_id": str, "kind": str, "value": float,
                          "prob": float, "league": str, "stamp": str},
        "record": {"section": str, "play": str, "market": str, "side": str,
                   "point": float, "price": float, "stake": float,
                   "result": str, "profit": float, "price_clv": float,
                   "line_clv": float, "stake_sized": float,
                   "profit_sized": float, "close_source": str,
                   "p_model": float, "p_fair": float,
                   "slate_date": "datetime64[ns]", "league": str, "stamp": str},
        "cumulative_record": {"section": str, "play": str, "market": str,
                              "side": str, "point": float, "price": float,
                              "stake": float, "result": str, "profit": float,
                              "price_clv": float, "line_clv": float,
                              "stake_sized": float, "profit_sized": float,
                              "close_source": str, "p_model": float,
                              "p_fair": float,
                              "slate_date": "datetime64[ns]", "league": str,
                              "stamp": str},
        "market_health": {"market": str, "window_days": int,
                          "since": "datetime64[ns]", "n_bets": int,
                          "n_decided": int, "staked": float, "profit": float,
                          "roi": float, "roi_p": float, "clv_trusted": bool,
                          "n_clv": int, "mean_line_clv": float,
                          "mean_price_clv": float, "pct_beat_close": float,
                          "clv_p": float, "claimed": float, "realized": float,
                          "drift": float, "flag_negative_clv": bool,
                          "flag_negative_roi": bool, "flag_overclaims": bool,
                          "flag_exclusion": bool, "thin": bool, "flags": str,
                          "as_of": "datetime64[ns]", "league": str, "stamp": str},
        "clv_by_market": {"market": str, "n_bets": int, "mean_price_clv": float,
                          "mean_line_clv": float, "pct_beat_close": float,
                          "clv_trusted": bool, "league": str, "units": float},
        "exposure": {"league": str, "bets": int, "games": int,
                     "stake_sized": float, "stake_solo": float,
                     "bankroll": float, "cap_fraction": float,
                     "cap_units": float, "exposure": float, "stamp": str},
        # `book` and `note` are written by `prop_slate_to_frame` but were
        # missing here, so the column set differed between a day with props
        # and a day without: the sentinel had twelve columns, a real frame
        # fourteen. A page selecting `book` then rendered fine all season
        # and died with a red box on the first quiet slate. The schema is a
        # floor, not a filter — anything the producer writes has to be
        # listed here or the empty case silently drops it.
        "props": {"game_id": str, "player": str, "market": str, "side": str,
                  "point": float, "book": str, "price": float,
                  "p_model": float, "p_fair": float, "edge": float,
                  "stake": float, "note": str,
                  "league": str, "stamp": str},
        "dfs_lineup": {"slot": str, "player_name": str, "position": str,
                       "kickoff": "datetime64[ns]", "game_time": str,
                       "slate_start": "datetime64[ns]",
                       "suffix": str, "slate": str,
                       "team": str, "salary": float, "points": float,
                       "league": str, "stamp": str},
        "dfs_showdown": {"slot": str, "player_name": str, "position": str,
                         "kickoff": "datetime64[ns]", "game_time": str,
                         "slate_start": "datetime64[ns]",
                         "suffix": str, "slate": str, "format": str,
                         "team": str, "salary": float, "points": float,
                         "league": str, "stamp": str},
        "dfs_tiered": {"slot": str, "player_name": str, "position": str,
                       "kickoff": "datetime64[ns]", "game_time": str,
                       "tier": float, "team": str, "points": float,
                       "format": str, "game_type": str, "unit": str,
                       "league": str, "stamp": str},
        "dfs_gpp": {"rank": int, "players": str, "total_salary": float,
                    "total_points": float, "score": float, "stacks": str,
                    "league": str, "stamp": str},
        "portfolio": {"game_id": str, "market": str, "side": str, "kind": str,
                      "price": float, "edge": float, "stake": float,
                      "stake_solo": float, "bankroll": float, "slate_cap": float,
                      "league": str, "stamp": str},
        "publish": {"game_id": str, "market": str, "side": str, "player": str,
                    "price": float, "stake": float, "edge": float, "tier": str,
                    "drift": float, "conviction": float, "context": float,
                    "published": bool, "reason": str, "league": str,
                    "stamp": str, "home_team": str, "away_team": str,
                    "kickoff": "datetime64[ns]", "stake_sized": float},
        "model_config": {"league": str, "label": str, "detail": str},
        "cards": {"kind": str, "league": str, "stamp": str, "file": str,
                  "away": str, "home": str, "caption": str, "game_id": str},
        "ratings": {"team": str, "off": float, "def": float, "net": float,
                    "pace": float, "scale": str, "rank": int,
                    "rank_prev": float, "net_prev": float,
                    "league": str, "stamp": str},
        "line_moves": {"game_id": str, "market": str, "side": str,
                       "point_open": float, "price_open": float,
                       "point_now": float, "price_now": float,
                       "seen_open": "datetime64[ns]",
                       "seen_now": "datetime64[ns]", "league": str},
        "injuries": {"player_name": str, "team": str, "position": str,
                     "status": str, "is_out": bool, "league": str},
        "weather": {"game_id": str, "league": str, "covered": bool,
                    "temp_f": float, "wind_mph": float, "precip_pct": float},
        "units": {"league": str, "slate_date": "datetime64[ns]",
                  "profit": float, "profit_sized": float, "bets": int,
                  "units": float, "units_sized": float},
        "bankroll": {"seed": float, "current": float, "peak": float,
                     "drawdown": float, "open_exposure": float, "open_bets": int,
                     "settled_bets": int, "halted": bool, "halt_threshold": float,
                     "mode": str, "staked": float, "profit": float,
                     "as_of": "datetime64[ns]", "league": str},
        "bankroll_curve": {"recorded_at": "datetime64[ns]", "record_type": str,
                           "league": str, "bet_id": str, "market": str,
                           "result": str, "amount": float, "bankroll": float},
        "ledger_open": {"bet_id": str, "league": str, "kind": str, "game_id": str,
                        "market": str, "side": str, "player": str, "point": float,
                        "book": str, "price": float, "stake": float,
                        "placed_at": "datetime64[ns]"},
    }
    for name, frame in tables.items():
        if frame.empty:
            frame = sentinel_frame(schemas[name])
        else:
            # A family that predates a column (record chains before sized
            # stakes, cards before the bankroll rode along) still needs it
            # for the page SQL to parse.
            for column, kind in schemas[name].items():
                if column not in frame.columns:
                    frame[column] = pd.Series(
                        [None] * len(frame), dtype=(
                            "float64" if kind is float
                            else "datetime64[ns]" if kind == "datetime64[ns]"
                            else "object"),
                    )
        frame.to_parquet(out / f"{name}.parquet", index=False)
        print(f"{name}: {len(frame)} rows")


if __name__ == "__main__":
    main()
