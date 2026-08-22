# Velocity — Edge Research: The Public Evidence and Our Roadmap

**Status:** Research synthesis (2026-08), from a six-track deep dive across
academic literature, documented professional records, and practitioner
sources. Every load-bearing claim carries a source; confidence is tagged
where it matters (well-documented / plausible / marketing-claim).
**Scope:** the four modeled sports (NFL, NCAAF, MLB, WNBA), the two
candidate sports (NBA, NCAAB), player props & pick'em, DFS (DK/FanDuel),
and the operational layer (staking, CLV, timing, venues).
**Companion to:** [`DESIGN.md`](DESIGN.md), [`WAGERING.md`](WAGERING.md),
[`INTEL.md`](INTEL.md), [`BACKTEST_INTEL.md`](BACKTEST_INTEL.md).

---

## 0. The one-page verdict

1. **The closing line of a liquid market is nearly unbeatable with public
   data — and that's fine, because profit doesn't live there.** Every
   audited public power rating loses at the NFL close (−2.5% to −6.7% ROI:
   538 Elo, QB-Elo, ESPN FPI over 8 seasons,
   [Robbins et al.](https://myweb.ecu.edu/robbinst/PDFs/Betting%20on%20FPI%20-%20DSI.pdf));
   NBA sides and the NCAA tournament test efficient; ML models lose to
   Vegas on margin MAE. The documented profit lives in **derivatives,
   props, low-attention slates (NCAAF/NCAAB/WNBA), timing, and price** —
   exactly the structure Velocity already targets.
2. **The realistic ceiling is known.** Liquid major sides/totals: **+1–4%
   ROI / 53–57% ATS** (Peabody's audited 55.4%, Buchdahl's +3.4% over
   20k bets, Data Golf's +1.8–3.8% on measured Pinnacle-vs-soft edges).
   Specialist niches (props, derivatives, golf-style outrights):
   **+5–10%**, at the cost of low limits and fast account death. Anything
   marketed above ~58% long-run is a tout. "70%+" does not exist.
3. **Our architecture is validated by the record.** Monte Carlo
   distributions → derivative pricing → de-vig → EV gate → fractional
   Kelly → CLV grading is, piece for piece, what Benter, Peabody,
   Voulgaris, and the Unabated school describe. The gaps are not
   architectural — they are (a) a few evidence-backed strategy layers not
   yet implemented, (b) measurement upgrades, and (c) the operational
   layer (venues, limits, timing) that code alone can't solve.
4. **The single best newly-found, peer-reviewed, directly-implementable
   edge:** NCAAF **team totals** are biased because scores are censored at
   zero — a lines-only strategy won **>55% over two decades**
   ([Arscott 2023, J. Sports Economics](https://journals.sagepub.com/doi/10.1177/15270025221148991)).
   We already price team totals off the sim.
5. **The most valuable negative results:** rest is now *overpriced* by the
   NFL market (true bye effect ≈ +0.3 pts vs ≈ +0.97 priced,
   [Lopez & Bliss 2024](https://www.frontiersin.org/journals/behavioral-economics/articles/10.3389/frbhe.2024.1479832/full))
   — our `RestAdjustedModel` adds +1.0 and should be re-swept; reverse
   line movement is formally unprofitable in CFB totals
   ([Springer](https://link.springer.com/article/10.1007/s12197-019-09479-3));
   steam-chasing is over; NBA sides are a news-speed game, not a modeling
   game; single DFS lineups have a median outcome of −100%.

---

## 1. Cross-sport doctrine (what the evidence says everywhere)

### 1.1 The market as prior, the close as yardstick

- Models that beat the close **blend market information in**: nfelo
  regresses ratings toward market-implied ratings and bets only at large
  divergence ([nfelo](https://www.nfeloapp.com/analysis/using-market-regression-to-improve-prediction-accuracy-in-the-nfl/));
  the Kaggle-winning NCAAB model's core insight was that the Vegas line
  itself is the best single predictor
  ([Lopez & Matthews](https://arxiv.org/pdf/1412.0248)). Velocity's
  `SlateConfig.model_weight` (market anchoring) exists and defaults to
  pure-model — the sweep to find the right blend is unfinished business.
- **CLV doctrine, quantified:** beating the devigged close predicts
  realized profit nearly 1:1 (Data Golf: 4,803 bets, +1.84% actual vs
  +2.28% expected; [source](https://datagolf.com/how-sharp-are-bookmakers));
  CLV-based skill tests reach significance in **~50 bets vs ~1,000+** for
  P/L ([Buchdahl](https://www.sportstradingnetwork.com/article/using-the-closing-line-to-test-your-skill-in-betting/)).
  **But CLV is only trustworthy where the close is efficient** — Captain
  Jack Andrews: "CLV doesn't mean anything in props"; same caveat for
  WNBA and early-season NCAAB
  ([Unabated](https://unabated.com/articles/getting-precise-about-closing-line-value)).
  Velocity should carry a per-market *CLV-trustworthiness* flag: grade
  majors on CLV, grade props/WNBA on longer-window P/L + process.
- Devig against a **market-making** book's close (Pinnacle; Circa for
  football) — grading against a soft book's close overstates EV by ~4.5%.

### 1.2 Staking science (upgrades to our Kelly stack)

- **Benter's factor-of-two rule:** overestimate your edge by 2× and full
  Kelly turns growth negative ([Benter 1994](https://gwern.net/doc/statistics/decision/1994-benter.pdf)).
  Our ¼-Kelly + calibrated shrink already respects this; the shrink sweep
  discipline (WAGERING.md §3) is the right mechanism — keep it.
- **Drawdown-constrained Kelly** (Busseti/Ryu/Boyd,
  [arXiv:1603.06183](https://arxiv.org/abs/1603.06183)): convex program
  with an explicit P(drawdown > X) constraint; **dominates fractional
  Kelly at equal drawdown risk**. Natural upgrade for portfolio phase W2.
- Simultaneous correlated bets need joint treatment — our
  `portfolio.size_portfolio` correlation de-scaling is the right shape;
  wiring it (W2) is confirmed as a real gap, not a nicety.

### 1.3 Line shopping, timing, and venues

- Line shopping is worth **+1–2% ROI** — comparable to the entire model
  edge in majors. It is the highest-ROI infrastructure investment
  ([BettingUSA](https://www.bettingusa.com/sports/line-shopping/)).
- **Timing beats picking:** the same number is worth more early
  (NCAAF Sunday–Monday openers at low limits; MLB overnight lines) or on
  news (injury windows). NFL is efficient by midweek.
- **Account limiting is the tax on winning:** operators identify ~90% of
  winners within ~20 bets (CLV screening); post-flag limits run
  ~$50–$200 ([How Gambling Works](https://howgamblingworks.substack.com/p/the-truth-about-limits);
  [Kaunitz et al.](https://arxiv.org/abs/1710.02824) — the inefficiency
  persists *because* exploiting it kills the account). Durable venues:
  **Circa** (welcomes winners, $50k NFL limits), and the **2026 CFTC
  exchange class** — Novig and ProphetX approved mid-2026, Kalshi in most
  states with live litigation. Strategy: segment the book of business —
  (a) promo EV (finite), (b) soft-book pick-offs (weeks-to-months
  lifetime), (c) origination edge at winner-tolerant venues (the durable
  business).
- **Kill-signal discipline:** per-strategy CLV control charts; a strategy
  whose devigged CLV goes flat is dead regardless of recent P/L. Cap
  backtest variant counts (~45 per 5 years of data before overfit is
  near-certain, [Bailey/López de Prado](https://www.davidhbailey.com/dhbpapers/overfit-tools-at.pdf))
  — our multiple-comparisons rules (DESIGN §7.3) now have a number.

---

## 2. The four modeled sports

### 2.1 NFL — CLV practice, plus two real layers

Confirmed dead: sides/totals model-only edge (every public model loses at
the close — our own BACKTEST_NFL.md agrees); RLM/steam; trend angles.

Worth building:
- **Wind ≥15 mph totals**: threshold effect at 15 mph; totals 2–4 pts
  lower at 15–20 mph; persists even after line movement
  ([Pinnacle](https://www.pinnacle.com/betting-resources/en/football/nfl-points-totals-and-the-effect-of-the-weather/5zn2av8e4w2g9sf9);
  academically [Borghesi](https://www.researchgate.net/publication/24071150_Weather_Biases_in_the_NFL_Totals_Market)).
  We wired Round-5 wind constants into the *projection* — the research
  says the residual edge is betting these **near kickoff** when the
  forecast is certain; a slate-timing feature, not just a model feature.
- **Rest re-sweep (fade the rested?):** market prices bye at ~+0.97, true
  effect ~+0.3. Our +1.0 bye adjustment replicates the market's
  overpricing. Lab experiment: sweep bye ∈ {0, +0.3, +1.0} and test a
  *fade-the-rested-team* residual.
- **Key numbers:** 3 ≈ 15% of margins, 7 ≈ 9%; buying -3.5→-3 is the one
  standard +EV buy. Line-shopping logic should weight points-through-3/7
  explicitly in NFL only (NCAAF: 3 is just 9.5% — shop price, not hooks).
- **QB on/off ≈ up to 7 pts** — our QB-adjusted ratings + (future) injury
  veto is the defensive requirement; being wrong here is how books pick
  models off.

### 2.2 NCAAF — our best sport, now with a second proven edge

- **Team totals (Arscott censoring bias)** — peer-reviewed >55% over two
  decades, implementable from lines alone, better with our sim's
  distributions (we can price P(team score) natively including the
  zero-censoring the market mishandles). **Top build priority.**
  Requires team-total lines: The Odds API carries them live; historical
  via its archive tier.
- **Totals disagreement filter** — already wired (≥4 pts, 52.6–53.4%);
  the research confirms it as the right family (attention scarcity).
- **Early-week openers**: Circa posts CFB Sunday 11am at $3k limits;
  "small-conference openers are the softest numbers of the week."
  Operational: run the NCAAF slate **Sunday night/Monday**, not Friday.
  Our collector cadence should match.
- **Priors**: returning production >60% of SP+ projection accuracy;
  transfer portal handled by folding transfers' production at half-credit
  for level jumps ([ESPN/Connelly](https://www.espn.com/college-football/story/_/id/48259759/college-football-returning-production-2026-notre-dame-texas)).
  Our `priors.py` scaffolding awaits exactly this data (CFBD carries
  returning production).
- TV-game over-bias, holdover bias (prior-year top-10 overvalued in
  openers), climate mismatches: plausible secondary angles, sweep-grade.

### 2.3 MLB — derivatives validated; the ABS regime change is the 2026 story

- **Our F5/NRFI derivative pricing is textbook sharp practice** (price
  derivatives off the fair main number, attack low-attention markets —
  the entire Unabated toolset productizes this). Edge hierarchy: props/
  alt lines > team totals/NRFI > F5 > full game, inversely ordered to
  limits.
- **ABS challenge system (2026)**: zone −11%, BB/game 3.1→3.7 (+15–19%),
  K% flat, catcher framing devalued
  ([ESPN tracker](https://www.espn.com/mlb/story/_/id/48305211/2026-mlb-abs-challenge-system-tracker-team-player-rankings)).
  Consequences for us: (a) pre-2026 umpire K/BB factors are stale;
  (b) K-prop models keyed to old pitch-count/leash norms overproject IP;
  (c) NRFI base rates shift with more baserunners; (d) books anchored to
  old baselines are the near-term opportunity. **Re-estimate everything
  umpire/framing-adjacent on 2026-only data.**
- **Lineup timing**: MLB itself confirmed lineup info moves lines
  ([ESPN 2019](https://www.espn.com/chalk/story/_/id/26156818/mlb-office-wants-lineups-info-made-public));
  our W5 lineup-release repricing plan is confirmed as the right call.
- Projection systems: differences are marginal; **ensembles win**
  (FanGraphs' own reviews). Blend, don't bet on one system. Game-level
  accuracy is unpublished — measuring ours vs closes is a private edge.
- Academic support: MLB line movement shows negative autocorrelation
  (books overreact — don't chase steam; consider buy-backs)
  ([Management Science](https://pubsonline.informs.org/doi/10.1287/mnsc.2022.00456)).

### 2.4 WNBA — credibly soft, decaying, ops-shaped

- Softness is real but the window is closing (handle doubled 2024).
  Documented edges, in order: (1) **the day-before 5pm injury report +
  late scratches** — recreational books sit on stale numbers; (2) props
  priced from naive averages with ~2–3 point cross-book dispersion;
  (3) schedule-density unders (44 games, more B2Bs — note pre-2024
  travel effects are stale post-charters); (4) early-season anchoring to
  prior-year ratings.
- Build: WNBA injury-report ingest (5pm ET day-before cadence — pairs
  with the existing injuries-collector priority), a schedule-density
  feature (B2B/3-in-4 from our games data — trivial), and prop line
  shopping across books. CLV is *not* trustworthy here; grade on P/L.
- Data: wehoop/sportsdataverse (free, includes officials + odds
  endpoints), Across the Timeline (history to 1997), official injury
  report page.

---

## 3. The two candidate sports

### 3.1 NCAAB — the priority expansion (mirror of our NCAAF thesis)

- **Why:** inefficiency concentrates where pregame information is scarce
  ([Colquitt et al. 2001](https://www.researchgate.net/publication/4762897_Testing_Efficiency_Across_Markets_Evidence_from_the_NCAA_Basketball_Betting_Market))
  — 360 teams, 5,000+ games, low-majors, November. Historically
  documented: big/home underdogs profitable
  ([Paul & Weinbach](https://link.springer.com/article/10.1007/BF02761584)),
  unders on high totals 52.7% over a decade, huge favorites under-cover
  (Wolfers' 44k-game sample). The tournament is efficient — the soft part
  is November–February low-majors.
- **Architecture:** ridge-adjusted possession efficiency (pace ×
  offensive/defensive rating) — structurally our NCAAF fit with
  possessions instead of plays — plus Torvik-style recency decay and a
  returning-production preseason prior, feeding the same sim/EV/Kelly
  stack. Kenpom/Torvik are the *market's own inputs*; edge comes from
  what they omit: injuries/rosters (kenpom is roster-blind), per-venue
  HCA (2.5–6+ pts spread; altitude), 1H/derivative pricing, and speed.
- **Leak-free backtesting exists for free:** Bart Torvik's *timemachine*
  daily-ratings archive (as-of-date ratings,
  [data page](http://adamcwisports.blogspot.com/p/data.html)) +
  sportsbookreviewsonline open/close archives (~2007+). Budget: ~$0
  to prototype; kenpom API ~$25/yr if wanted.
- Expectation discipline: raw model ties the market; 53–55% ATS long-run
  is a strong result; internal backtests >56% at volume are presumed
  leaky.

### 3.2 NBA — enter through totals and props, not sides

- Sides at close are near-unbeatable (market MAE ≈ 8; public ML models
  land at 9+). Sides opportunity is **news-speed** (star scratch = 4–5
  pts in minutes) — an ops race we shouldn't enter first.
- **Totals**: the best-documented NBA inefficiency is **early-season
  totals bias** (week-1 unders 58.2%, ~11% per-game returns in the
  2007 study — dated, must re-test)
  ([Finance Research Letters](https://www.sciencedirect.com/science/article/abs/pii/S1544612307000177));
  plus pace-interaction modeling and referee crews (published daily;
  foul-heavy crews ≈ 8–9 pts of FTs).
- **Props**: role-player rebounds/assists are the documented soft spot;
  minutes projection is the whole game; blowout minute-curtailment breaks
  naive models (start worrying at 7+ spreads). DARKO (free) ×
  minutes → team efficiency is the published pattern; RAPTOR historical
  CSVs are free training data.
- Rest raw flags are priced; the residual is *interactions*
  (rest × travel × altitude × opponent rest) — exactly our intel-layer
  signal family, but as model features this time.

---

## 4. Props & pick'em (the highest-EV, lowest-limit inventory)

- **Why props stay soft:** no sharp origin price (even Pinnacle
  outsources props; limits ~$250–500), hold 5–10%+ vs ~4.5 mainlines,
  stale repricing after news, and **median-vs-mean confusion** — books
  price the median; skewed distributions make the mean higher; naive
  projection users over-bet overs, and distribution-aware bettors price
  tails/alts correctly. **Our correlated Monte Carlo prices medians and
  tails natively — this is our structural advantage; protect and extend
  it** (it is literally the documented Peabody method,
  [The Ringer](https://www.theringer.com/2022/09/21/gambling/gamblers-super-bowl-props-rufus-peabody-nfl)).
- **The best timing edge in the space:** second-order injury
  redistribution — the star's own props reprice instantly; **teammates'
  props lag 30–90 minutes**. Requires the injuries feed + fast re-run:
  our W5 repricing plan extended to props.
- **Pick'em math validated** (cross-confirmed breakevens): PP 6-flex
  **54.21%**/leg and UD 5-pick **54.9%**/leg are the low bars; 2–3 leg
  powers (57.7–58.5%) are the trap. At 56% legs, UD 5s ≈ +10% ROI
  ([ETR](https://establishtherun.com/how-to-beat-pick-em-on-underdog-fantasy/)).
  Two engine to-dos: (a) **worst-case devig** (min EV across
  multiplicative/power/Shin) when deriving leg probabilities — suppresses
  false positives near thresholds; (b) verify per-platform same-game
  payout reductions — PrizePicks now taxes correlated combos, so the
  correlation subsidy must be checked per-slip, not assumed.
- Nobody publishes audited projection-vs-closing-prop accuracy. Banking
  our own prop-close archive (already doing) and measuring is a real
  proprietary asset.

---

## 5. DFS (DraftKings / FanDuel)

- **Economics:** ~15% rake in flagship GPPs, ≤10% in 50/50s/small
  leagues; ~1.3% of players won 91% of profits (top pros: huge volume at
  ~6–26% ROI); 89.3% of players have negative lifetime returns (DK's own
  disclosure). **Contest selection is the first edge**: single-entry and
  3-max contests, small fields (<3k), low buy-ins, 20-max mid-prize
  contests; avoid multi-entry double-ups and the Milly Maker except as
  small "fun" allocations ([ETR game selection](https://establishtherun.com/levitans-dfs-game-selection-which-contests-to-play/)).
- **Peer-reviewed strategy anchors:**
  [Haugh & Singal (Management Science)](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3393127)
  made **+350% real-money in top-heavy GPPs** while their cash strategies
  lost — model the field (Dirichlet regression on ownership), simulate
  contest EV, exploit the top percentiles.
  [Hunter/Vielma/Zaman](https://arxiv.org/abs/1604.01455): single lineup
  median = **−100%**; 100–200 diversified lineups with **pairwise overlap
  caps of 4–7**, stacking constraints, and blended projections (+ Vegas
  lines) shift the whole distribution.
- **Winning-lineup constraints (452 Milly top-10s,
  [ETR](https://establishtherun.com/levitan-winning-draftkings-milly-maker-trends/)):**
  QB + 2 teammates + 1 bring-back (41% of winners vs 29% of field);
  cumulative ownership **100–125%** (hard ceiling 150%); 2–3 sub-5%
  players; leave $300–$1,000 salary unspent (full-cap lineups duplicate
  460×+); cap player exposure 50–70%. MLB: 5-3/4-2 consecutive
  batting-order stacks (mandatory). NBA: minutes news + **late swap** is
  the edge; stacking optional.
- **For our optimizer** (`build_dfs_lineup.py` + sim): the published
  upgrade path is scoring candidate lineups by **simulated contest EV
  against a modeled field** instead of raw projected points — our
  correlated sim is the hard part and already exists; the field model
  (ownership projections + our own contest-result CSVs) is the missing
  input. Site tuning: DK full-PPR + bonuses → volume/yardage; FD half-PPR
  no bonuses → TD-dependent; FD MVP slots carry no salary premium in
  showdowns (always the top projected scorer).
- **Bankroll:** ≤10% of bankroll per slate; tail-driven P/L; a full NFL
  season is statistically almost nothing — judge by process.

---

## 6. What we will NOT build (evidence-based negative space)

| Idea | Why not |
|---|---|
| RLM / steam chasing | Formally unprofitable in CFB totals; modern repricing killed the chaser's window; head-fakes are engineered |
| NBA sides model | Near-efficient at close; the edge is news latency, an ops race we'd lose |
| NCAA tournament betting focus | Tested efficient 1996–2019; softness is November low-majors |
| Cash-game DFS grinding | 56–58% required vs a field with identical tooling; the peer-reviewed money is in top-heavy |
| Trend angles ("division dogs 23-6") | Mined small samples; documented decay |
| A single "best" projection source | Ensembles beat every individual system, everywhere it's been measured |
| Chasing >58% win rates | No credible documentation of anyone sustaining it at -110; treat any internal result >56% at volume as a leak until proven otherwise |

---

## 7. Prioritized roadmap (evidence × fit × cost)

> **Implementation status (2026-08):** P0 #1 (team totals end-to-end +
> censoring study — gate defaults off pending banked posted closes), #2
> (rest re-sweep — clean negative, +1.0 stays; MODEL_LAB Round 8), #4
> (worst-case devig + same-game caps) shipped. #3 re-confirmed Round 3's
> blend result via the lab. #5 (ABS): no umpire/framing factors exist in
> the model yet — nothing to re-estimate; noted for the MLB factor build.
> P1: #7's sizing half (portfolio-sized combined card), #8's trust flags +
> FDR helper shipped; #6 (injuries banking) and #9–10 remain. P2: #11
> phase N1 (Torvik ingest + timemachine client, BUILD_NCAAB.md) shipped.

**P0 — sweep-grade experiments on data we already have (each a lab PR):**
1. NCAAF **team-totals censoring strategy** (Arscott) — implement in the
   sim path; backtest lines-only variant vs sim-priced variant.
2. **Rest re-sweep** — bye +1.0 vs +0.3 vs 0; test fade-the-rested
   residual (our current constant matches the market's documented
   overpricing).
3. **Market-anchoring sweep** — `model_weight` grid on banked archives
   (the nfelo lesson); promote from paper-CLV evidence per WAGERING
   discipline.
4. **Pick'em worst-case devig** — add power/Shin + min-EV mode to
   `fair_leg_prob`; verify PP same-game payout reductions in `PAYOUTS`.
5. **MLB ABS re-estimation** — 2026-only umpire/K-prop/NRFI factors;
   deprecate framing inputs.

**P1 — build items already on the roadmap, now evidence-confirmed:**
6. **Injuries history banking** (INTEL.md's headline) — now triple-
   confirmed: the intel veto channel, WNBA's #1 edge, and the props
   second-order redistribution window all depend on it.
7. **W2 portfolio sizing** upgrade candidate: drawdown-constrained Kelly
   (Busseti-Boyd) instead of plain fractional.
8. **W3 monitor** additions: per-market CLV-trustworthiness flags; CLV
   control charts as kill signals; FDR (Benjamini-Hochberg) across sweep
   families with the ~45-variant budget.
9. **Line shopping breadth** — more outs is +1–2% ROI; slate timing to
   market softness (NCAAF Sunday night, MLB overnight, props on news).
10. **DFS contest-EV optimizer** — field model (Dirichlet on ownership)
    + sim-scored lineups + overlap/stack/ownership/salary constraints
    from §5; contest-selection policy in the runner.

**P2 — new verticals (each a BUILD_*.md-style phased plan):**
11. **NCAAB** — ridge possession-efficiency model; Torvik timemachine +
    sportsbookreviewsonline backtests; venue-HCA and derivative layers;
    November/low-major selectivity. Fits the NCAAF playbook and the
    existing stack; ~$0 data cost.
12. **NBA totals + props** — DARKO-pattern player layer × minutes; re-test
    early-season totals bias on modern data; referee-crew feature;
    role-player props. Sides stay out.
13. **Venue strategy** (operational, not code): segment promo EV /
    soft-book / origination books; track the 2026 CFTC exchange docket
    (Novig, ProphetX, Kalshi) as the durable home for origination edge.

---

## 8. Data source shopping list (new, Python-usable)

| Need | Source | Cost |
|---|---|---|
| NFL injuries/depth/snaps/participation | nflverse `load_injuries`/`load_depth_charts`/`load_snap_counts`/FTN charting | free |
| NFL/NCAAF historical odds | Aussportsbetting xlsx, Kaggle spreadspoke, CFBD `/lines`, SBR archives | free |
| Intraday/props odds history | The Odds API historical (props since 2023-05) | $99/mo when needed |
| NFL referees | nflpenalties.com, Football Zebras | free |
| Weather (curated NFL) | Tom Bliss dataset; Open-Meteo/NWS APIs | free |
| MLB umpires | UmpScorecards (+API); Baseball-Reference ABS tracker | free |
| MLB pitch/framing | Baseball Savant CSV / pybaseball; framing: deprecate | free |
| WNBA everything | wehoop/sportsdataverse + wnba.com injury report + Across the Timeline | free |
| NCAAB ratings/backtest | **Torvik endpoints + timemachine**; kenpom API; EvanMiya | ~$0–50/yr |
| NCAAB/NBA PBP | hoopR/sportsdataverse-py parquet releases; nba_api; CBBpy | free |
| NBA injuries history | prosportstransactions.com; official report parser (`nbainjuries`) | free |
| NBA player impact | DARKO (live), RAPTOR archive (training data), nbarapm.com | free |
| DFS salaries/contests | DK unofficial API (`getcontests`/`draftables`); own contest-result CSVs; FantasyPros history | free |
| Ownership projections | FTN / Fantasy Team Advice (free tier); aggregate multiple | free–cheap |
| Injury news speed | RotoWire API (via OpticOdds resale), curated X lists | cheap |

---

## 9. Honest caveats about this research

- Practitioner ROI figures (Carty's 13%, tout claims) are self-reported;
  the audited anchors are Buchdahl (+3.4%/20k bets), Data Golf
  (+1.8–3.8%), Haugh-Singal (+350% top-heavy DFS on small stakes), and
  the academic strategies (Arscott >55%, Kaunitz +3.5%-class).
- Published anomalies decay after publication — every P0 item gets our
  own walk-forward re-verification before a dollar moves (the NCAAF
  totals filter set the pattern: re-measure, then wire).
- Several key studies are dated (early-season NBA totals 2007, NCAAB
  underdogs 1996–2004, CFB favorites 1985–2003). Direction is the
  hypothesis; our backtest is the evidence.
- Correlation values, rake tables, and payout structures drift — footnote
  the retrieval date (2026-08) and re-measure at implementation time.
