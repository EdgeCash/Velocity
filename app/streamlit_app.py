"""MatchUp Labs app — the day's slate as a dark, readable board.

One global **league switcher** (sidebar) drives every view, defaulting to
whichever league's slate is freshest — August opens on MLB, October on NFL,
no hand-kept calendar. A metrics strip orients first (games today, plays,
yesterday, season units); four tabs carry the detail:

* **Board** — the day's picks across EVERY league, grouped under section
  headers (the reference-site board), then the selected league's matchup
  panels: win-probability split bar, kickoff strip, projected score, fair
  total/line, and that game's plays.
* **Pick'em** — the slip-EV board: ranked slips (book-fair marginals ×
  model correlation) and the qualifying legs behind them.
* **Cards** — the day's graphics grouped in collapsible sections, each
  viewable and downloadable with the caption copy alongside. This is the
  posting workflow on a phone.
* **Performance** — yesterday's graded plays, the season-to-date record by
  section, and the transparency block: exactly what model is live for the
  league (curated from docs/MODEL_LAB.md at promotion time).

Data comes from the newest ``slate-*`` GitHub Actions artifact (the runner's
parquets), fetched with a token from Streamlit secrets — the paid-odds data
itself never lives in the public repo. For local use, point
``VELOCITY_SLATE_DIR`` at a runner ``--out`` folder instead; no token needed.

    streamlit run app/streamlit_app.py

Deploy free on Streamlit Community Cloud; see docs/LAUNCH.md ("The plays app").
"""

from __future__ import annotations

import io
import os
import sys
import tempfile
import zipfile
from pathlib import Path

import pandas as pd
import requests
import streamlit as st

# `streamlit run` puts the script dir on sys.path; test harnesses don't.
sys.path.insert(0, str(Path(__file__).parent))
from format_plays import (  # noqa: E402
    LEAGUES,
    MODEL_CONFIG,
    PICKEM_BREAKEVENS,
    card_images,
    default_league,
    league_freshness,
    load_slate_frames,
    matchup_cards,
    plays_table,
    season_summary,
    stamp_time,
)

st.set_page_config(
    page_title="MatchUp Labs",
    page_icon="🏈",
    layout="centered",
    initial_sidebar_state="expanded",
)

_API = "https://api.github.com"
_ACCENT = "#3ddad0"
_LEAGUE_LABELS = {"nfl": "NFL", "ncaaf": "NCAAF", "mlb": "MLB", "wnba": "WNBA"}
_CSS = """
<style>
.stApp { background: #0a0e13; }
[data-testid="stSidebar"] { background: #0d1218; border-right: 1px solid #1d2430; }
h1, h2, h3, h4 { color: #f2f5f7 !important; letter-spacing: 0.04em; }
[data-testid="stMetric"] { background: #10151c; border: 1px solid #1d2430;
                           border-radius: 10px; padding: 10px 12px; }
[data-testid="stMetricLabel"] { color: #7d8894 !important; }
[data-testid="stMetricValue"] { color: #f2f5f7 !important; font-size: 22px !important; }
.v-table { width: 100%; border-collapse: collapse; background: #10151c;
           border-radius: 10px; overflow: hidden; }
.v-table th { color: #7d8894; font-size: 12px; letter-spacing: 0.12em;
              text-transform: uppercase; text-align: left; padding: 10px 14px;
              border-bottom: 1px solid #1d2430; }
.v-table td { padding: 13px 14px; border-bottom: 1px solid #1d2430;
              font-size: 16px; color: #e8edf2; }
.v-table tr:hover td { background: #141b24; }
.v-league { text-align: center !important; color: #3ddad0 !important;
            font-size: 12px; letter-spacing: 0.25em; font-weight: 700; }
.v-play { color: #3ddad0; font-weight: 600; }
.v-dim { color: #7d8894; font-size: 13px; }
.v-card { background: #10151c; border: 1px solid #1d2430; border-radius: 12px;
          padding: 14px 16px; margin-bottom: 14px; }
.v-card-head { display: flex; justify-content: space-between; align-items: baseline; }
.v-team { color: #f2f5f7; font-size: 19px; font-weight: 700; }
.v-strip { color: #7d8894; font-size: 12px; letter-spacing: 0.08em;
           text-transform: uppercase; margin: 8px 0 2px; }
.v-nums { display: flex; gap: 22px; flex-wrap: wrap; margin: 8px 0; }
.v-num-label { color: #7d8894; font-size: 11px; letter-spacing: 0.12em;
               text-transform: uppercase; }
.v-num { color: #f2f5f7; font-size: 17px; font-weight: 700; }
.v-win { color: #fbbf24; }
.v-chip { display: inline-block; background: #0d2f2c; color: #3ddad0;
          border: 1px solid #1e5a54; border-radius: 6px; padding: 4px 10px;
          margin: 3px 6px 3px 0; font-size: 14px; font-weight: 600; }
.v-win-row { color: #22c55e; } .v-loss-row { color: #ef4444; }
.v-push-row, .v-pending-row { color: #7d8894; }
.bs-good { color: #22c55e !important; } .bs-mid { color: #fbbf24 !important; }
.bs-bad { color: #ef4444 !important; }
.bs-strip { background: #14808c; color: #eafcfb; border-radius: 6px;
            padding: 6px 12px; font-size: 11.5px; letter-spacing: 0.08em;
            text-transform: uppercase; display: flex; gap: 18px;
            flex-wrap: wrap; margin: 10px 0 6px; }
.bs-bar { display: flex; height: 12px; border-radius: 6px; overflow: hidden;
          margin: 10px 0 0; }
.bs-bar-away { background: #2a6f68; } .bs-bar-home { background: #3ddad0; }
.bs-grid { display: grid; gap: 14px;
           grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); }
.bs-card { background: #10151c; border: 1px solid #1d2430;
           border-radius: 12px; padding: 14px 16px; }
.bs-badge { display: inline-flex; align-items: center; justify-content: center;
            background: #0d2f2c; color: #4ade80; border: 1.5px solid #16a34a;
            border-radius: 10px; font-size: 22px; font-weight: 800;
            padding: 5px 12px; min-width: 54px; }
.bs-badge-dim { border-color: #1d2430; color: #e8edf2; background: #141b24; }
.bs-label { color: #7d8894; font-size: 10px; letter-spacing: 0.14em;
            text-transform: uppercase; }
.bs-val { color: #e8edf2; font-size: 15px; font-weight: 700; }
.bs-cardrow { display: flex; gap: 18px; flex-wrap: wrap; margin-top: 10px; }
</style>
"""


def _secret(name: str) -> str:
    try:
        return str(st.secrets.get(name, ""))
    except (FileNotFoundError, KeyError):
        return ""


@st.cache_data(ttl=600, show_spinner="fetching the latest slate…")
def fetch_artifact_dir(repo: str, token: str) -> str | None:
    """Download the newest slate artifact from recent workflow runs → local dir."""
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
    runs = requests.get(
        f"{_API}/repos/{repo}/actions/workflows/live-slate.yml/runs",
        params={"status": "success", "per_page": 10},
        headers=headers,
        timeout=30,
    )
    runs.raise_for_status()
    for run in runs.json().get("workflow_runs", []):
        arts = requests.get(run["artifacts_url"], headers=headers, timeout=30)
        arts.raise_for_status()
        for art in arts.json().get("artifacts", []):
            if not str(art.get("name", "")).startswith("slate-") or art.get("expired"):
                continue
            payload = requests.get(
                art["archive_download_url"], headers=headers, timeout=120
            )
            payload.raise_for_status()
            dest = Path(tempfile.mkdtemp(prefix="velocity-slate-"))
            with zipfile.ZipFile(io.BytesIO(payload.content)) as zf:
                zf.extractall(dest)
            return str(dest)
    return None


def _slate_dir() -> Path | None:
    local = os.environ.get("VELOCITY_SLATE_DIR", "")
    if local:
        return Path(local)
    token = _secret("GITHUB_TOKEN")
    repo = _secret("GITHUB_REPO") or "EdgeCash/Velocity"
    if not token:
        return None
    fetched = fetch_artifact_dir(repo, token)
    return Path(fetched) if fetched else None


def _fmt_pct(value: object) -> str:
    return "—" if value is None or pd.isna(value) else f"{float(value):.0%}"


def _fmt_num(value: object) -> str:
    if value is None or pd.isna(value):
        return "—"
    v = float(value)
    return f"{0.0 if v == 0 else v:g}"  # never the "-0" artifact


def _sidebar(folder: Path) -> str:
    """Brand, the global league switcher, freshness, and a manual refresh."""
    freshness = league_freshness(folder)
    with st.sidebar:
        st.markdown(
            f"<div style='color:{_ACCENT};font-weight:700;letter-spacing:0.18em;"
            "font-size:15px;margin-bottom:2px'>MATCHUP LABS</div>"
            "<div class='v-dim'>model-priced slates, graded in public</div>",
            unsafe_allow_html=True,
        )
        st.divider()

        def _option_label(league: str) -> str:
            when = stamp_time(freshness.get(league))
            note = f" · {when:%b %-d}" if when is not None else " · no slate"
            return f"{_LEAGUE_LABELS[league]}{note}"

        league = st.radio(
            "League",
            list(LEAGUES),
            index=list(LEAGUES).index(default_league(freshness)),
            format_func=_option_label,
            key="league",
        )
        st.divider()
        if st.button("↻ Refresh slate", width="stretch"):
            fetch_artifact_dir.clear()
            st.rerun()
        st.caption(
            "Slates re-run through the day; refresh pulls the newest artifact."
        )
    return str(league)


def _metrics_row(
    view: pd.DataFrame,
    frames: dict[str, pd.DataFrame | None],
) -> None:
    """The at-a-glance strip: today's volume and how the model has been doing."""
    projections = frames.get("projections")
    games_today = 0 if projections is None else len(projections)
    record = frames.get("record")
    yesterday = "—"
    delta = None
    if record is not None and not record.empty:
        settled = record[record["result"] != "pending"]
        if not settled.empty:
            wins = int((settled["result"] == "win").sum())
            losses = int((settled["result"] == "loss").sum())
            profit = float(settled["profit"].dropna().sum())
            yesterday = f"{wins}-{losses}"
            delta = f"{profit:+.1f}u"
    summary = season_summary(frames.get("cumulative"))
    season = "—" if summary is None else f"{float(summary['units']):+.1f}u"  # type: ignore[arg-type]
    season_rec = (
        None if summary is None else f"{summary['wins']}-{summary['losses']}"
    )
    a, b, c, d = st.columns(4)
    a.metric("Games today", games_today)
    b.metric("Plays", len(view))
    c.metric("Yesterday", yesterday, delta=delta)
    d.metric("Season", season, delta=season_rec, delta_color="off")


def _edge_cell(value: object) -> str:
    """Edge as a color-coded cell — green when it clears 10%, amber past 5%."""
    if value is None or pd.isna(value):
        return "<span class='v-dim'>—</span>"
    v = float(value)
    klass = "bs-good" if v >= 0.10 else ("bs-mid" if v >= 0.05 else "")
    return f"<span class='{klass}'>+{v:.1%}</span>"


def _render_plays_board(folder: Path) -> None:
    """The day's picks across EVERY league, grouped under teal section
    headers — the reference-site board: one place answers "what's the play
    today", whatever is in season. Leagues with a slate but nothing clearing
    say so; leagues with no slate at all say that instead.
    """
    freshness = league_freshness(folder)
    chunks: list[str] = []
    total = 0
    for league in LEAGUES:
        chunks.append(
            f"<tr><td colspan='3' class='v-league'>{_LEAGUE_LABELS[league]}</td></tr>"
        )
        if not freshness.get(league):
            chunks.append("<tr><td colspan='3' class='v-dim'>No slate</td></tr>")
            continue
        frames = load_slate_frames(folder, league)
        view = plays_table(
            frames["plays"], frames["props"], frames["parlays"],
            frames["games_map"], league,
        )
        if view.empty:
            chunks.append("<tr><td colspan='3' class='v-dim'>No plays</td></tr>")
            continue
        total += len(view)
        chunks.append("".join(
            f"<tr><td>{r['matchup']}</td><td class='v-play'>{r['play']}</td>"
            f"<td>{_edge_cell(r.get('edge'))}</td></tr>"
            for r in view.to_dict("records")
        ))
    st.caption(
        "All leagues, ranked inside each by the model's edge over the "
        "devigged fair probability."
    )
    st.markdown(
        "<table class='v-table'>"
        "<tr><th>Matchup</th><th>Play</th><th>Edge</th></tr>"
        f"{''.join(chunks)}</table>",
        unsafe_allow_html=True,
    )


def _render_card(card: dict) -> None:
    kickoff = card.get("kickoff")
    when = "" if kickoff is None or pd.isna(kickoff) else (
        pd.Timestamp(kickoff)
        .tz_localize("UTC")
        .tz_convert("America/Chicago")
        .strftime("%b %-d · %-I:%M %p CT")
    )
    p_home = card.get("p_home_win")
    p_away = None if p_home is None or pd.isna(p_home) else 1.0 - float(p_home)
    plays = "".join(f"<span class='v-chip'>{p}</span>" for p in card["plays"]) or (
        "<span class='v-dim'>no plays in this game</span>"
    )
    def _score(value: object) -> str:
        return "—" if value is None or pd.isna(value) else f"{float(value):.1f}"

    nums = [
        ("proj score", f"{_score(card.get('mu_away'))}–{_score(card.get('mu_home'))}"),
        ("fair total", _fmt_num(card.get("fair_total"))),
        ("fair line", _fmt_num(card.get("fair_spread"))),
    ]
    nums_html = "".join(
        f"<div><div class='v-num-label'>{label}</div><div class='v-num'>{value}</div></div>"
        for label, value in nums
    )
    bar = ""
    if p_home is not None:
        away_w = max(3.0, min(97.0, (1.0 - float(p_home)) * 100.0))
        bar = (
            f"<div class='bs-bar'><div class='bs-bar-away' "
            f"style='width:{away_w:.0f}%'></div><div class='bs-bar-home' "
            f"style='width:{100 - away_w:.0f}%'></div></div>"
        )
    strip_bits = [b for b in (when,) if b]
    strip = (
        f"<div class='bs-strip'>{''.join(f'<span>{b}</span>' for b in strip_bits)}</div>"
        if strip_bits else ""
    )
    st.markdown(
        f"""<div class='v-card'>
<div class='v-card-head'>
  <span class='v-team'>{card['away']}
    <span class='v-win'>{_fmt_pct(p_away)}</span></span>
  <span class='v-dim'>@</span>
  <span class='v-team'><span class='v-win'>{_fmt_pct(p_home)}</span>
    {card['home']}</span>
</div>
{bar}
{strip}
<div class='v-nums'>{nums_html}</div>
<div>{plays}</div>
</div>""",
        unsafe_allow_html=True,
    )


def _render_board(
    view: pd.DataFrame,
    frames: dict[str, pd.DataFrame | None],
    league: str,
    folder: Path,
) -> None:
    """The consumer surface, in reading order: every league's picks first,
    then the selected league's game-by-game panels."""
    _render_plays_board(folder)
    cards = matchup_cards(frames["projections"], frames["games_map"], view, league)
    if not cards:
        return
    st.markdown(f"#### {_LEAGUE_LABELS[league]} matchups")
    for card in cards:
        _render_card(card)


def _render_pickem(slips: pd.DataFrame | None, legs: pd.DataFrame | None) -> None:
    """Ranked pick'em slips + the qualifying legs board."""
    if (slips is None or slips.empty) and (legs is None or legs.empty):
        st.info(
            "No pick'em board in the latest slate — it builds alongside the "
            "prop slate once prop lines and projections are live."
        )
        return
    st.caption(
        "Book-fair leg probabilities × model correlation. Lines are the "
        "sharp books' own numbers until the board feed lands — the "
        "divergence edge switches on with it."
    )
    if slips is not None and not slips.empty:
        st.markdown("#### Ranked slips")

        def _needs(slip: str) -> str:
            # The per-leg breakeven for this shape — the Unabated lesson:
            # a probability means nothing without the threshold it must clear.
            b = PICKEM_BREAKEVENS.get(str(slip))
            return "—" if b is None else f"{b:.0%}/leg"

        rows = "".join(
            f"<tr><td class='v-play'>{r['slip']}</td>"
            f"<td>{float(r['ev']):.2f}x</td>"
            f"<td>{float(r['p_all']):.0%}</td>"
            f"<td class='v-dim'>{_needs(r['slip'])}</td>"
            f"<td>{r['legs']}</td></tr>"
            for r in slips.to_dict("records")
        )
        st.markdown(
            "<table class='v-table'>"
            "<tr><th>Slip</th><th>EV</th><th>All hit</th><th>Needs</th>"
            "<th>Legs</th></tr>"
            f"{rows}</table>",
            unsafe_allow_html=True,
        )
    else:
        st.info("No slip cleared the EV floor today — that is a result, not a bug.")
    if legs is not None and not legs.empty:
        st.markdown("#### Qualifying legs")
        power2 = PICKEM_BREAKEVENS["power-2"]

        def _leg_card(r: dict) -> str:
            p_book, p_model = float(r["p_book"]), float(r["p_model"])
            # The badge is the book-fair hit probability — green once it
            # clears the strictest (power-2) breakeven, dim otherwise.
            badge_class = "bs-badge" if p_book >= power2 else "bs-badge bs-badge-dim"
            model_class = "bs-good" if p_model >= p_book else "v-dim"
            return (
                "<div class='bs-card'>"
                "<div style='display:flex;gap:12px;align-items:center'>"
                f"<span class='{badge_class}'>{p_book:.0%}</span>"
                f"<div><div class='bs-val'>{r['player']}</div>"
                f"<div class='v-play'>{r['side']} {float(r['line']):g} · "
                f"{r['market']}</div></div></div>"
                "<div class='bs-cardrow'>"
                f"<div><div class='bs-label'>Fair</div>"
                f"<div class='bs-val'>{p_book:.0%}</div></div>"
                f"<div><div class='bs-label'>Model</div>"
                f"<div class='bs-val {model_class}'>{p_model:.0%}</div></div>"
                f"<div><div class='bs-label'>Needs</div>"
                f"<div class='bs-val v-dim'>{power2:.0%}</div></div>"
                "</div></div>"
            )

        cards_html = "".join(_leg_card(r) for r in legs.to_dict("records"))
        st.markdown(f"<div class='bs-grid'>{cards_html}</div>",
                    unsafe_allow_html=True)


def _render_model(league: str, frames: dict[str, pd.DataFrame | None]) -> None:
    """The transparency block: season-to-date and exactly what model is live.

    The nfelo lesson — the model's configuration and record ARE the product.
    Config blocks are curated at promotion time (docs/MODEL_LAB.md); the
    record reads from the cumulative chain each graded run carries forward.
    """
    summary = season_summary(frames.get("cumulative"))
    if summary is None:
        st.caption("Season to date: no graded plays yet.")
    else:
        st.markdown(
            f"#### Season to date: {summary['wins']}-{summary['losses']}"
            + (f"-{summary['pushes']}" if summary["pushes"] else "")
            + f" · {float(summary['units']):+.1f}u"  # type: ignore[arg-type]
        )
        sections = summary["sections"]
        if isinstance(sections, dict) and sections:
            parts = "".join(
                f"<tr><td>{name}</td><td>{w}-{losses}</td>"
                f"<td>{units:+.1f}u</td></tr>"
                for name, (w, losses, units) in sorted(sections.items())
            )
            st.markdown(
                "<table class='v-table'>"
                "<tr><th>Section</th><th>Record</th><th>Units</th></tr>"
                f"{parts}</table>",
                unsafe_allow_html=True,
            )
    rows = "".join(
        f"<tr><td class='v-dim'>{label}</td><td>{value}</td></tr>"
        for label, value in MODEL_CONFIG.get(league, [])
    )
    st.markdown(
        f"<table class='v-table'>"
        f"<tr><th>What's live — {_LEAGUE_LABELS[league]}</th><th></th></tr>"
        f"{rows}</table>",
        unsafe_allow_html=True,
    )
    st.caption(
        "Every promotion wins a walk-forward benchmark first — nothing ships "
        "on intuition. Full history: docs/MODEL_LAB.md in the repo."
    )


def _render_record(record: pd.DataFrame | None) -> None:
    if record is None or record.empty:
        st.info("No graded record yet — it appears once a prior slate has been graded.")
        return
    settled = record[record["result"] != "pending"]
    wins = int((settled["result"] == "win").sum())
    losses = int((settled["result"] == "loss").sum())
    profit = float(settled["profit"].dropna().sum())
    st.markdown(f"#### Yesterday: {wins}-{losses} · {profit:+.1f}u")

    def _row(r: dict) -> str:
        gain = r.get("profit")
        gain_cell = "" if gain is None or pd.isna(gain) else f"{float(gain):+.2f}u"
        return (
            f"<tr class='v-{r['result']}-row'><td>{r['play']}</td>"
            f"<td>{r['market']} {r['side']}</td>"
            f"<td>{r['result']}</td><td>{gain_cell}</td></tr>"
        )

    rows = "".join(_row(r) for r in record.to_dict("records"))
    st.markdown(
        "<table class='v-table'>"
        "<tr><th>Play</th><th>Market</th><th>Result</th><th>Profit</th></tr>"
        f"{rows}</table>",
        unsafe_allow_html=True,
    )


def _gallery(
    items: list[tuple[str, Path]] | None,
    captions: str | None,
    league: str,
    kind: str,
) -> None:
    """One card section: images + download buttons + the caption copy."""
    for label, path in items or []:
        st.image(str(path), caption=label, width="stretch")
        st.download_button(
            f"Download {label}", Path(path).read_bytes(),
            file_name=Path(path).name, mime="image/png",
            key=f"dl-{kind}-{league}-{label}",
        )
    if captions:
        with st.expander("Post captions"):
            st.code(captions, language=None)


def _render_cards_tab(folder: Path, league: str) -> None:
    """The posting workflow, phone-first: each card family in its own section."""
    images = card_images(folder, league)
    empty = (
        not images["model"] and not images["simcheck"] and not images["deepdive"]
        and images["record"] is None and images["dfs"] is None
    )
    if empty:
        st.info("No cards in the latest slate yet — they render with each run.")
        return

    if images["record"] is not None:
        st.markdown("#### Model record")
        record = Path(str(images["record"]))
        st.image(str(record), width="stretch")
        st.download_button(
            "Download record card", record.read_bytes(),
            file_name=record.name, mime="image/png", key=f"dl-record-{league}",
        )

    if images["model"]:
        with st.expander(
            f"Matchup cards — market vs model ({len(images['model'])})",  # type: ignore[arg-type]
            expanded=True,
        ):
            _gallery(images["model"], images["model_captions"], league, "model")  # type: ignore[arg-type]

    if images["simcheck"]:
        with st.expander(
            f"Sim Checks — yesterday's results vs the pregame model "
            f"({len(images['simcheck'])})"  # type: ignore[arg-type]
        ):
            _gallery(images["simcheck"], images["simcheck_captions"], league, "check")  # type: ignore[arg-type]

    if images["deepdive"]:
        with st.expander(
            f"Deep dives — the analytical companion ({len(images['deepdive'])})"  # type: ignore[arg-type]
        ):
            _gallery(images["deepdive"], images["deepdive_captions"], league, "dive")  # type: ignore[arg-type]

    if images["dfs"] is not None:
        with st.expander("DFS lineup"):
            dfs = Path(str(images["dfs"]))
            st.image(str(dfs), width="stretch")
            st.download_button(
                "Download DFS lineup card", dfs.read_bytes(),
                file_name=dfs.name, mime="image/png", key=f"dl-dfs-{league}",
            )
            if images["dfs_captions"]:
                with st.expander("Post caption"):
                    st.code(images["dfs_captions"], language=None)


def main() -> None:
    st.markdown(_CSS, unsafe_allow_html=True)

    folder = _slate_dir()
    if folder is None:
        st.warning(
            "No data source configured. Add a `GITHUB_TOKEN` (Actions: read) to the "
            "app secrets, or set `VELOCITY_SLATE_DIR` to a local slate folder."
        )
        return

    league = _sidebar(folder)
    frames = load_slate_frames(folder, league)
    if all(f is None for f in frames.values()):
        fresh = league_freshness(folder)
        live = [_LEAGUE_LABELS[lg] for lg in LEAGUES if fresh.get(lg)]
        st.title(_LEAGUE_LABELS[league])
        if live:
            st.info(
                f"No {_LEAGUE_LABELS[league]} slate in the latest artifact — "
                f"likely out of season. Leagues with fresh slates: {', '.join(live)} "
                "(switch in the sidebar)."
            )
        else:
            st.info("No slate files found yet — run the live-slate workflow first.")
        return

    view = plays_table(
        frames["plays"], frames["props"], frames["parlays"], frames["games_map"],
        league,
    )
    st.title(f"{_LEAGUE_LABELS[league]} board")
    generated = None
    for key in ("plays", "props"):
        frame = frames[key]
        if frame is not None and "generated_at" in frame.columns and len(frame):
            generated = frame["generated_at"].iloc[0]
            break
    if generated is not None:
        st.caption(f"Slate generated {pd.Timestamp(generated):%b %-d, %H:%M} UTC")
    _metrics_row(view, frames)

    tab_board, tab_pickem, tab_cards, tab_perf = st.tabs(
        ["Board", "Pick'em", "Cards", "Performance"]
    )
    with tab_board:
        _render_board(view, frames, league, folder)
    with tab_pickem:
        _render_pickem(frames.get("pickem"), frames.get("pickem_legs"))
    with tab_cards:
        _render_cards_tab(folder, league)
    with tab_perf:
        _render_record(frames["record"])
        st.divider()
        _render_model(league, frames)


main()
