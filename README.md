# Velocity

NFL & NCAAF game and player-prop projection and wagering system.

Velocity projects the full distribution of every game and market — spreads,
totals, moneylines, team totals, and player props — from a shared Monte Carlo
simulation, then converts those projections into disciplined, positive-expected
-value wagers via de-vigging, edge estimation, and fractional-Kelly staking.

The guiding metric is **closing-line value (CLV)**: the market's closing price is
the sharpest widely available forecast, so consistently beating it is the real
signal of edge.

See [`docs/DESIGN.md`](docs/DESIGN.md) for the full system design and
[`docs/BUILD.md`](docs/BUILD.md) for the phased, test-gated build plan.

## Layout

```
velocity/
  store/      canonical schema, parquet/duckdb IO, point-in-time access
  features/   opponent-adjusted efficiency, usage, context
  models/     game models (NFL/NCAAF), props, shared Monte Carlo sim
  wagering/   de-vig, edge/EV, Kelly staking, portfolio
  backtest/   walk-forward engine + metrics
  eval/       calibration + reports
```

## Development

```bash
pip install -e '.[dev]'
pytest          # fast, offline, fixture-backed suite
ruff check .
mypy
```

The test suite never hits the network — it runs on the frozen fixtures under
`tests/fixtures/`. Determinism is enforced via seeded generators
(`velocity/util/seed.py`), so the same seed and input always produce the same
output.
