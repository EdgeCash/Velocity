# Velocity — YouTube Content Pipeline

**Status:** Specified, not built
**Code:** `velocity/report/` (existing renderers), `velocity/report/video.py` (planned)
**Companion to:** [`docs/INTEL.md`](INTEL.md) (the conviction score this spec
selects on), [`docs/SITE.md`](SITE.md) (the card room these formats reuse),
[`docs/LAUNCH.md`](LAUNCH.md) (the daily loop they hang off).

---

## 1. What this is, in one paragraph

The daily run already produces broadcast-grade graphics: one all-inclusive
sheet per game, prop strips, DFS cards, and next-morning Sim Checks. This
spec turns a **selected subset** of that output into video. The governing
decision: **do not narrate every matchup.** Publish the board's best plays,
the board's best props, and one DFS build — chosen by the conviction score
the intel layer already computes — and let the selection itself be the
content. Everything downstream (script, render, upload cadence, grading)
follows from that one choice.

## 2. Why selection, not volume

Three reasons, in descending order of importance.

**It is the only version that survives YouTube's Inauthentic Content
policy.** Fourteen near-identical sheets read by a synthetic voice is the
canonical pattern that fails Partner Program review — same template, no
meaningful commentary, mass-produced. One video that chooses three plays out
of forty is a different object: the choosing *is* the original contribution,
and `Conviction.rationale()` supplies per-play evidence lines that differ
game to game by construction.

**It creates the accountability loop, which is the moat.** A channel that
posts every game can never be graded — "we had that one" is what every picks
channel says. A channel that commits publicly to a small, named set gets
graded on exactly that set by machinery the repo already has
(`report/scorecard.py:79` `grade_slate`, `report/daily_record.py`). The next
morning's Sim Check is then a receipt against an unhedgeable claim. Nobody
without a model can copy that format.

**Production cost collapses.** ~14 renders/day becomes ~7 units, which is
what makes a daily cadence survivable inside the existing `live-slate`
workflow.

## 3. The selection contract

Three rules, mirroring the intel layer's own contract. These are what keep
the format honest when the board is thin.

1. **The gate is fixed; the count is not.** The format is *"tier A or B,
   above `min_edge`, at most three"* — never *"three plays."* A daily video
   that requires three plays will pull C-tier or flagged bets into the slot
   to fill it, which is precisely what `velocity/intel/score.py` was built to
   prevent ("it can never promote"). Some days this yields three, some days
   one, some days zero.
2. **Zero is a publishable result.** `caption()` already emits *"no edge on
   this board"* (`velocity/report/social.py:523`). A betting channel that
   says "nothing today" is instantly credible and is the one thing
   competitors will not imitate. Ship it as a 30-second Short.
3. **Selection never re-ranks the model.** The video layer reads
   `build_pick_sets()` output in the order it arrives (`intel/picks.py:49`,
   already sorted by `score` descending) and slices. It does not apply its
   own scoring, weighting, or "what will play well" adjustment. If a play is
   boring and top-ranked, it leads.

## 4. What feeds what

The repo contains two different objects for several of these, and picking the
wrong one silently changes what you are held to. This table is the
authoritative mapping.

| Format | Source of truth | Selection rule |
|---|---|---|
| Game plays | `intel.picks.build_pick_sets()` → tiers A/B | Highest `Conviction.score` per `game_id`, then top 3 across games |
| Props | `wagering.props_slate.build_prop_slate()` → intel-tiered | Top 3 by conviction; **not** `social._select_watch()` |
| DFS | `dfs.pipeline` cash lineup | The single solved lineup, or a 3–4 player core |
| Sim Check | `report.sim_check.build_sim_checks()` | **Every** play published the day before — no editorial gate (§14) |

Three traps this table closes:

**Props: two objects, one word.** `social._select_watch()`
(`social.py:317`) ranks *display facts* by distance from 50% at the board
line — it is card decoration and never passed an EV gate.
`props_slate._best_prop()` (`props_slate.py:141`) returns the highest-EV
qualifying opportunity per (player, market, side) and flows through the intel
tiering. **Video uses the second.** Using the first would quietly turn "top 3
props" into "three interesting numbers," which grades very differently when
you are being held to it publicly.

**Game plays: dedupe by game.** `wagering/portfolio.py` already de-scales
correlated bets inside a correlation group (`group_correlation: 0.5`,
`group_cap_fraction: 0.10`) because it knows two bets in one game are not
independent. Video needs the same discipline for an editorial reason: three
plays in one game is one story, not three. Take the highest-conviction play
per `game_id`, then the top three across games.

**DFS: publish the cash build, not the GPP portfolio.** This is the one
format with a direct strategic cost. Publishing the optimal tournament lineup
raises its duplication rate in the exact contests you are entering — you
would be paying for reach with your own EV, and `velocity/dfs/gpp.py` already
documents that full-cap builds "duplicate hundreds of times." Publish the
cash/single-lineup build or a 3–4 player core; keep the `gpp.py` portfolio
behind the site. That also gives the channel a natural funnel instead of
giving away the highest-value artifact for free.

## 5. Formats and cadence

Bundling all seven units into one video is wrong: the three verticals have
different shelf lives. DFS dies at lock, props travel best standalone, game
plays carry the long-form. One render job, four outputs, no overlap in
half-life.

| Format | Length | Frame | Fires |
|---|---|---|---|
| Flagship daily | 5–8 min | 1600×900 | after the slate build |
| Prop Shorts (2–3) | ~20 s | 1080×1920 | after the slate build |
| DFS Short | ~30 s | 1080×1920 | timed to slate lock |
| Sim Check Short | ~25 s | 1080×1920 | next morning, post-grading |

The flagship is the YPP-eligible unit — it is where genuine commentary lives
and it is the one that should carry a human voice. The Shorts are the
discovery surface. The Sim Check is the one that compounds.

## 6. Assets that already exist

Almost nothing here needs new design work.

* **`report/social_png.py` renders 1600×900** — already YouTube's native
  landscape frame, no re-layout.
  **But** its type scale fails the measured legibility floors (§12): the
  `EDGE` chip renders at cap-height 4.7% of frame height, which is decoration
  tier — invisible at every feed size. The most important number on the card
  is currently unreadable where it matters.
* **`report/outlook_png.py` renders 1080×1350** — one crop from a Short.
* **`social.caption()`** (`social.py:523`) emits post copy "stated as fact,
  no imperatives." That docstring is a policy asset, not just a style note:
  it is what keeps scripts clear of the promotional framing that draws
  gambling-policy enforcement. Preserve it verbatim into video scripts.
  One caveat from §14: it leads with the projected score and fair line as
  facts, and the uncertainty research says a drawn mean/median mark measurably
  biases viewers toward discounting uncertainty. The *sentence* is fine; the
  *graphic* must not give those numbers a mark.
* **`Conviction.rationale()`** (`intel/score.py:94`) returns the strongest
  evidence lines, vetoes first — the spine of the flagship's narration.
* **`social.distributions_frame()`** (`social.py:501`) persists the full
  unfolded pmf per game. This is the most animatable asset in the repo: the
  Monte Carlo distribution filling in frame by frame with the market's number
  dropping onto it is a visual no competing channel can produce.
* **`report/sim_check.py`** + `render_sim_check()` already pin the actual
  result on the pregame distribution with percentile as the hero number.

## 7. Build gaps

```
selection (§4)  →  script (Claude, per format)  →  frames  →  ffmpeg  →  upload
     ✅ exists          planned                    planned    new dep    new
```

* `velocity/report/video.py` — pmf animation frames + card-to-clip
  composition. Offline-testable against `tests/fixtures/` like every other
  renderer; deterministic under `velocity/util/seed.py`.
* `scripts/render_video.py` — CLI wrapper (file IO only, per repo convention).
* **ffmpeg** — not currently a dependency. Runtime-only, not needed by the
  offline test suite.
* **YouTube Data API v3 upload** — refresh token as an Actions secret. Quota:
  uploads cost 1,600 units against a default 10,000/day, so ~6 videos/day
  before a quota-increase request. The cadence in §5 fits under it.

## 8. Policy posture

Ranked by actual risk, not by how alarming it sounds.

1. **Gambling monetization will be constrained — but not by the mechanism
   originally written here.** The advertiser-friendly guidelines
   (`support.google.com/youtube/answer/6162278`) contain **no gambling section
   at all**; gambling is handled through Community Guidelines
   (`answer/9229611`) as age-restriction and removal, not through
   ad-suitability. Sports betting is explicitly *excluded* from the
   online-casino age restriction, so the category itself is permitted. Expect
   ad-suitability friction anyway, and do not plan on full ad rates: AdSense
   is not the prize. Judge the channel as a funnel to the site.
   Hard rules, both [DOC]: never facilitate access to a non-certified book —
   the Nov 2025 update extends this to "URLs, embedded links, **logos, verbal
   mentions and visual displays**" — and never promise guaranteed returns.
   See §15 for the generated blocklist.
2. **Inauthentic content** — the real demonetization vector, and the reason
   for §2 and §3. Mitigated structurally by selection plus per-play
   `rationale()`, not cosmetically.
3. **Team logos.** `report/assets.py:23` hotlinks
   `a.espncdn.com/i/teamlogos/...`. Trademarks, not Content ID — low
   probability of a manual claim, non-zero, and trivially avoided by falling
   back to the team-color blocks already rendered. Worth doing for video,
   which is far more visible than the card room.
4. **Broadcast footage: none, ever.** The repo uses zero game video or
   audio, which removes the leading cause of sports-channel strikes outright.
   That advantage is structural — do not trade it away for B-roll.

## 9. Phasing

1. **Sim Check Shorts.** Lowest policy exposure (a retrospective accuracy
   claim, not a pick), and the format the architecture uniquely enables.
   Proves the render path end to end.
2. **Flagship daily long-form.** Carries YPP eligibility. Human voice or
   human approval on the narration — the fully hands-off version is the one
   that gets demonetized.
3. **Prop and DFS Shorts at volume.** Gate this on how (1) and (2) perform;
   it is the step most exposed to the inauthentic-content policy.

---

# Part II — The visual spec

Added after four parallel research passes (Aug 2026). Raw reports, with full
citations and the scripts that produced the measurements, are archived
alongside this doc's PR: shorts legibility, competitive teardown, thumbnail
packaging, animated uncertainty.

## 10. Evidence convention

Every claim below carries one of these, and the label is load-bearing:

| Tag | Meaning |
|---|---|
| **[DOC]** | Official platform documentation |
| **[MEAS]** | Genuinely measured — a published study, or our own measurement |
| **[PRAC]** | Named practitioner with a track record, unpublished |
| **[FOLK]** | Creator folklore with no traceable measurement |

**Standing rule: [FOLK] never enters a spec.** All four researchers
independently hit the same wall — the first page of results for every
packaging query is AI-generated content farms manufacturing precise-sounding
statistics with no methodology, sample size, or date, contradicting each other
page to page ("82% of mobile users are on dark mode", "dark thumbnails gain
15% visibility", "65% drop off in 3 seconds"). None of it is in this spec.
Where no primary source exists, this doc says so rather than borrowing a
number. **The only numbers permitted to drive a design decision are [DOC],
[MEAS], or our own Analytics.**

## 11. What the research changed

**The graphic is not the growth lever.** [MEAS] In betting YouTube, graphics
sophistication is inversely correlated with audience size: the picks channels
running cheap production (PickDawgz 278K, WagerTalk 252K, Calling Our Shot
221K) are 3–8× larger than every design-invested operation (Action Network
79.8K on a real-time Singular.live overlay pipeline, VSiN 34.2K, Odds Shark
30.1K). The two biggest sports-analytics channels on the platform —
Thinking Basketball (672K) and Jon Bois / Secret Base "Chart Party" — win on
narration and narrative; Chart Party's charts are built in Google Sheets.

Nothing in Google's own ABCD creative guidance rewards graphic polish; it
rewards framing, pacing, surprise, and audio [DOC].

**Consequence, and it governs Part II:** stop spending on visual
sophistication once the legibility floors in §12 are cleared, and spend the
remainder on the script and the audio. The card's jobs are (a) shareable
object on X/IG, (b) thumbnail source, (c) trust artifact — not reach.

**The one differentiating graphic.** [MEAS] Frequency framing — quantile
dotplots, icon arrays, animated draws — measurably beats densities,
intervals, and text for non-technical people making threshold decisions. And
no sports betting outlet uses it: every model source found expresses Monte
Carlo as prose plus a single percentage. Verified white space, and we hold the
distribution. §14 is the spec.

## 12. The visual system

### Type: measured floors, not preferences

[MEAS] Barlow Condensed Bold cap height = **0.700 × font size**.

**Thumbnails** — tiers as cap-height fraction of frame height, from a rendered
ladder downsampled to real feed widths:

| Tier | Cap ≥ | Font ≥ | Survives to |
|---|---|---|---|
| 1 — hero | 17% H | 24.4% H | 120px (everywhere) |
| 2 — support | 13% H | 18.6% H | 168px (suggested rail) |
| 3 — detail | 7.8% H | 11.1% H | 246px+ |
| decoration | < 5% H | — | never load-bearing |

At 1920×1080 that is fonts of **264 / 201 / 120**, decoration ceiling ~77.
Word budget follows arithmetically: **3–6 words, at most 2 type sizes, plus
one hero numeral.** Corroborated by reading text off 24 sampled competitor
thumbnails — median 3 words.

**Video** — the binding constraint is 360-wide transcode survival (YouTube
360p ≈ 1 Mbps, 4:2:0), cross-checked against ANSI/HFES 16-arcmin and FAA
20-arcmin cap-height minima and Netflix's 42-characters-across rule. They
converge: **absolute floor 48px (0.025 H) at 1080×1920; captions 72px; hero
300px.** **Barlow Condensed is forbidden below 100px** — it closes up in the
downscale. Below that, switch to `Barlow-Bold` (already vendored in
`assets/fonts/`). Two-face system, not one.

[MEAS] Downscaling to 120×68 retains **96%** of the L\* contrast range. So
"thumbnails lose contrast on mobile" is false — global contrast survives;
*stroke-level detail* dies. It is a type-size problem, not a contrast
problem. This is why logos survive and small text does not.

### Palette: two computed failures, and the fix

Both were found independently by two researchers using different methods,
arriving at the same numbers.

| Pair | Contrast | Needs | Verdict |
|---|---|---|---|
| ground `#0b0f14` vs YouTube dark UI `#0f0f0f` | **1.00:1** | silhouette | fails on thumbnails |
| model `#0d9488` vs actual `#d97706` | **1.18:1** | 3:1 (WCAG 1.4.11) | fails everywhere |
| win `#22c55e` vs loss `#ef4444` | 1.65:1 (1.34:1 deuteranope) | 3:1 | fails |
| panel `#131a23` vs ground | 1.10:1 | — | invisible on video |
| `#ffffff` on `#3ddad0` chip | 1.73:1 | 4.5:1 | fails |

The model/actual failure is the important one: **our two most semantically
loaded colors have nearly identical luminance** (L\* 55.1 vs 59.9 — with dim
ink at 61.6, all three inside 7 L\*). The palette separates roles by hue with
zero luminance work. In greyscale they are the same color; and all consumer
video is 4:2:0, so chroma is at half resolution while our discriminating
information lives entirely in chroma.

**Resolution, assigned by surface.** Three researchers wanted the bright teal
`#3ddad0` for three different jobs; it can only do two, so:

* **Thumbnail** — `#3ddad0` is the **silhouette**: a perimeter keyline at
  1.2–1.5% of frame height plus a top identity band at 9–11% of height.
  [MEAS] this moves edge L\* 32.8 → 45.8 and bright pixels to 20.2%, matching
  the competitive norm of 21.3%. Rule: **at least one element at L\* ≥ 60
  must touch all four edges.** Market/model carry chips with **near-black
  ink** (`#052421` on teal, `#1a0d00` on amber) — never white.
* **Video interior** — `#3ddad0` is the **model mark** (11.11:1 on ground vs
  `#0d9488`'s 5.13:1, and ~2:1 luminance separation from amber). No keyline
  here: a Short fills the screen, so §12's silhouette rule does not apply and
  `#0b0f14` remains an asset.
* **Where two teals must coexist** (the distribution's split at the line) —
  `#5eead4` / `#0f766e`, the only pair clearing 3:1 against each other
  (3.70:1) *and* against the ground. Being a lightness split it survives all
  CVD types, greyscale, and 4:2:0.
* **Amber `#d97706` keeps ACTUAL RESULT**, always with a 4px `#0b0f14` halo
  (so its adjacent color is the ground at 6.03:1, not teal at 1.18:1) and
  always differing in **shape** — needle vs dots — never hue alone.
* **Win/loss always carries a glyph** (✓/✗ or +/−). Red-green is the most
  common CVD confusion and on a Sim Check thumbnail win/loss *is* the content.
* **Dim ink is demoted to meaningless chrome.** [MEAS] at 168px a dim grey
  market number reads as "blurry", not "de-emphasised". De-emphasise by
  **size**, keep both numbers at ink lightness.

Keep the dark ground. [MEAS] the positive-polarity (dark) advantage is real
and *grows* as text shrinks (Piepenbrock/Buchner) — but keep it **flat**, and
put captions on a light chip in inverse.

**Stop encoding structure with panels.** At 1.10:1 (edges 1.38:1) the layered
surfaces that define the current card *do not exist* on video or at thumbnail
scale. Structure must come from position, type size, and the keyline.

## 13. Frame geometry — one layout engine, two outputs

[DOC] Google publishes exactly one numeric vertical safe-zone spec, and it is
an Illustrator SVG embedded in `support.google.com/google-ads/answer/9128498`.
Parsed against its own dimension-line geometry, at 1080×1920:

```
margins   top 288   bottom 672   left 48   right 192
safe rect x 48..888, y 288..1248   (840 x 960)
checks    288 + 960 + 672 = 1920 ✓   48 + 840 + 192 = 1080 ✓
```

The right margin is large because of the action rail; the bottom margin is
huge because of the title/handle block. **Never put a number in the bottom
25% or the right 16%.**

And the geometry resolves the thumbnail crop for free. [DOC] a vertical video
uploaded with a 16:9 thumbnail has it **replaced by an auto-generated 4:5
crop** on home, explore, and subscriptions. A 4:5 crop of 1080×1920 is
1080×1350 at **y 285..1635** — and Google's hard safe rect (y 288..1248) sits
entirely inside it. So:

> **Obey the 840×960 safe rect and content survives the Shorts chrome *and*
> the thumbnail crop with no extra work.**

Better still, `y 285..1635` **is** our existing 1080×1350 portrait card. Build
the Short so that `frame[285:1635, :]` *is* the portrait asset:
`report/outlook_png.py` already renders exactly that geometry. One layout
engine, two outputs, no second design.

**Pin the plot box, not the frame.** [MEAS] the 9:16 frame itself makes
forecasts look more certain — squeezing the margin axis into a tall frame
tightens any distribution, and Hofman et al. showed axis rescaling alone
changes judgments. Use an identical axes box and an identical px-per-point
x-scale across **every game and both aspect ratios**. Cheapest honesty
guarantee available.

## 14. The distribution graphic

### Form, ranked

1. **Quantile dot plot, split at the line.** The only candidate with a direct
   controlled result on a structurally identical task: Fernandes, Walls,
   Munson, Hullman & Kay (CHI 2018) had subjects decide which side of a
   threshold the mass falls on; quantile dotplots produced decisions at **97%
   of optimal payoff (95% CI [95,98]), ~5pp better than PDF, CDF, interval,
   text, and control**. Precedent: 538 ran 40,000 sims and shipped 100 dots.
2. **Icon array / 10×10 grid** — best pure "34 out of 100" comprehension, but
   discards the axis, so no "by how much". Use as a 2s cutaway or end card.
3. **pmf bars** — nearly free, and preserves the real spikes at 3/6/7/10,
   which is genuine football information. Use as a faint step silhouette
   *behind* the dots.
4. **CDF** — mathematically ideal, but Ibrekk & Morgan (1987) found a CDF
   alone severely misled lay subjects and statistics training did not rescue
   them. Secondary panel only.
5. **Beeswarm** — acceptable as a transient build state; meaningless packing
   axis; never the final frame.
6. **Smoothed density — do not ship.** Erases real structure, no countable
   denominator, and it is exactly the false-precision failure the **Bernanke
   Review** cited when eliminating the Bank of England's 30-year fan charts.

**Dot count is surface-dependent.** 100 dots landscape (538 precedent); **~20
dots in a Short** — Fernandes found dotplot performance degrades to PDF level
as dot counts rise on space-constrained screens, so more dots is *worse* on a
phone. Label "1 dot = 1 in 100" and state "20,000 simulations" in text.

### Motion

* **Resting positions are quantiles at (i−0.5)/n — never random draws.**
  Otherwise the RNG seed decides how peaky the forecast looks. Honesty fix
  that satisfies our determinism requirement for free.
* **Build order must be low-discrepancy** (van der Corput or a frozen
  shuffle) — not left-to-right, not naïve random. The last dots to land are
  the most salient, so a tail-finishing order makes tails look heavy.
  Low-discrepancy makes every intermediate frame an unbiased miniature.
* Landscape build, 20.0s at 30fps: axis established in the first 8 frames then
  **frozen forever**; 15 dots land individually at 167ms (150–200ms drop,
  cubic ease-out); rate ramps 6→60/s to exactly 100 dots; line drops
  **400–500ms ease-out, no bounce**; 400ms dwell; recolour staggered 36ms per
  column; counts fade in; **2.9s dead-static hold** (that is the deliverable
  frame, not slack); amber needle 600ms.
* **Animated draws (HOPs) are a separate device** from the building dotplot:
  400ms hard-cut per draw, 15 draws in 6s for a Short. Published timings
  elsewhere: <500ms/frame, or 1500ms hold + 500ms cubic tween.
* Text floor **833ms** (Netflix minimum subtitle event), practical 1200ms;
  headline number 1500–2500ms. **Never animate digits you expect read.**
* **No jitter, ever.** The NYT election needle's designer added jitter
  deliberately so a steady value would not imply certainty; it became "the
  most hated data visualization in politics" and the Times switched it off.
  Motion-as-uncertainty reads as anxiety.
* Hard minimums for 4:2:0 survival: dots ≥14px, lines ≥6px, ≥8px of ground
  between a teal and an amber mark, no gradients, upload CRF 16–18.

### Honesty rules

* **Do not draw the mean, median, or "projected margin" as a mark.** Kale,
  Kay & Hullman (2021) found adding means to uncertainty displays measurably
  biases viewers toward *discounting* uncertainty. State it in narration; give
  it no mark. (This is the §6 conflict with `caption()`.)
* **No error bars in a Short.** The deterministic construal error means
  viewers read a 95% interval as high/low forecasts and "maintained this
  belief even when the correct interpretation was shown prominently in a
  key." A legend cannot fix it.
* **Shade no bands.** Hurricane-cone research: viewers read the cone edge as
  a safety boundary and its widening as the storm growing.
* **Mitigate the cliff effect at the line** (Helske et al. 2021) by always
  showing both counts at equal weight, plus the push/near-line mass.
* **A running PIT/percentile strip in every result video**, so one
  4th-percentile game lands inside a visible population instead of reading as
  a broken model.
* **Selection is the real exposure, not the visual.** Pinning the actual
  result is accountability only if **every** game that got a pre-game video
  gets a result video, deterministically, with no editorial gate. This is why
  §4's Sim Check row says *every*.

Two reassurances: showing spread does **not** cost source trust (van der
Bles/Spiegelhalter, PNAS 2020, including a BBC News field experiment —
confidence in the number drops appropriately, trust in the source barely
moves); and animation is right here (Robertson's anti-animation result applies
to *analysis*; animation was fastest for *presentation*).

## 15. Packaging

### The information budget for a 22s Short

[MEAS] **55–70 narration words** (Brysbaert 2019: oral reading 183 wpm,
n=18,573) and **exactly 4 load-bearing numbers** (Cowan 2001: ~4 chunks) — a
fifth number *evicts* one. 6–8 visual states, **≥2 in the first 2 seconds**
([DOC] Google/Ipsos n=5,000: "aim for two or more shots in the first five
seconds").

**Division of labour, from Mayer's redundancy principle** — identical
narration plus on-screen prose competes for visual working memory, so the
viewer reads, stops listening, and swipes:

> **narration = the argument · graphic = numerals only · caption = verbatim
> words**

**Sound is ON, contrary to the assumption this doc started with.** [DOC]
Google's ABCD playbook: "95% — the amount of video watched on YouTube that is
played with sound on" (Google internal, Sept 2018 — dated, treat directionally).
So narration is load-bearing, not decoration. Burn captions anyway as
insurance: [MEAS] Verizon/Publicis 2019 (n=5,616) found 69% watch sound-off in
public and 80% are likelier to finish a captioned video.

**First frame:** hold it **still for 0.4–0.8s** before any motion — highest
leverage single change in the pipeline, and it costs one ffmpeg argument. A
frame already animating does not register as a poster. Number and matchup
readable before a word is spoken. Shorts titles are overlaid and truncated
hard: **under ~40 characters**, number early.

### Thumbnail template (validated, programmable)

1920×1080, all positions as fractions of W/H so it is resolution-independent.
[MEAS] the rendered template measures mean L\* 27.2, edge L\* 45.8, 20.2%
bright pixels — inside the successful-channel range.

```
KEYLINE    perimeter, #3ddad0, width 0.014·H        [silhouette guarantee]
BAND       y 0 → 0.10·H, #3ddad0, identity text cap 0.06·H, ink #052421
LOGO ROW   y 0.145 → 0.315·H — two logos r = 0.085·H
           matchup "KC @ BUF" at Tier 2 (0.186·H), #eef2f6
COMPARE    x 0.055 → 0.45·W
           chip "MARKET" #d97706 / ink #1a0d00, Tier 3 — number Tier 1
           chip "MODEL"  #3ddad0 / ink #052421, Tier 3 — number Tier 1
PICTOGRAM  x 0.62 → 0.975·W, y 0.49 → 0.93·H
           ~17 bars from the sim pmf, market line as an amber rule
           NO axes / NO gridlines / NO tick labels / NO legend
SAFE AREA  inset 4% all sides for load-bearing content
```

Every text element gets a black stroke ~0.004·H. **Logos need ≥20% of frame
height** to be identifiable at 168px (~25% at 120px) — [MEAS] logos beat
abbreviations at small size because shape+colour survives downscale where
letterforms die. The matchup string is Tier 2 support, never the hero.

Trademark caveat: team logos are trademarks. Editorial use is universal in
this vertical and YouTube does not police it, but leagues occasionally do and
a monetized channel is a more attractive target. **A decision, not an
assumption** — `assets.py` can fall back to team-colour blocks.

### Titles

The trap is episodic invisibility: "MLB Sides, Totals & Player Props for
Friday!" is slot-filled and interchangeable across days. We hold a
differentiator no competitor has — **a specific number disagreeing with a
specific market number, which differs every day because the data differs.**
The differentiation is *generated*, not written.

```
The Market Has {TEAM} {LINE}. We Have {MODEL}. | {LEAGUE} Wk {N}
{TEAM} {LINE} Is {DELTA} Points Off Our Number | {LEAGUE} {DATE}
Three Plays The Market Has Wrong Tonight | {LEAGUE} {DATE}
No Edge On Tonight's {LEAGUE} Board — Here's Why We're Passing
We Called {N} Plays Yesterday. Here's The Grade. | {LEAGUE}
{PLAYER} {MARKET} {LINE} — Our Model Says {P}%
```

Claim in the **first 45–50 characters**, searchable `{LEAGUE} Wk {N}` token
after the pipe. 100-char hard limit is [DOC]; the 50–60 mobile truncation
figure is [FOLK] and unverified. Put "Week 5, episode 34" in **YouTube Shows
metadata, not the title** [DOC] — and Shows' stated requirement of "the same
style of thumbnails, the same video format" is something a programmatic
template satisfies by construction.

§3 rule 2 turns out to be our strongest packaging asset: **"No Edge On
Tonight's Board"** is a title no competitor will imitate, and it is
differentiated by construction.

### Generated blocklist (hard-fail the render)

`guaranteed` · `lock` · `locks` · `can't lose` · `sure thing` · `risk-free` ·
`free money` · `printing money` · `bank` · `easy money` — plus **every
sportsbook name and logo**. Say "MARKET" / "CONSENSUS" / "THE NUMBER", never
"DraftKings 3.5": [DOC] a visual display of a non-certified book is treated as
facilitating access. (OddsJam does it; they are a certified-affiliate
business. Do not infer permission.) **"Lock" is standard handicapping
vernacular and is the highest-frequency trap in this niche.**

Misleading-metadata policy is about **promise/delivery consistency**, not
intensity — so generating thumbnail text from the same selected-plays
structure that feeds the script *is* the compliance control. Architecture as
policy.

## 16. Quota and tooling — a launch gate

```
6 × 1,600  videos.insert     = 9,600
6 ×    50  thumbnails.set    =   300
                               9,900  of 10,000 units/day  →  99%
```

**Zero headroom for a single retry.** Either request a quota increase before
launch or cut the daily upload count. Also [DOC]: `thumbnails.set` caps at
**2 MB via the API** (the 50 MB figure is the Studio UI limit, so the
pipeline is bound by the smaller one), and `429 uploadRateLimitExceeded` is
real — set a thumbnail **once per video, on upload**, never re-render in a
loop.

**Test & Compare is mostly unusable for daily content.** [DOC] no Data API
endpoint exists (Studio desktop only); winners resolve on **watch time share**
over days to two weeks, which exceeds a Week-5 video's useful life; **Shorts
are ineligible**, so four of six daily units get no feedback at all; and
YouTube itself warns against A/B testing thumbnails on single videos.

**So make the template the experimental unit, not the video.** Ship template
variant A for a fortnight of dailies, B for the next, compare cohort medians
of impressions/CTR/AVD from the Analytics API. Keep a **frozen holdout
template** to detect drift. Reserve Test & Compare proper for evergreen
pillar videos (how the model works, a CLV explainer, season previews), where a
two-week window is fine.

**Do not make CTR the optimization target.** [DOC] YouTube's own docs work an
example where CTR falls **9% → 3.5% while impressions rise 10×** and frame it
as success; high CTR with low view duration is treated as a clickbait signal
and gets *reduced* recommendation. Search traffic flatters CTR, home and
suggested depress it. The benchmark band is 2–10% and there is no official
"good" number.

## 17. What we must measure ourselves

Genuine gaps — no primary source exists, so these are ours to establish:

* No platform publishes an **organic** Shorts safe zone (§13's numbers are
  from the Ads spec), a minimum text size, a Shorts retention benchmark, or a
  Shorts sound-off rate.
* No measured study exists on karaoke captions, counters/bar races, or the
  apportionment between audio quality and visual polish.
* No [MEAS] evidence exists either way on hype vs analytical aesthetic for
  *reach*. What is documented is a credibility cost with sharp audiences and
  **zero measured reach upside** — so §12's restraint is the defensible
  default, not a proven optimum.
* The only documented Shorts-specific attention metric is **"Viewed (vs
  swiped away)"** — a first-frame metric with no published benchmark. A
  programmatic renderer emitting several hook variants per game should
  optimize this against its own history. That is the closest thing to a real
  feedback loop available to us.
