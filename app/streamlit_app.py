"""MatchUp Labs app — the day's slate as a dark, readable board.

Four tabs over the live-slate workflow's persisted artifacts:

* **Cards** — the day's graphics: model cards, Sim Checks, and the record
  card, each viewable and downloadable, with the caption copy alongside. This
  is the posting workflow on a phone.
* **Plays** — the reference-style two-column board: matchup on the left, the
  play in accent teal on the right ("Kansas City ML -145", "Allen O249.5
  PASS YDS +105"), games → props → parlays.
* **Matchups** — one card per game: teams, kickoff, win probabilities,
  projected score, fair total/line — and that game's plays.
* **Record** — yesterday's graded plays (the same record the email leads with).

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
    card_images,
    load_slate_frames,
    matchup_cards,
    plays_table,
)

st.set_page_config(page_title="MatchUp Labs — Plays", page_icon="🏈", layout="centered")

_API = "https://api.github.com"
_ACCENT = "#3ddad0"
_CSS = """
<style>
.stApp { background: #0a0e13; }
h1, h2, h3 { color: #f2f5f7 !important; letter-spacing: 0.04em; }
.v-table { width: 100%; border-collapse: collapse; background: #10151c;
           border-radius: 10px; overflow: hidden; }
.v-table th { color: #7d8894; font-size: 12px; letter-spacing: 0.12em;
              text-transform: uppercase; text-align: left; padding: 10px 14px;
              border-bottom: 1px solid #1d2430; }
.v-table td { padding: 13px 14px; border-bottom: 1px solid #1d2430;
              font-size: 16px; color: #e8edf2; }
.v-league { text-align: center !important; color: #3ddad0 !important;
            font-size: 12px; letter-spacing: 0.25em; font-weight: 700; }
.v-play { color: #3ddad0; font-weight: 600; }
.v-dim { color: #7d8894; font-size: 13px; }
.v-card { background: #10151c; border: 1px solid #1d2430; border-radius: 12px;
          padding: 14px 16px; margin-bottom: 14px; }
.v-card-head { display: flex; justify-content: space-between; align-items: baseline; }
.v-team { color: #f2f5f7; font-size: 19px; font-weight: 700; }
.v-strip { background: #14808c; color: #eafcfb; border-radius: 6px;
           padding: 5px 10px; font-size: 12px; letter-spacing: 0.06em;
           margin: 10px 0; display: flex; gap: 18px; flex-wrap: wrap; }
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
    return "—" if value is None or pd.isna(value) else f"{float(value):g}"


def _render_plays(view: pd.DataFrame) -> None:
    if view.empty:
        st.info("No plays cleared the edge threshold in the latest slate.")
        return
    rows = "".join(
        f"<tr><td>{r['matchup']}</td><td class='v-play'>{r['play']}</td></tr>"
        for r in view.to_dict("records")
    )
    st.markdown(
        "<table class='v-table'>"
        "<tr><th>Matchup</th><th>Play</th></tr>"
        f"<tr><td colspan='2' class='v-league'>NFL · NCAAF</td></tr>{rows}</table>",
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
    nums = [
        ("proj score", f"{_fmt_num(card.get('mu_away'))}–{_fmt_num(card.get('mu_home'))}"),
        ("fair total", _fmt_num(card.get("fair_total"))),
        ("fair line", _fmt_num(card.get("fair_spread"))),
    ]
    nums_html = "".join(
        f"<div><div class='v-num-label'>{label}</div><div class='v-num'>{value}</div></div>"
        for label, value in nums
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
<div class='v-strip'><span>{when}</span></div>
<div class='v-nums'>{nums_html}</div>
<div>{plays}</div>
</div>""",
        unsafe_allow_html=True,
    )


def _render_record(record: pd.DataFrame | None) -> None:
    if record is None or record.empty:
        st.info("No graded record yet — it appears once a prior slate has been graded.")
        return
    settled = record[record["result"] != "pending"]
    wins = int((settled["result"] == "win").sum())
    losses = int((settled["result"] == "loss").sum())
    profit = float(settled["profit"].dropna().sum())
    st.markdown(f"### Yesterday: {wins}-{losses} · {profit:+.1f}u")

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


def _render_cards_tab(folder: Path) -> None:
    """The day's graphics: view, download, and copy the post copy — phone-first."""
    league = str(
        st.radio("League", ["NFL", "NCAAF"], horizontal=True, key="cards-league")
    ).lower()
    images = card_images(folder, league)
    model, checks = images["model"], images["simcheck"]
    record, dfs = images["record"], images["dfs"]
    if not model and not checks and record is None and dfs is None:
        st.info("No cards in the latest slate yet — they render with each run.")
        return

    if record is not None:
        st.markdown("#### Model record")
        st.image(str(record), width="stretch")
        st.download_button(
            "Download record card", Path(str(record)).read_bytes(),
            file_name=Path(str(record)).name, mime="image/png",
            key=f"dl-record-{league}",
        )

    if checks:
        st.markdown("#### Sim Checks — yesterday's results vs the pregame model")
        for label, path in checks:  # type: ignore[misc]
            st.image(str(path), caption=label, width="stretch")
            st.download_button(
                f"Download {label}", Path(path).read_bytes(),
                file_name=Path(path).name, mime="image/png",
                key=f"dl-check-{league}-{label}",
            )
        if images["simcheck_captions"]:
            with st.expander("Sim Check captions"):
                st.code(images["simcheck_captions"], language=None)

    if model:
        st.markdown("#### Today's matchup cards — market vs model")
        for label, path in model:  # type: ignore[misc]
            st.image(str(path), caption=label, width="stretch")
            st.download_button(
                f"Download {label}", Path(path).read_bytes(),
                file_name=Path(path).name, mime="image/png",
                key=f"dl-model-{league}-{label}",
            )
        if images["model_captions"]:
            with st.expander("Matchup card captions"):
                st.code(images["model_captions"], language=None)

    if dfs is not None:
        st.markdown("#### DFS lineup")
        st.image(str(dfs), width="stretch")
        st.download_button(
            "Download DFS lineup card", Path(str(dfs)).read_bytes(),
            file_name=Path(str(dfs)).name, mime="image/png",
            key=f"dl-dfs-{league}",
        )
        if images["dfs_captions"]:
            with st.expander("DFS lineup caption"):
                st.code(images["dfs_captions"], language=None)


def main() -> None:
    st.markdown(_CSS, unsafe_allow_html=True)
    st.markdown(
        f"<div style='color:{_ACCENT};font-weight:700;letter-spacing:0.18em;"
        "font-size:14px'>MATCHUP LABS</div>",
        unsafe_allow_html=True,
    )
    st.title("PLAYS")

    folder = _slate_dir()
    if folder is None:
        st.warning(
            "No data source configured. Add a `GITHUB_TOKEN` (Actions: read) to the "
            "app secrets, or set `VELOCITY_SLATE_DIR` to a local slate folder."
        )
        return
    frames = load_slate_frames(folder)
    if all(f is None for f in frames.values()):
        st.info("No slate files found yet — run the live-slate workflow first.")
        return

    view = plays_table(
        frames["plays"], frames["props"], frames["parlays"], frames["games_map"]
    )
    generated = None
    for key in ("plays", "props"):
        frame = frames[key]
        if frame is not None and "generated_at" in frame.columns and len(frame):
            generated = frame["generated_at"].iloc[0]
            break
    if generated is not None:
        st.caption(f"Slate generated {pd.Timestamp(generated):%b %-d, %H:%M} UTC")

    tab_cards, tab_plays, tab_matchups, tab_record = st.tabs(
        ["Cards", "Plays", "Matchups", "Record"]
    )
    with tab_cards:
        _render_cards_tab(folder)
    with tab_plays:
        _render_plays(view)
    with tab_matchups:
        cards = matchup_cards(frames["projections"], frames["games_map"], view)
        if not cards:
            st.info("No projections persisted in the latest slate.")
        for card in cards:
            _render_card(card)
    with tab_record:
        _render_record(frames["record"])


main()
