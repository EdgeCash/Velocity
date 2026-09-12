# The WNBA sim repair — 2026-09

Baseball's sim was rebuilt from the ground up
([`docs/BUILD_MLB.md`](BUILD_MLB.md) §8): runs are small integers, the
distribution is overdispersed against Poisson, and the home team's unbatted
ninth makes the whole thing asymmetric. The natural next move was to do the
same for the WNBA. **Measuring first said not to**, and this is the record of
what it actually needed.

## What was wrong, measured against the promoted fit

Not against the league averages — against the **walk-forward residuals of the
model production actually runs** (pace×efficiency, λ=10, recency-8), over 718
games:

| | shipped | measured residual | |
|---|---|---|---|
| `sd_margin` | 12.5 | **12.93** | 3% low — nearly right |
| `sd_total` | 15.0 | **18.15** | **21% low — the real defect** |
| corr(margin, total) | 0.0 | −0.014 | already right |
| P(tie) | **3.2%** | **0.0%** (0 in 875 games) | impossible outcome |

The first diagnostic pass compared the shipped 12.5 against the league's
*unconditional* margin sd of 14.0 and concluded the margin was 11% narrow.
That was the wrong comparison: what a sim needs is the dispersion of the
model's error, not of the league. Against the model the margin was fine, and
**the total was the thing costing money** — every WNBA total, team total and
alternate line was priced a fifth too confident.

The model also projects totals **2.13 points low** on average. That is a
ratings-level bias rather than a sim one (football has `velocity/models/
level.py` for exactly this) and is left for its own change; it is recorded
here so it is not lost.

## What it needed, and what it did not

Baseball's machinery does **not** port, and using it would have been
pattern-matching rather than modelling:

* **Counts.** A WNBA score is eighty-odd points, where a normal is a good
  approximation. Runs are 0–15, where it is not.
* **Censoring by winning.** The home team's unbatted ninth is baseball's
  biggest asymmetry. Basketball has no equivalent — both teams play all forty
  minutes whatever the score.

What the two sports *do* share is that the final score cannot be level. So
`velocity/models/overtime.py` is deliberately small: when the rounded scores
come out tied, play the extra period the real game would have played, with
**both sides scoring** — which is what separates it from baseball's tie
resolution, where a single run ends it.

Its constants come from the clock, not from a fit: a WNBA overtime is five
minutes against regulation's forty, the league scores ~84 a team, so a period
is ~21 combined and its margin dispersion is regulation's scaled by the time
(12.9 × √(5/40) ≈ 4.6). They are not fitted further because **they cannot
be**: only final scores are banked, so which games went to overtime is not
observable. They are instead *checked* against the joint distribution they
produce, below.

## What the repair bought

Simulated at the league's own mean margin and total, 400k draws:

| | real (875 games) | shipped | repaired |
|---|---|---|---|
| P(tie) | 0.0000 | 0.0319 | **0.0000** |
| P(home −0.5) vs P(home +0.5) gap | 0.0000 | 0.0319 | **0.0000** |
| total sd | 19.05 | 15.02 | **18.13** |
| margin sd | 14.00 | 12.48 | 12.94 |
| mean total, close − blowout | +5.73 | +0.02 | +1.83 |

Two of those are exact. **The short spreads are the clearest win**: a lump of
probability on an impossible outcome made covering −0.5 and covering +0.5
differ by a full 3.2 points, and now they differ by nothing, as they must in a
sport with no ties.

**The total-versus-margin gradient is only a third recovered** (+1.83 against
a real +5.73), and that is worth stating plainly rather than tuning away. The
shortfall says the real margin distribution has more mass near zero than a
normal does — basketball's endgame compresses margins, as a trailing team
fouls and a leading team runs clock — so more games reach overtime than a
normal implies. That is a **shape** claim the normal cannot hold at any
choice of constants. The repo already has the lever for it
(`SimConfig.residuals`, an empirical residual pool), and 718 walk-forward
pairs is too thin to fit one well. Revisit with a third season.

## What it did not buy, against expectation

The MLB round's write-up predicted this repair might move the anchoring weight
the way baseball's did (0.153 → 0.245). **It did not**, and the prediction was
wrong in an instructive way. Re-running the anchoring sweep on the repaired
sim (`scripts/sweep_anchoring.py --league wnba`):

| | before the repair | after |
|---|---|---|
| w, all (n=572) | 0.270 ± 0.161 | 0.262 ± 0.159 |
| w, 2025 | 0.431 ± 0.204 | 0.425 ± 0.202 |
| w, 2026 | −0.013 ± 0.264 | −0.023 ± 0.261 |
| ATS vs the close | 54.2% ± 2.1% | 54.2% ± 2.1% |

Nothing moved, and on reflection nothing should have. The sweep measures the
**moneyline**, and the tie mass was never really costing the moneyline:
`p_home_win` already split ties evenly and an extra period is close to a coin
flip, so the split was right by accident. The repair improves exactly the
things it was aimed at — the total and the short spreads — and leaves alone
the one it was not.

So the WNBA verdict stands unchanged, and now stands against a
correctly-shaped sim: **the league stays paper**
([`docs/MODEL_LAB.md`](MODEL_LAB.md) WNBA Round 3). Its ATS edge replicates at
54.2% and still sits 0.86 standard errors above the 52.4% that pays.
