"""Velocity dashboard — view the daily plays and matchup cards.

A local viewer over a live-slate output folder (what ``scripts/run_live_slate.py``
writes, or an extracted private Actions artifact). No network, no model, no API
credits — it only reads persisted parquet + the rendered cards HTML.

    pip install -e '.[dashboard]'
    streamlit run dashboard/app.py
    # point the sidebar at your slate folder, or set VELOCITY_SLATE_DIR

The plays shown are the model's *suggested* bets. They are a CLV-positive but
in-sample, not-yet-profitable signal — treat them as a paper-trade to log against
the close, not as instructions to stake. See docs/PROJECT_STATUS.md.
"""

from __future__ import annotations

import os
from pathlib import Path

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components
from velocity.report.dashboard_data import (
    SlateSet,
    discover_slates,
    shape_plays,
    slate_summary,
)

st.set_page_config(page_title="Velocity — daily plays", page_icon="⚾", layout="wide")


def _read(path: Path | None) -> pd.DataFrame | None:
    if path is None or not path.exists():
        return None
    try:
        return pd.read_parquet(path)
    except Exception as exc:  # noqa: BLE001 - a bad file shouldn't crash the viewer
        st.warning(f"could not read {path.name}: {exc}")
        return None


def _sidebar() -> tuple[Path, float]:
    st.sidebar.title("⚾ Velocity")
    default_dir = os.environ.get("VELOCITY_SLATE_DIR", "artifacts")
    data_dir = Path(st.sidebar.text_input("Slate folder", value=default_dir))
    bankroll = st.sidebar.number_input(
        "Bankroll (for stake %)", min_value=1.0, value=100.0, step=50.0
    )
    st.sidebar.caption(
        "Point this at the folder `run_live_slate.py --out` wrote to, or an "
        "extracted private Actions artifact."
    )
    return data_dir, bankroll


def _pick_slate(slates: list[SlateSet]) -> SlateSet:
    labels = [s.label for s in slates]
    choice = st.sidebar.selectbox(
        "Slate", options=range(len(slates)), format_func=lambda i: labels[i]
    )
    return slates[choice]


def _plays_tab(sset: SlateSet, bankroll: float) -> None:
    games = _read(sset.game_path)
    props = _read(sset.props_path)
    games_map = _read(sset.games_path)

    s = slate_summary(games, props)
    c1, c2, c3 = st.columns(3)
    c1.metric("Game plays", s["game_plays"])
    c2.metric("Prop plays", s["prop_plays"])
    c3.metric("Total staked", f"{s['total_staked']:.2f}")

    st.subheader("Game markets")
    game_disp = shape_plays(games, games_map, bankroll=bankroll)
    if game_disp.empty:
        st.info("No game-market plays cleared the edge threshold in this slate.")
    else:
        st.dataframe(game_disp, use_container_width=True, hide_index=True)

    st.subheader("Player props")
    st.caption("total_bases is excluded live (no edge at any calibration — see PROJECT_STATUS).")
    prop_disp = shape_plays(props, games_map, bankroll=bankroll, player=True)
    if prop_disp.empty:
        st.info("No prop plays in this slate (or no prop board was pulled).")
    else:
        st.dataframe(prop_disp, use_container_width=True, hide_index=True)


def _cards_tab(sset: SlateSet) -> None:
    if sset.cards_path is None or not sset.cards_path.exists():
        st.info("No matchup cards were rendered for this slate (MLB game days only).")
        return
    html = sset.cards_path.read_text()
    components.html(html, height=1500, scrolling=True)


def main() -> None:
    data_dir, bankroll = _sidebar()
    slates = discover_slates(data_dir)

    st.title("Daily plays & matchup cards")
    if not slates:
        st.warning(f"No slates found under `{data_dir}`.")
        st.markdown(
            "Generate one with the live runner (needs the odds API key):\n"
            "```\npython scripts/run_live_slate.py --league mlb --out artifacts/slates\n```\n"
            "or download a private Actions artifact and extract it into that folder, "
            "then set the sidebar path."
        )
        return

    sset = _pick_slate(slates)
    st.caption(
        f"**{sset.label}** · showing suggested plays — a CLV-positive but in-sample "
        "signal. Paper-trade against the close; don't stake blind (see `docs/PROJECT_STATUS.md`)."
    )

    plays, cards = st.tabs(["Plays", "Matchup cards"])
    with plays:
        _plays_tab(sset, bankroll)
    with cards:
        _cards_tab(sset)


if __name__ == "__main__":
    main()
