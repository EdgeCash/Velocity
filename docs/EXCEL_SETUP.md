# Excel front end — setup, refresh, and workbook structure

> **On an iPad, iPhone or Android tablet, read
> [`docs/EXCEL_IPAD.md`](EXCEL_IPAD.md) instead.** Those versions of Excel
> have no Power Query at all, so nothing on this page can be assembled there.
> They get `velocity.xlsx` — one file, every tab already filled in — which
> the same pipeline run produces alongside these CSVs.

This page is the **desktop** route: Excel on Windows, or Mac with Power Query
limitations.

Velocity is the engine; Excel is the front end. The seam is six CSVs in
`datasets/exports/`, written by `velocity/export` and refreshed by
`python -m velocity.run_weekly`. Power Query reads them directly — no
transformation step, no manual cleanup, and nothing to re-type.

```
python -m velocity.run_weekly   →   datasets/exports/*.csv   →   Refresh All
```

---

## 1. The files

| File | One row per | Feeds the tab |
|---|---|---|
| `games.csv` | game on the board | Betting Card |
| `props.csv` | staked or papered player prop | Props |
| `dfs.csv` | player on the DK slate | DFS Pool |
| `dfs_optimizer.csv` | player, with one column per contest type | DFS Optimizer |
| `plays.csv` | curated play, tiered A+/A/B/Watch | Curated Plays |
| `dashboard.csv` | one metric (long format) | Dashboard, Historical Performance |
| `velocity.xlsx` | — | **all of the above, prebuilt**: the tablet route, and a desktop shortcut |

Every file carries `generated_at`, `season` and `week` on **every row**, as
the last three columns. `generated_at` is when the *numbers* were made — the
stamp of the pipeline run the export read — not when the CSV was written.
Those differ by however long the artifact sat in storage, and a dashboard
that reports the latter calls stale data fresh.

### Guarantees the workbook can rely on

* **Filenames never change.** No stamps, no dates, no league suffixes. A
  Power Query source that changes name every run is not a source.
* **Column names and order never change silently.** Each export owns a
  `*_COLUMNS` tuple in code, and `tests/test_export_contract.py` fails the
  build if one drifts.
* **Headers are `lower_snake_case`, always.** A header with a space or a
  `%` becomes `#"Model %"` in every M expression that touches it, and a
  rename then breaks the workbook silently. The contract test enforces this.
* **A file always exists.** With no data the exporter writes the header row
  alone, so a refresh finds a table with zero rows rather than a broken
  query.
* **Empty means unknown, never zero.** A column with no honest source in the
  run is blank. `ownership` is blank unless a projection was supplied;
  percentile columns are blank when the run banked no distribution. Do not
  wrap these in `IFERROR(...,0)` — a zero here is a claim the model never
  made.
* **UTF-8 with a BOM, LF line endings.** The BOM is the only in-band way to
  tell Excel a file is UTF-8; Power Query handles it transparently. LF is
  written explicitly so two identical runs produce byte-identical files and
  a diff of two exports is signal rather than noise.

---

## 2. Power Query setup (once)

For each file:

1. **Data → Get Data → From File → From Text/CSV.**
2. Point at `datasets/exports/games.csv` (and so on).
3. In the preview dialog, check:
   * **File Origin:** `65001: Unicode (UTF-8)`
   * **Delimiter:** `Comma`
   * **Data Type Detection:** `Based on entire dataset`
4. **Transform Data**, then in the Power Query editor:
   * Confirm **Promoted Headers** is the first step.
   * Confirm **Changed Type** types `generated_at` as **Text**, not Date.
     It is already an unambiguous ISO-8601 UTC instant; letting Excel parse
     it hands the value to the viewer's regional settings.
   * Delete nothing else. The file is already the shape the tab wants.
5. **Close & Load To… → Table → New worksheet**, named per §4.

### Make the path portable

Hard-coded paths break the moment the workbook moves. Create one named cell
— say `ExportPath` on a hidden `Config` sheet, holding
`C:\Velocity\datasets\exports\` — and start each query from it:

```m
let
    Root  = Excel.CurrentWorkbook(){[Name="ExportPath"]}[Content]{0}[Column1],
    Source = Csv.Document(
        File.Contents(Root & "games.csv"),
        [Delimiter = ",", Encoding = 65001, QuoteStyle = QuoteStyle.Csv]
    ),
    Promoted = Table.PromoteHeaders(Source, [PromoteAllScalars = true])
in
    Promoted
```

Leaving the types to `Table.PromoteHeaders` alone (no `Table.TransformColumnTypes`
step) is deliberate for these queries: a column that is blank this week and
numeric next week cannot then break the refresh with a type error.

---

## 3. Refresh procedure

**Every refresh is two steps, in this order.**

```bash
# 1. Rebuild the CSVs (seconds, if the artifacts are already banked)
python -m velocity.run_weekly --steps export --slate-dir artifacts/slate

# 2. In Excel: Data → Refresh All   (or Ctrl+Alt+F5)
```

A full run — refresh datasets, fit, simulate, price, export — is:

```bash
python -m velocity.run_weekly --slate-dir artifacts/slate --bankroll 250
```

Useful flags:

| Flag | Why |
|---|---|
| `--steps export` | re-export from banked artifacts without re-simulating |
| `--leagues nfl` | one league |
| `--bankroll N` | what `kelly_fraction` and `recommended_stake` are sized against |
| `--dry-run` | print the plan without running anything |
| `--strict` | stop at the first failing step |
| `--salaries` / `--fp` | the DK salary snapshot and FantasyPros frame the DFS step needs |

The run logs one JSON object per step to stdout and exits non-zero if any
step failed. A step that fails does **not** stop the export: a props outage
should cost the props, not the board.

### Set Excel to refresh on open

Right-click each query → **Properties** → check **Refresh data when opening
the file**, and uncheck **Enable background refresh** so formulas that depend
on a table do not calculate against half of it.

---

## 4. Recommended workbook structure

Seven tabs, one query each (except Dashboard, which pivots one query).

### Dashboard
Source: `dashboard.csv` (long format: `section`, `metric`, `value`, `detail`).

Pivot or `XLOOKUP` by `section` + `metric`. The sections are `performance`,
`roi`, `clv`, `top_plays`, `top_props`, `top_dfs`. Suggested tiles:

| Tile | Lookup |
|---|---|
| Record | `performance` / `record` (the `detail` cell) |
| Win rate | `performance` / `win_rate` — format `0.0%` |
| ROI | `roi` / `roi_overall` — format `0.0%` |
| Mean CLV | `clv` / `mean_price_clv` |
| Beat the close | `clv` / `pct_beat_close` — format `0.0%` |
| Top plays | filter `section = "top_plays"`, show `detail` |

Long format is the one shape Power Query pivots into any tile without a
transformation step. Six separate tables would each need their own query and
would drift apart on the first schema change.

### Betting Card
Source: `games.csv`. Suggested columns left to right:

`away_team`, `home_team`, `market_spread`, `model_home_score`,
`model_away_score`, `spread_edge`, `cover_probability`, `market_total`,
`model_total`, `total_edge`, `over_probability`, `confidence`,
`kelly_fraction`, `weather`.

Sign conventions, which the conditional formatting should follow:

* `spread_edge` — **positive = value on the home side.** Market `-3` against a
  model fair `-6` is `+3`: the market is selling home too cheap.
* `total_edge` — **positive = value on the over.** Model 48 against a market
  44 is `+4`.
* `cover_probability` — P(**home** covers `market_spread`).
* `over_probability` — P(total goes over `market_total`).

Both probabilities are counted off the simulation's own distribution over the
full 100,000 draws, with pushes excluded from the numerator exactly as the
grader does. They are the engine's numbers, not a normal approximation of
them.

### Props
Source: `props.csv`. Lead with `player`, `team`, `market`, `line`,
`projection`, `hit_probability`, `edge`, `recommended_stake`, then the
distribution: `median`, `75th_percentile`, `90th_percentile`,
`99th_percentile`.

`fair_price` and `market_price` are both American odds, so they subtract
cleanly. `edge` is `hit_probability` minus the de-vigged market probability.

A useful conditional format: highlight where `line` sits below `median` on an
over — the sim's middle outcome already clears the number.

### DFS Pool
Source: `dfs.csv`. `player`, `team`, `position`, `salary`, `projection`,
`median`, `ceiling`, `value_score`, `stack_rating`, `ownership`,
`leverage_score`.

* `value_score` — projected points per $1,000 of salary (DraftKings' own
  convention). Blank on a salary-free board, because points per zero dollars
  is not a large number, it is not a number.
* `stack_rating` — 0–10, how strong the *teammates* are: the summed
  projection of the best three other players on that team, scaled across the
  slate. High means a lineup built around this team has partners.
* `ownership` — **blank unless you supply it.** DraftKings does not publish
  pre-lock ownership and nothing in this repo models it.
* `leverage_score` — blank without ownership, since leverage against an
  unknown field is not a quantity.

### DFS Optimizer
Source: `dfs_optimizer.csv`. One row per player, one column per contest type:

| Column | Quantile | Use it for |
|---|---|---|
| `cash` | 50th | double-ups, 50/50s — you need the median |
| `single_entry` | 75th | single-entry fields — a good day wins |
| `gpp` | 90th | large-field tournaments |
| `ceiling` | 99th | the top-heavy tail |

Point Excel's Solver (or your optimizer of choice) at the column that matches
the contest. Constraints for DK Classic NFL: `salary` sum ≤ 50,000, exactly
1 QB, 2 RB, 3 WR, 1 TE, 1 FLEX (RB/WR/TE), 1 DST — nine rows selected.

The four columns are the *same player's* distribution read at four points.
If they are all identical, the run banked no distribution and every column
fell back to blank; check that `dfs_dist_{league}_{stamp}.parquet` exists in
the artifact folder.

### Curated Plays
Source: `plays.csv`. `tier`, `selection`, `market`, `edge`, `confidence`,
`stake`, `reason`, `bet_type`.

Tiers, best first:

| Tier | What it means |
|---|---|
| **A+** | high confidence **and** a rule with a measured walk-forward record behind it |
| **A** | high confidence, no promoted rule for this market yet |
| **B** | staked, positive edge, lower confidence |
| **Watch** | seen and **not bet** — vetoed by the intel layer, papered, zero-staked, or no positive edge |

`Watch` is not a failure state. A play that the system looked at and declined
is more use to a reader than a silent absence, so it is shown with the reason
it was declined.

`confidence` is 0–10, blended from the edge, the rule's walk-forward record
(win rate scaled by seasons cleared) and the intel layer's conviction.
Components that are absent are dropped and the remaining weights
renormalized, so a play with no context read is scored on what *is* known
rather than penalized for the silence.

`reason` is one line by design. Excel renders a multi-line cell as one tall
row that pushes the rest of the table off the screen; set the column to
~90 characters wide and **turn wrap text off**. For the block form, call
`velocity.wagering.plays.explain()`.

Sort by `tier` then `confidence` descending — which is the order the file
already arrives in.

### Historical Performance
Source: `dashboard.csv`, filtered to `section` in `{performance, roi, clv}`.

To build a season-long history rather than a snapshot, append each refresh to
a persistent table: Power Query → **Append Queries** against a stored
`history.csv` you archive after each run, keyed on `generated_at`. The export
layer deliberately does not keep history itself — it is a projection of the
current run, and the settled record lives in the pipeline's own archive
(`velocity/backtest/archive.py`).

---

## 5. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Accented names show as `JosÃ©` | Excel read the file as ANSI | Set File Origin to `65001: Unicode (UTF-8)` in the query |
| A column is entirely blank | that family had no artifact this run | check the `export` step's log line — it names every missing family |
| Refresh errors on a type | a column was blank last week, numeric now | delete the `Changed Type` step from that query |
| `generated_at` shows as a date serial | Excel parsed the ISO string | set that column's type to **Text** |
| All four DFS contest columns match | no `dfs_dist_*` artifact | re-run the `dfs` step with `--salaries` and `--fp` |
| Numbers look stale | `generated_at` is the stamp of the run that made them | re-run the pipeline, not just the export |
| Files are missing entirely | the export step never ran | `python -m velocity.run_weekly --steps export --dry-run` to see the plan |

### A note on what is in these files

Exports are derived from paid and licensed feeds — The Odds API prices,
BettingPros lines, DraftKings salaries. `datasets/exports/` is gitignored for
exactly that reason (see `.gitignore`, and
[`docs/HYBRID_MIGRATION_PLAN.md`](HYBRID_MIGRATION_PLAN.md) §4). Keep the
workbook local; do not commit it, and do not publish the CSVs.
