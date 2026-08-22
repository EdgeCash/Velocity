# Velocity — The Intelligence Layer

**Status:** Built (v1), wired into the live slate runner
**Code:** `velocity/intel/` · **Tests:** `tests/test_intel_*.py`
**Companion to:** [`docs/DESIGN.md`](DESIGN.md) §6 (the EV gate this layer sits
behind), [`docs/WAGERING.md`](WAGERING.md) (the staking discipline it never
touches).

---

## 1. What it is, in one paragraph

The wagering engine answers *"is the price wrong?"* — de-vig, edge, EV, Kelly.
The intelligence layer answers the complementary question: *"does the football
agree?"* Every bet that clears the EV gate is judged against the game's
assembled evidence — unit matchups (EPA/play by phase), recent form, rest
spots, and the injury report — and folded into a **conviction score and tier**.
The output is a set of argued pick cards: **Prime** (edge + confirming
context), **Solid** (edge, neutral context), **Model-only** (edge, but the
context leans against), and **Flagged** (vetoed on information the pricing
model cannot see — a QB ruled out, a propped player on the report). Every pick
carries its evidence lines, so the card reads as an argument, not a list.

## 2. The contract (what keeps it honest)

Three rules, enforced by construction:

1. **It never promotes.** Only bets that already cleared `evaluate()`'s gate
   (`edge ≥ min_edge` and `EV > 0`) reach this layer. A −EV bet cannot be
   resurrected by a nice matchup — the layer literally never sees it.
2. **It confirms, demotes, or vetoes.** Agreeing context raises conviction;
   contradicting context demotes the tier; a veto flags the bet as unplayable
   on information the model has not priced (the QB the ratings fit still
   believes is starting).
3. **It never touches stakes.** Kelly sizing is calibrated against the model's
   probabilities and stays exactly as the slate computed it. The layer ranks,
   tiers, and flags; the operator decides. (If tier-conditioned staking ever
   looks attractive, it goes through a walk-forward backtest PR like every
   other staking change — §3 of WAGERING.md.)

## 3. Architecture

```
build_slate / build_prop_slate          (the EV gate — unchanged)
        │  qualifying Bets
        ▼
ContextLibrary.build(games, plays, injuries, as_of=now)     velocity/intel/context.py
        │  GameContext per game (units, form, rest, outs; z-scales)
        ▼
signals: matchup · form · rest · injury · availability · prop_matchup
        │  SignalResult per (bet, signal): score ∈ [−1,+1] aligned
        │  with the side, a rationale sentence, optional veto
        ▼
assess() → Conviction (edge_score ⊕ context_score → tier)   velocity/intel/score.py
        ▼
build_pick_sets / intel_frame / render_pick_sets            velocity/intel/picks.py
```

### 3.1 Context (`context.py`)

One `GameContext` per game, built from the same committed datasets the model
fit on, deliberately reusing the deep-dive card's stat definitions
(`scoring_form` / `epa_form` / `team_form`) so the layer judges bets on
exactly the numbers a human sees on the card:

- **Units:** season EPA/play — offense/defense overall plus pass/rush splits
  (defensive values are EPA *allowed*). Scoring form (`ppg`/`papg`) always,
  as the fallback when no plays exist (NCAAF boxscore-only spans, MLB, WNBA).
- **Form:** each team's last-5 results, streak, and recent scoring means over
  its own last `form_games` games.
- **Rest:** full days since the team's last completed game.
- **Outs:** the injuries snapshot filtered to genuine outs
  (`is_out` — Out/IR/doubtful/PUP/suspended; questionable deliberately
  excluded, most questionables play).
- **Scales:** league cross-team mean and std per stat, so signals z-score
  differences instead of trusting raw gaps.

**Point-in-time:** `as_of` restricts the library to games completed before
that moment and to those games' plays — the store layer's golden rule,
honored here so the layer is backtestable without lookahead.

### 3.2 Signals (`signals.py`)

Each signal answers one narrow question and returns a score **aligned with
the bet** (positive = confirms the side being bet), or abstains (`None`).
Coin-flip differences read as ~0 by construction — everything is z-scored
against the league spread, the same `ADV` discipline as the deep-dive card.

| Signal | Markets | What it measures | Veto |
|---|---|---|---|
| `matchup` | spread/ML, totals | Net unit edge in σ: offense vs offense, defense-allowed vs defense-allowed (EPA when plays exist, scoring form otherwise). Totals score the combined environment. | — |
| `form` | spread/ML, totals | Recent net pts/gm vs the season baseline, per team; totals use the combined-scoring trend. | — |
| `rest` | spread/ML | Rest-day differential, `gap/7` capped; abstains under 3 days or when both sides are on opener-length layoffs. | — |
| `injury` | spread/ML, totals | Positionally weighted outs (QB 0.9, skill ~0.3, line ~0.25); sides score the burden differential, totals lean under on offensive outs and over on defensive outs. | **QB out on the picked side** — the ratings fit priced the healthy starter |
| `availability` | props | Is the propped player himself on the report? | **Player out** |
| `prop_matchup` | props | The opposing unit the stat runs through (pass D for passing/receiving, rush D for rushing) in σ; abstains for unmapped players — never guesses a team. | — |

Weather is deliberately absent: wind is already priced *inside* the
projection (`WeatherAdjustedModel`), and a signal repeating it would
double-count. Teammate outs are deliberately absent from prop scoring: they
cut both ways (a thinner offense scores less, survivors absorb the vacated
usage), so scoring them honestly needs player-level share data — see §6.

### 3.3 Conviction (`score.py`)

```
edge_score    = clip(edge / edge_reference, 0, 1)          # 0.05 edge ⇒ 1.0
context_score = Σ wᵢ·sᵢ / Σ wᵢ  over the signals that spoke  ∈ [−1, +1]
composite     = 0.4·edge_score + 0.6·(context_score+1)/2   ∈ [0, 1]
tier          = A (≥ 0.65) / B (≥ 0.45) / C, or X when any signal vetoed
```

Abstaining signals drop out of the weighting entirely — a bet judged by two
signals is not diluted by the four that had nothing to say. Default weights
(`DEFAULT_SIGNAL_WEIGHTS`): matchup 0.35, injury 0.25, form 0.25, rest 0.15;
props: availability 0.45, prop_matchup 0.35. All knobs live in `IntelConfig`
(blend, reference edge, weights, tier cuts, `veto_enabled`).

A game the context library cannot cover (unmatched team names) yields **no
verdict** rather than a fabricated neutral one — the runner reports those
bets as un-assessed, mirroring the slate's "unresolved, never guessed"
discipline.

### 3.4 Pick sets (`picks.py`)

`build_pick_sets` partitions convictions into Prime / Solid / Model-only /
Flagged, strongest first within each; `render_pick_sets` prints the argued
card; `intel_frame` flattens every verdict (bet identity, edge_score,
context_score, conviction, tier, recommended, rationale) into the parquet
that lands beside the slate.

## 4. Running it

Wired into `scripts/run_live_slate.py`, on by default:

```bash
python scripts/run_live_slate.py --league nfl --data datasets/nfl \
    --snapshot-file snap.json \
    --injuries-file artifacts/fp/fp_injuries_<ts>.parquet   # optional but recommended
# ... prints the intelligence card after the slates, writes intel_nfl_<stamp>.parquet
# --no-intel disables the layer entirely
```

- Without `--injuries-file`, the injury/availability signals abstain (stated
  in the log) — the layer still tiers on matchup/form/rest.
- The injuries snapshot is the `collect_fantasypros.py` artifact
  (`normalize_injuries` schema: `player_name/team/position/status/is_out`).
  This is the first production consumer of that collector.
- Best-effort like every surface after the game slate: any failure prints
  `intel layer skipped: …` and leaves the slate untouched.

## 5. What the layer is *not* claiming

- **The stat signals carry no measured edge — now verified.** The tier
  backtest ([`docs/BACKTEST_INTEL.md`](BACKTEST_INTEL.md)) replayed 15 NFL
  and 10 NCAAF seasons: context-confirmed picks did **not** outperform
  context-contradicted ones (NFL mildly inverted, not significant; NCAAF
  exactly flat). The expected null for public stats the close already
  prices. Tiers organize *evidence*; they do not add edge — which is
  exactly why the layer only annotates and vetoes rather than re-staking.
  The injury/availability vetoes are the untested (and most promising)
  channel: they carry the one input that is *not* in public season stats.
- **Unit stats are raw season means,** not opponent-adjusted ratings — the
  same numbers as the deep-dive card, chosen for explainability. The
  opponent-adjusted fit already lives inside the projection; the signal is
  a cross-check in the model's own currency, not a second model.
- **The veto is conservative by design.** A QB out flags every bet on that
  side even when the market has already repriced it. A flagged bet is
  "re-check the board before betting", not "the bet is wrong".

## 6. Next steps (each its own gated PR)

1. ~~**Tier backtest.**~~ **Done** — `velocity/backtest/intel_tiers.py`,
   results in [`docs/BACKTEST_INTEL.md`](BACKTEST_INTEL.md). Verdict: stat
   signals add no measurable edge over the EV gate (so no tier-conditioned
   staking); the untested injury channel is now the priority.
2. ~~**Injuries history.**~~ **Done** — two feeds, both wired:
   *Backfill:* `datasets/nfl/injuries.parquet` (nflverse official weekly
   designations, 2011–2025, 51.7k rows; `scripts/build_injury_history.py`,
   kept current by `refresh_datasets.py`) — the tier backtest now slices it
   per week (`injuries_for_week`), so the injury and QB-veto signals fire
   point-in-time and the channel is *measured*, not presumed
   (BACKTEST_INTEL.md addendum). *Forward:* daily FantasyPros snapshots
   (`collect-injuries.yml`, plus a Sunday pre-kick run); the live slate
   downloads the freshest one and passes `--injuries-file` automatically.
   Still open from this item: moving availability *inside* the prop model
   (`redistribute_shares`'s production caller) once player-week stats bank.
3. **Player-level form.** `normalize_weekly_stats` (nflverse) is ingest-ready
   but unbanked; committing a player-week table unlocks prop form signals
   (usage trend, not just the opposing unit).
4. **Opponent-adjusted signal units.** Swap `epa_form` raw means for the
   ridge fit's ratings in the matchup signal once the explainability story
   (showing σ *and* the rating delta) is worked out on the card — the
   backtest's caveat list names raw-mean context as a possible reason for
   the null.
