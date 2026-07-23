"""Per-game run environment — park + weather → sim tilts.

Turns a game's venue and first-pitch weather into the two knobs the baseball sim
consumes: a multiplicative **HR factor** (``_scale_hr``) and a symmetric
**reach-base tilt** (``_tilt_offense``) applied to both lineups. It folds together:

* the park's HR factor (already used) *times* a temperature HR multiplier,
* the park's **non-HR** run environment (doubles / triples / BABIP — the part of
  the runs factor not explained by HR) *plus* temperature's small BABIP effect,
* a **roof gate**: indoors (fixed roof or a closed retractable) neutralizes
  weather, so still air isn't given a phantom temperature swing.

Pure and offline-testable, and deliberately **report-free** — it takes plain
numbers (the park indices on a 100 scale, the temperature, an indoors flag), so
the models layer never imports the report table. The caller (the live runner)
reads :mod:`velocity.report.park_factors` and the weather and passes the numbers
in. Magnitudes are first-order (see the constants) and calibration-pending against
CLV; the *direction* is what's asserted in tests.
"""

from __future__ import annotations

from dataclasses import dataclass

# Temperature: ~0.6% more HR per °F above a ~70°F reference (Nathan; see #31),
# clamped so an extreme forecast can't produce an absurd multiplier.
NEUTRAL_TEMP_F = 70.0
_HR_PER_DEG_F = 0.006
_HR_MULT_MIN, _HR_MULT_MAX = 0.85, 1.15
# Temperature's secondary effect on balls in play (carry → BABIP), much smaller.
_TEMP_TILT_PER_DEG = 0.0006

# How much of a park's run index is carried by HR (so the non-HR tilt doesn't
# double-count what the HR factor already applies), and how strongly the residual
# non-HR run index tilts reach-base outcomes.
_HR_RUN_SHARE = 0.5
_PARK_TILT_COEF = 0.3


@dataclass(frozen=True)
class RunEnvironment:
    """The two per-game knobs: an HR multiplier and a symmetric reach-base tilt."""

    hr_factor: float = 1.0
    tilt: float = 0.0


def temperature_hr_multiplier(temp_f: float) -> float:
    """Multiplicative HR factor from temperature (1.0 at the neutral reference)."""
    mult = 1.0 + _HR_PER_DEG_F * (temp_f - NEUTRAL_TEMP_F)
    return max(_HR_MULT_MIN, min(_HR_MULT_MAX, mult))


def park_non_hr_tilt(runs_index: float, hr_index: float) -> float:
    """Reach-base tilt from a park's *non-HR* run environment (indices, 100 = neutral).

    The runs index includes HR's contribution; the HR factor already applies that,
    so the tilt uses the residual (``runs − HR_SHARE·HR``). A doubles/BABIP park
    like Fenway (108 runs / 96 HR) gets a positive tilt; an HR-only park like
    Yankee Stadium (100 runs / 110 HR) gets a slightly negative one.
    """
    residual = (runs_index - 100.0) - _HR_RUN_SHARE * (hr_index - 100.0)
    return _PARK_TILT_COEF * residual / 100.0


def game_run_environment(
    *,
    park_hr_index: float = 100.0,
    park_runs_index: float = 100.0,
    temp_f: float | None = None,
    indoors: bool = False,
) -> RunEnvironment:
    """Combine park + (roof-gated) weather into a :class:`RunEnvironment`.

    ``park_hr_index`` / ``park_runs_index`` are the committed park factors on the
    100 scale. ``temp_f`` is the first-pitch forecast (``None`` if unknown);
    ``indoors`` gates it off (fixed roof / closed retractable), since a controlled
    climate shouldn't inherit the outdoor temperature.
    """
    hr_factor = park_hr_index / 100.0
    tilt = park_non_hr_tilt(park_runs_index, park_hr_index)
    if temp_f is not None and not indoors:
        hr_factor *= temperature_hr_multiplier(temp_f)
        tilt += _TEMP_TILT_PER_DEG * (temp_f - NEUTRAL_TEMP_F)
    return RunEnvironment(hr_factor=hr_factor, tilt=tilt)
