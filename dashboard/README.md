# Velocity dashboard

A local Streamlit viewer for the daily **plays** (game markets + player props) and the
**matchup cards**. It only *reads* what the live runner already writes — persisted
parquet + the rendered cards HTML — so it needs **no network, no model, and no API
credits**.

## Run it

```bash
pip install -e '.[dashboard]'          # adds streamlit
streamlit run dashboard/app.py
```

In the sidebar, point **Slate folder** at wherever your slate files live, or set
`VELOCITY_SLATE_DIR`. Two ways to get slate files there:

- **Generate locally** (needs the odds API key in the env):
  ```bash
  python scripts/run_live_slate.py --league mlb --out artifacts/slates
  ```
- **Download a private Actions artifact** (the `live-slate` run's output) and extract it
  into that folder. The viewer recurses, so per-run subfolders are fine.

## What it shows

- **Plays** tab — the model's suggested bets, one table for game markets and one for
  player props, with matchup label, market/side/number, book, American price, model vs
  fair probability, edge %, and stake (absolute + % of bankroll), highest-edge first.
  `total_bases` is excluded live (no edge at any calibration — see
  `../docs/PROJECT_STATUS.md`).
- **Matchup cards** tab — the per-game cards (projected score, fair line, PLAY/LEAN/PASS
  calls, team stat grid, weather + park), embedded from the runner's `cards_*.html`.

## A word on the plays

These are a **CLV-positive but in-sample, not-yet-profitable** signal. Treat them as a
**paper-trade to log against the close**, not as instructions to stake real money. The
trust gates (out-of-sample validation, forward live-CLV) are in
`../docs/WAGERING_SYSTEM_PLAN.md`.

## How it's built

The pure data layer — run discovery, grouping, and plays shaping — lives in
`velocity/report/dashboard_data.py` and is unit-tested
(`tests/test_dashboard_data.py`); `app.py` is a thin Streamlit view over it, so the app
has no untested logic and the test suite never imports Streamlit.
