# Velocity on an iPad

**Short version:** don't use the CSVs. Open `velocity.xlsx` — one file, every
tab already filled in. Get it from the email, or from the Actions artifact.

---

## 1. Why the CSV route does not work here

[`docs/EXCEL_SETUP.md`](EXCEL_SETUP.md) builds the front end from six CSVs
plus Power Query. On a desktop that is the right design: connect once, and
every later refresh is a button.

On an iPad it is not a design at all:

| | iPad |
|---|---|
| Power Query (`Data → Get Data`) | **Not present.** Not hidden, not behind a subscription tier — Excel for iPad, iPhone and Android has never shipped it |
| Refreshing a query built elsewhere | **No.** A workbook with connections opens, but the Connections/Queries pane and refresh are absent |
| Running `python -m velocity.run_weekly` | **No.** No local Python, and the pipeline needs the paid feeds anyway |

So on a tablet there is no sequence of taps that assembles the workbook from
the CSVs. The answer is not a workaround for Power Query; it is to ship the
finished file instead.

## 2. What to use instead

`velocity/export/workbook.py` writes **`velocity.xlsx`** — the same six
tables, in one workbook, already populated, formatted, filterable:

```
Read Me · Dashboard · Betting Card · Props · DFS Pool · DFS Optimizer · Curated Plays
```

It is built by the same pipeline run that writes the CSVs, from the same
frames, read back from those CSVs so the file and the files cannot disagree.
Nothing to connect. Nothing to refresh — a newer run is a newer file, which
on a tablet is *fewer* taps than a query refresh would have been.

Two differences from the CSVs, both deliberate:

* `generated_at` / `season` / `week` are in each sheet's subtitle and on Read
  Me, not repeated down every row. You need the horizontal space more.
* Headers read like headers (`Home Cover %`), not like machine names
  (`cover_probability`). Nothing queries this file.

---

## 3. Getting the file onto your iPad

### Route A — email (best, if you can set it up once)

`live-slate.yml` already emails the slate when three repository secrets are
set, and now attaches `velocity.xlsx` to that mail. Every run lands in your
inbox; you tap the attachment and Excel opens it.

Set these once at
**github.com → EdgeCash/Velocity → Settings → Secrets and variables →
Actions**:

| Secret | Value |
|---|---|
| `MAIL_USERNAME` | the sending account (a Gmail address works) |
| `MAIL_PASSWORD` | an **app password**, not the account password |
| `MAIL_TO` | where to send it — your own address |

For Gmail, an app password needs 2-Step Verification on, then
**myaccount.google.com → Security → 2-Step Verification → App passwords**.
`MAIL_SERVER` and `MAIL_PORT` default to `smtp.gmail.com` / `465`; set them
only for another provider.

You can do all of this from Safari on the iPad. Nothing else is needed — the
email step is already written and skips itself silently until the secrets
exist.

> The mail carries paid-odds-derived numbers, so send it to a private inbox
> and don't forward it. Same posture as the rest of the data
> ([`docs/HYBRID_MIGRATION_PLAN.md`](HYBRID_MIGRATION_PLAN.md) §4).

### Route B — download from Actions (works today, no setup)

In **Safari** (Chrome on iOS handles the unzip step less well):

1. Go to `github.com/EdgeCash/Velocity` and sign in.
2. **Actions** tab → **Live slate** in the left sidebar → tap the newest run
   with a green check.
3. Scroll to the bottom, to **Artifacts** → tap `slate-<number>`. It
   downloads a `.zip`.
4. Open the **Files** app → **Downloads**.
5. **Long-press the .zip → Uncompress.** A folder appears next to it.
6. Open the folder, find `velocity.xlsx`, tap it. Excel opens it.

Steps 4–6 are the only awkward part, and only the first time — after that the
muscle memory is about fifteen seconds.

### Route C — force a fresh build from the iPad

If the latest run is stale and you want one now, without waiting for the next
scheduled window:

1. **Actions** → **Refresh Excel exports** → **Run workflow**.
2. Optionally set `bankroll` (default 100) and `leagues`.
3. Wait ~2 minutes, then take the artifact as in Route B.

This rebuilds the workbook from the newest banked slate — it does **not**
re-run the simulation, so it is minutes rather than half an hour. Manually
dispatched runs start within a minute; *scheduled* ones don't, which is the
whole subject of [`docs/LATENCY_AUDIT.md`](LATENCY_AUDIT.md).

To re-run the engine itself: **Actions → Live slate → Run workflow**. That one
takes 20–30 minutes and spends Odds API credits.

---

## 4. Using it on the iPad

You need the **Excel app** (free from the App Store; a Microsoft 365
subscription is needed to *edit*, but not to open, view, sort or filter).

* **Sort / filter** — every table has a filter row and a frozen header. Tap
  the arrow in a header cell.
* **Rotate to landscape** for the Betting Card and DFS Optimizer. They are
  wide by nature.
* **The Reason column on Curated Plays** is long prose. Tap the cell and read
  it in the formula bar rather than widening the column.
* **Curated Plays is colour-coded** — green A+, pale green A, amber B, grey
  Watch.
* **Don't edit the file.** A newer run replaces it. Keep your own notes in a
  separate workbook.

### Two things that are easy to misread

* **Signs.** `Spread Edge` positive = value on the **home** side. `Total Edge`
  positive = value on the **over**. Both are in points.
* **Blank is not zero.** A blank cell means the system has no honest source
  for that number. `Ownership` is always blank unless you supply a
  projection — DraftKings doesn't publish pre-lock ownership and nothing here
  models it — and `Leverage`, meaningless without ownership, is blank with
  it. Don't fill these with zeros; zero is a claim, blank is the truth.

---

## 5. Alternatives, and why they are worse

| Option | Verdict |
|---|---|
| **Excel for the web** in Safari | Can refresh some Power Query connections, but only for sources already in OneDrive/SharePoint — not the local CSVs. You'd have to upload six files per refresh, by hand, to save a step. Worse than one attachment |
| **Google Sheets + `IMPORTDATA()`** | Needs the CSVs at an anonymously-readable URL. These are paid-feed-derived and must not be public, so this is off the table on licensing grounds, not technical ones |
| **Numbers (Apple)** | Opens the .xlsx, but re-renders formatting and number formats unpredictably. Fine for a look, not for the DFS Optimizer |
| **Remote desktop to a PC** | Works, and gives you the full Power Query build. If you have a desktop available, use [`docs/EXCEL_SETUP.md`](EXCEL_SETUP.md) instead of this page |

If you ever do get to a desktop, the CSVs are still there in the same
artifact, and the Power Query setup still applies. The two front ends read
the same numbers from the same run; neither is a fork of the other.
