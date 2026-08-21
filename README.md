# Velocity

NFL & NCAAF game and player-prop projection and wagering system.

Velocity projects the full distribution of every game and market — spreads,
totals, moneylines, team totals, and player props — from a shared Monte Carlo
simulation, then converts those projections into disciplined, positive-expected
-value wagers via de-vigging, edge estimation, and fractional-Kelly staking.

The guiding metric is **closing-line value (CLV)**: the market's closing price is
the sharpest widely available forecast, so consistently beating it is the real
signal of edge.

On top of the EV gate sits an **intelligence layer** (`velocity/intel`,
[`docs/INTEL.md`](docs/INTEL.md)): every qualifying bet is judged against the
game's evidence — unit matchups, recent form, rest, and the injury report —
and tiered into argued pick sets. It confirms, demotes, or vetoes; it never
promotes a bet the model didn't like and never touches stakes.

See [`docs/DESIGN.md`](docs/DESIGN.md) for the full system design,
[`docs/BUILD.md`](docs/BUILD.md) for the phased, test-gated build plan, and
[`docs/WAGERING.md`](docs/WAGERING.md) for the wagering system's current state
and build plan.

## Layout

```
velocity/
  store/      canonical schema, parquet/duckdb IO, point-in-time access
  features/   opponent-adjusted efficiency, usage, context
  models/     game models (NFL/NCAAF), props, shared Monte Carlo sim
  wagering/   de-vig, edge/EV, Kelly staking, portfolio
  intel/      intelligence layer — matchup/form/rest/injury signals → tiered picks
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
