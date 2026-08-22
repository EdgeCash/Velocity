# Backtest — Intelligence-Layer Tiers vs a Decade of Closes

**Status:** Measured (first replay, 2026-08)
**Code:** `velocity/backtest/intel_tiers.py`, `scripts/backtest_intel_tiers.py`
**Data:** committed datasets — NFL 2011–2025 (15 seasons), NCAAF 2015–2024
(10 seasons), closing `spread_line`/`total_line` in the games frames.
**Method:** walk-forward replay (same slicing as `engine.walk_forward`); each
week the model's pick against the close is gated exactly like the live slate
(`evaluate()` at −110/−110, `min_edge` 0.02), judged through the intelligence
layer with a **point-in-time** context library (`as_of` = the week's first
kickoff), and graded against the realized score. Flat one-unit stakes.
Break-even at −110 is **52.38%**.

Injury/availability signals abstain throughout — no injuries history exists
yet — so this measures the **stat signals**: matchup, form, rest.

## 1. The verdict, up front

**The stat-based context signals add no measurable edge on top of the EV
gate.** Confirming context does not raise the win rate of qualifying picks in
either league; in the NFL the direction is mildly *inverted* (the
context-contradicted side did slightly better). The layer's edge component
does order NCAAF outcomes — consistent with the already-proven
points-of-disagreement edge — but that is the model speaking, not the
context.

This is the expected null for an efficient market: matchup/form/rest are
computed from **public season stats, which the closing line already prices**.
The result converts INTEL.md §5's "unproven" into "measured null", and it
validates the v1 design decision: the layer is an **evidence organizer and a
veto mechanism, not a ranker**. Tier A does not mean bet more.

## 2. The record

### 2.1 NFL — 6,755 qualifying picks, 15 seasons

| Slice | Bets | Win % | Flat ROI |
|---|---:|---:|---:|
| Tier A | 3,359 | 50.7% | −0.032 |
| Tier B | 3,177 | 52.2% | −0.004 |
| Tier C | 219 | 52.8% | +0.008 |
| Context confirming (≥ +0.15) | 1,544 | 51.5% | −0.017 |
| Context contradicting (≤ −0.15) | 3,155 | 52.6% | +0.004 |
| Context neutral | 2,056 | 49.7% | −0.051 |

Per-season above break-even: tier A 3/15, B 6/15, C 10/15.

The inversion is directionally consistent (contradicted picks won more in
the thin- and mid-confidence bands too) but **not significant**: confirming
vs contradicting is ~1.1pp on these samples, ≈ 0.7σ. The honest headline is
"no measurable difference", with the *sign* of the point estimate a caution
against ever staking up on stat-confirmed NFL picks. It also fits the
standing NFL read (BACKTEST_NFL.md: no edge overall) — when the model and
the public stats agree, that agreement is already in the number.

### 2.2 NCAAF — 17,990 qualifying picks, 10 seasons

| Slice | Bets | Win % | Flat ROI |
|---|---:|---:|---:|
| Tier A | 12,546 | 51.4% | −0.019 |
| Tier B | 5,388 | 48.5% | −0.073 |
| Tier C | 56 | 52.7% | +0.006 |
| Context confirming | 1,409 | 48.8% | −0.066 |
| Context contradicting | 4,699 | 48.8% | −0.067 |
| Context neutral | 11,882 | 51.4% | −0.019 |

Tier A beats B by 2.9pp (≈ 3.6σ on these samples) — but the context split
shows why that is **not** a context result: confirming and contradicting are
*identical* (48.8%), and the model-confidence bands alone order the outcomes
(p_model < 0.55: 48.8% → 0.55–0.60: 49.4% → > 0.60: 51.0%). The tier blend
is 40% edge, so tier A is largely "the model is confident", which NCAAF
rewards — the same phenomenon as the proven ≥4-point totals-disagreement
filter (BACKTEST_NCAAF.md), rediscovered through the tier lens. Strongly
non-neutral context (either direction) actually sits *below* neutral in the
mid and fat confidence bands — strong context scores concentrate in lopsided
matchups, where the closes are sharpest.

## 3. What this changes (and what it does not)

1. **Keep the contract.** No tier-conditioned staking; the composite stays a
   presentation-layer ranking. The measured null is now the documented
   reason, not just caution.
2. **The veto channel is untested — and is now the priority.** The one
   signal family this replay could not measure (injuries/availability) is
   also the only one carrying information that is *not* already in public
   season stats. Banking injuries snapshots (INTEL.md §6) is the next thing
   this backtest needs; until a season of history exists, the QB-out and
   player-out vetoes remain conservative safety checks, presumed useful,
   unproven.
3. **No sign-flipping.** The NFL inversion is one replay at ~0.7σ; per the
   multiple-comparisons discipline (DESIGN.md §7.3), "fade the confirmed
   side" would need to survive a second independent window before it is
   even a hypothesis worth a sweep.
4. **Edge ordering in NCAAF is real and already wired** — the live
   points-disagreement filter covers it. Nothing new to wire.

## 4. Caveats

- Synthetic −110 two-way pricing against the close: no line shopping, no
  vig variation, no CLV measurement — this is a beat-the-close win-rate
  test, the BACKTEST_*.md genre, not a bankroll simulation.
- The context library uses raw season means (deliberately, for
  explainability); an opponent-adjusted context could test differently.
- One replay, one configuration (default weights, default tier cuts). The
  per-pick parquet (`--out`) is banked so alternative cuts can be re-scored
  without re-running the models.

## 5. Reproduce

```bash
python scripts/backtest_intel_tiers.py --league nfl   --data datasets/nfl   --out artifacts/tiers
python scripts/backtest_intel_tiers.py --league ncaaf --data datasets/ncaaf --out artifacts/tiers
```
