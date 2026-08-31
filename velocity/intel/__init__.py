"""The intelligence layer — context-aware judgment on top of the EV gate.

The wagering engine answers "is the price wrong?"; this package answers "does
the *football* agree?". Every bet that clears the EV gate is judged against
the game's assembled evidence — unit matchups, recent form, rest, and the
injury report — and folded into a conviction tier and argued pick sets. The
layer confirms, demotes, or vetoes; it never promotes a bet the model didn't
like and never touches stakes. See ``docs/INTEL.md``.
"""

from velocity.intel.context import (
    ContextLibrary,
    GameContext,
    InjuryOut,
    TeamContext,
)
from velocity.intel.picks import PickSet, build_pick_sets, intel_frame, render_pick_sets
from velocity.intel.score import (
    DEFAULT_SIGNAL_WEIGHTS,
    TIER_FLAGGED,
    Conviction,
    IntelConfig,
    assess,
    assess_bets,
)
from velocity.intel.signals import (
    ExternalRatingSignal,
    FormSignal,
    InjurySignal,
    MatchupSignal,
    PropAvailabilitySignal,
    PropExternalSignal,
    PropLineOutlierSignal,
    PropMatchupSignal,
    RestSignal,
    SignalResult,
    default_game_signals,
    default_prop_signals,
)

__all__ = [
    "DEFAULT_SIGNAL_WEIGHTS",
    "TIER_FLAGGED",
    "ContextLibrary",
    "Conviction",
    "ExternalRatingSignal",
    "FormSignal",
    "GameContext",
    "InjuryOut",
    "InjurySignal",
    "IntelConfig",
    "MatchupSignal",
    "PickSet",
    "PropAvailabilitySignal",
    "PropExternalSignal",
    "PropLineOutlierSignal",
    "PropMatchupSignal",
    "RestSignal",
    "SignalResult",
    "TeamContext",
    "assess",
    "assess_bets",
    "build_pick_sets",
    "default_game_signals",
    "default_prop_signals",
    "intel_frame",
    "render_pick_sets",
]
