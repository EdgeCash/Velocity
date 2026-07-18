"""Wagering — turn projections into disciplined, positive-EV bets.

The pipeline is four steps, each a clean, independently testable stage
(:mod:`~velocity.wagering.devig` → :mod:`~velocity.wagering.edge` →
:mod:`~velocity.wagering.staking` → :mod:`~velocity.wagering.bet_log`):

1. **De-vig** a quoted price into the market's fair probability.
2. **Edge** — compare our model probability to that fair probability and price
   the expected value; only act past a threshold that covers our own error.
3. **Stake** with fractional Kelly under hard bankroll/correlation caps.
4. **Log** every bet with the closing line so closing-line value (CLV) — the
   leading indicator of genuine edge — can be tracked.

Every function here has a closed-form correct answer for textbook inputs; that
is what makes this layer the most rigorously testable part of the system.
"""
