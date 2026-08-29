"""Live-runner wagering policy — the defaults the CLI ships with.

The knobs live in ``SlateConfig`` and are behavior-tested elsewhere; what CI
must pin is the *policy* the runner resolves when nobody passes a flag: NFL
market anchoring on at the Round-3 select-chosen weight, every other league
on the raw model until its own lab argues otherwise (docs/MODEL_LAB.md).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_SCRIPT = Path(__file__).parent.parent / "scripts" / "run_live_slate.py"


def _runner():
    spec = importlib.util.spec_from_file_location("run_live_slate", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_model_weight_resolves_per_league() -> None:
    runner = _runner()
    # Unset → the league policy: NFL anchors at 0.2, everyone else raw.
    assert runner.resolve_model_weight(None, "nfl") == 0.2
    for league in ("ncaaf", "mlb", "wnba", "ncaab", "nhl"):
        assert runner.resolve_model_weight(None, league) == 1.0
    # An explicit flag always wins, 1.0 (raw) included.
    assert runner.resolve_model_weight(0.5, "nfl") == 0.5
    assert runner.resolve_model_weight(1.0, "nfl") == 1.0


def test_cli_default_leaves_weight_to_league_policy() -> None:
    args = _runner().build_parser().parse_args(["--league", "nfl"])
    assert args.model_weight is None  # sentinel — resolved by league, not argparse
    assert args.min_edge == 0.02
