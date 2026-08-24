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
| Sim Check | `report.sim_check.build_sim_checks()` | Exactly the plays published the day before |

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
* **`report/outlook_png.py` renders 1080×1350** — one crop from a Short.
* **`social.caption()`** (`social.py:523`) emits post copy "stated as fact,
  no imperatives." That docstring is a policy asset, not just a style note:
  it is what keeps scripts clear of the promotional framing that draws
  gambling-policy enforcement. Preserve it verbatim into video scripts.
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

1. **Gambling is a limited-ads category, structurally.** Not fixable.
   AdSense is not the prize — judge the channel as a funnel to the site, and
   the economics change shape. Never link sportsbook affiliates (an explicit
   policy violation, not a gray area).
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
