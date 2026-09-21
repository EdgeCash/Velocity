# Phase 13 proposal — operating Velocity from an iPad

**Status: proposal. Nothing here is implemented.** Requested per the owner's
Phase 13 directive: compare a workbook-only workflow, a GitHub Pages
dashboard, and a hybrid, on ease of use, reliability, maintenance, cost,
dependency on scheduled Actions, and data freshness.

**Recommendation: Option C (hybrid), built in two stages, with Stage 1
delivering most of the value.** Reasoning below; the short version is that
Options A and B fail at different moments and the failures do not overlap.

---

## 0. What the goal actually is

> "A workflow that can be operated primarily from an iPad while preserving
> the full Velocity simulation engine and wagering logic."

Two halves, and only one is a technology question.

**Reading** the board on an iPad works today. `velocity.xlsx` opens, sorts
and filters; the Run status block says whether it is usable.

**Acting** on it is where the tablet hurts. The current loop is: Safari →
Actions → Run workflow → wait → Artifacts → download zip → Files →
long-press → Uncompress → tap the file. Nine steps, of which the unzip is
the one nobody guesses. Email delivery (D3) removes most of that *when the
run happens on schedule* — and scheduled runs are exactly what cannot be
relied on.

So the real problem is not *rendering*. It is **the number of taps between
"I want a current board" and "I am looking at one"**, and how many of those
taps have to happen before kickoff.

Any option that does not shorten that loop has not solved the stated goal,
however nice its output looks.

---

## 1. The options

### Option A — workbook only

Stay with `velocity.xlsx`, delivered by email, and invest in the workbook
itself: better tabs, conditional formatting, a sharper Run status block,
possibly a second "Bet Card" tab reduced to the plays and nothing else.

### Option B — GitHub Pages dashboard

A static site built from `dashboard.csv` and `plays.csv` (and `games.csv`,
`team_totals.csv`, `props.csv`), published to GitHub Pages, opened in Safari
and pinned to the home screen.

### Option C — hybrid

The dashboard for **betting** (the curated card, the readiness verdict, the
edges you act on), the workbook for **research** (the full pool, the
distributions, the DFS optimizer, anything you sort and filter).

---

## 2. Comparison

| | **A — workbook only** | **B — Pages dashboard** | **C — hybrid** |
|---|---|---|---|
| **Ease of use on iPad** | Good once open. Email delivery is one tap; the download path is nine steps. Excel on a 10-inch screen is cramped for a 17-column card — landscape only, and the Reason column needs the formula bar. | **Best.** A URL, a home-screen icon, one tap, no download, no app, no unzip. Built for the screen rather than adapted to it. | **Best for each job.** Tap the icon to bet; open the workbook when you want to dig. |
| **Reliability** | **Highest.** A file on the device. No network after delivery, nothing to fail at read time, works on a plane. | Good, with a caveat: the page is only as fresh as its last publish, and a failed publish leaves a *stale page that still loads* — the most dangerous failure mode in this whole document. Needs the readiness verdict rendered prominently and a visible "as of" stamp. | Good, and the failure modes are independent: if the page is stale the workbook in your inbox is not. |
| **Maintenance** | **Lowest.** `workbook.py` exists and is tested. Changes are formatting. | Moderate and ongoing. A build step, a template, CSS, a publish workflow, and a second rendering of numbers that must not drift from the CSVs. Every export schema change touches it. | Highest total, but additive: the workbook keeps working while the dashboard is built, and the dashboard can stay deliberately small. |
| **Cost** | £0. Email is free; Actions minutes unchanged. | £0 for a **public** Pages site — which is the problem. See §3. A private host is ~£0–5/month (Cloudflare Worker behind Access, which this repo already runs for the Evidence site). | Same as B. |
| **Dependency on scheduled Actions** | **Unchanged and total.** Email arrives when the run runs; the run runs 1h48–3h00 late or not at all. Manual dispatch remains mandatory. | **Unchanged.** A dashboard published by the same workflow inherits the same delay. *Publishing is not freshness.* | **Unchanged** — and this is the honest answer for all three. No front end fixes the scheduler. |
| **Data freshness** | Freshest available at delivery; the file states its own age. | Same data, and *appears* live because it is a URL. That appearance is a liability unless the page states its age as loudly as its numbers. | Same, stated twice. |

---

## 3. The blocker on Option B as usually imagined

**GitHub Pages is public.** There is no private Pages tier on a personal
account, and even on an organisation it is an Enterprise feature.

`datasets/exports/` is gitignored precisely because those numbers derive
from The Odds API, BettingPros and DraftKings — feeds this repository
quarantines to Actions artifacts and whose terms do not permit
republication. Publishing `plays.csv` to a public URL would put paid
sportsbook prices, de-vigged fair probabilities and Kelly stakes on the open
internet.

That is not a risk to weigh. It is the thing the entire data posture exists
to prevent, and it would also hand away the edge.

**So Option B is only viable behind authentication.** Fortunately this is
solved: the repo already deploys the Evidence site to a **Cloudflare Worker
behind Cloudflare Access**, with the token and config already in the
workflow. A mobile dashboard is another route on that same Worker — reusing
an access model that exists rather than inventing one.

Any Phase 13 work that mentions Pages must mean *Cloudflare Worker behind
Access*, not `github.io`.

---

## 4. Recommendation — Option C, in two stages

### Stage 1 — the tap count (small, high value)

Do this first regardless of what follows. None of it needs a dashboard.

1. **Verify email delivery end to end** (D3 is now implemented but unproven
   in the wild — the secrets may not be set). One confirmed arrival closes
   the loop from nine taps to one.
2. **A "Bet Card" tab** in the workbook: the A+/A/B plays and nothing else,
   formatted for portrait, Reason wrapped to three lines. The current
   Curated Plays tab is a table; this would be a card.
3. **A one-tap dispatch shortcut.** An iOS Shortcut hitting the Actions
   `workflow_dispatch` REST endpoint turns "Safari → Actions → navigate →
   Run workflow" into a home-screen button. This is the single largest
   ease-of-use win available and it needs **no repository change at all** —
   just a fine-grained PAT and ten minutes in the Shortcuts app.

Estimated effort: a few hours. Most of the mobile pain disappears.

### Stage 2 — the dashboard (larger, only if Stage 1 leaves a gap)

A single-page dashboard on the existing Cloudflare Worker behind Access:

* the readiness verdict, at the top, unmissable, with the board's age
* the curated card — tier, selection, edge, confidence, stake, reason
* the day's games, one line each, sorted by the edge you care about
* a dispatch button (same endpoint as the Shortcut)
* deliberately **read-only and small** — no DFS optimizer, no pool, no
  distributions. Those stay in the workbook, where sorting and filtering
  already work.

Built from the CSVs the export already writes, so it adds a renderer, not a
second source of numbers.

Estimated effort: one to two days, plus ongoing upkeep on every export
schema change.

### What I would not do

* **Not Option A alone.** It leaves the nine-tap download path in place for
  every run where email has not arrived, which is exactly the runs that
  matter (late or dropped).
* **Not Option B alone.** Losing the workbook loses sorting, filtering and
  offline access — and a stale-but-loading page is a worse failure than a
  file whose age you can see.
* **Not public Pages**, per §3.
* **Not infrastructure migration**, per D4 — and note that none of A, B or C
  changes the scheduling delay. If the delay is what actually costs slates,
  the answer is in D4's data, not in this document.

---

## 5. What would change the recommendation

| If… | Then |
|---|---|
| Email delivery proves reliable and the Bet Card tab reads well | Stop at Stage 1. Option A is enough, and Stage 2's upkeep is not worth it. |
| You find yourself dispatching manually every game day | The iOS Shortcut matters more than either front end. Do that first, alone. |
| Several weekends of D4 data show dropped slots rather than late ones | No front end helps. That is the self-hosted-runner conversation, on D4's terms. |
| You want to act on the board away from a desk more than you want to research it | Stage 2 earns its keep. |

---

## 6. What this preserves

Every option here is a **reading surface**. None touches the simulation, the
wagering gate, the intel layer or the export contract. The engine keeps
producing the same frames; the export keeps writing the same CSVs; a
dashboard, if built, is one more consumer of them alongside the workbook and
the Evidence site.

That is the property worth protecting, and it is why the export layer was
built as a read-only projection in the first place.
