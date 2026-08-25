# DK contest formats — what DK actually offers, and what Velocity prices

DraftKings runs far more than "the main slate". This is the authoritative map
of the formats DK serves for MLB, NFL and CFB, read from DK's own rules API
rather than from the lobby's marketing copy, plus what Velocity builds for
each one.

## Where the rules come from

Two unauthenticated endpoints carry everything:

* `https://www.draftkings.com/lobby/getcontests?sport={MLB|NFL|CFB}` — the
  day's **draft groups** (`DraftGroups[]`: id, `ContestTypeId`, `GameCount`,
  `StartDate`, `ContestStartTimeSuffix`) and the **contests** that reference
  them (`Contests[].dg` → `Contests[].gameType`, DK's plain-English format
  name).
* `https://api.draftkings.com/lineups/v1/gametypes/{id}/rules?format=json` —
  the roster rules for a game type: `lineupTemplate` (**singular** — the
  roster slots and their counts), `salaryCap`, `teamCount.minValue`,
  `uniquePlayers`, `draftType`, and per-slot multipliers.

Two traps worth stating, both of which cost real time to find:

1. **`ContestTypeId` is not `gameTypeId`.** They agree for the newer formats
   (showdown is 114/96/95 in both spaces) but not the oldest: MLB Classic
   ships as `ContestTypeId` 28 against game type 2, NFL Classic as 21 against
   1. So format detection keys on `gameType` — the **name** off the contest
   list — and falls back to the id only for snapshots taken before the
   collector captured it (`velocity.dfs.salaries.game_type_names`).
2. **DK runs simulated look-alikes.** "Madden Classic" and "Madden Showdown
   Captain Mode" (game types 158/159) have the identical roster shape and a
   completely different player pool — simulated games. A numeric-only or
   shape-only filter drafts them by accident.

## The map

Cap `—` means the format has no salary cap at all: the roster is built by
tier pick or by snake draft, so there is nothing for a knapsack to solve.

### MLB

| Game type | Format | Roster | Cap | Draft | Velocity |
|---|---|---|---|---|---|
| 2 | Classic | P,P,C,1B,2B,3B,SS,OF,OF,OF | $50,000 | SalaryCap | **built** (`MLB_CLASSIC`) |
| 114 | Showdown Captain Mode | CPT (1.5x), UTIL x5 | $50,000 | SalaryCap | **built** (`velocity.dfs.showdown`) |
| 45 | Tiers | T1…T6, one player per tier | — | Tiered | planned |
| 346 | Single Stat - Home Runs | UTIL x3 | — | Tiered | HR model built (`docs/PROPS_HR.md`); board picker planned |
| 178 | Snake | IF,IF,OF,OF,UTIL,UTIL,BN | — | SnakeDraft | planned (draft advisor) |
| 179 | Snake Showdown | UTIL x3, BN | — | SnakeDraft | planned (draft advisor) |

### NFL

| Game type | Format | Roster | Cap | Draft | Velocity |
|---|---|---|---|---|---|
| 1 | Classic | QB,RB,RB,WR,WR,WR,TE,FLEX,DST | $50,000 | SalaryCap | **built** (`NFL_CLASSIC`) |
| 96 | Showdown Captain Mode | CPT (1.5x), FLEX x5 | $50,000 | SalaryCap | **built** (`velocity.dfs.showdown`) |
| 145 | Best Ball | QB,RB,RB,WR,WR,WR,TE,FLEX + 12 BN | — | SnakeDraft | planned; position limits QB 1-3, RB 2-6, WR 3-8, TE 1-3 |
| 189 | Snake | QB,RB,WR/TE,WR/TE,FLEX,FLEX,BENCH | — | SnakeDraft | planned (draft advisor) |
| 192 | Snake Showdown | S-FLEX x3, BENCH | — | SnakeDraft | planned (draft advisor) |
| 158 / 159 | Madden Classic / Madden Showdown | same shapes as 1 / 96 | $50,000 | SalaryCap | **excluded** — simulated games, not real players |

### CFB

| Game type | Format | Roster | Cap | Draft | Velocity |
|---|---|---|---|---|---|
| 94 | Classic | QB,RB,RB,WR,WR,WR,FLEX,S-FLEX | $50,000 | SalaryCap | **built** (`CFB_CLASSIC`) |
| 95 | Showdown Captain Mode | CPT (1.5x), UTIL x5 | $50,000 | SalaryCap | **built** (`velocity.dfs.showdown`) |
| 364 | Single Stat - Touchdowns | FLEX x3 | — | Tiered | planned |
| 377 | Snake | QB,RB,WR/TE,WR/TE,FLEX,S-FLEX,BENCH | — | SnakeDraft | planned (draft advisor) |
| 378 | Snake Showdown | S-FLEX x3, BENCH | — | SnakeDraft | planned (draft advisor) |

## Showdown Captain Mode

The one format that applies identically across all three sports, and the
second one Velocity solves exactly.

**The doubled board.** DK lists every player on a showdown board **twice**:
once at the captain roster slot (MLB 573 / CFB 510 / NFL's equivalent) at
1.5x salary, once at flex (574 / 509) at 1x. Verified live on MLB group
152707: 190 draftables, 95 players, every pair exactly 1.5x (Tarik Skubal
$19,500 CPT / $13,000 UTIL). The salary normalizer therefore dedupes on
**price**, not roster slot — on a classic board an RB's RB and FLEX entries
share one salary and collapse to one row, while a showdown board's two
prices are genuinely two rows — and `showdown_board()` pairs them back into
one row per player carrying `salary` and `captain_salary`.

**The rules that bind beyond the cap**, both read off the rules payload:

* `uniquePlayers: true` — the captain may not also fill a flex slot;
* `teamCount.minValue: 2` — a roster must span at least two teams. This is
  not decoration: a lopsided projection set will happily build six players
  from one side, and DK rejects it.

**The solve** (`velocity/dfs/showdown.py`) is exact, not heuristic:

1. Dominance-prune the pool. A player beaten on both prices and on points by
   six *same-team* players can never appear in a six-man roster — any roster
   using him can swap in a dominator it is not already using, at no more
   salary, no fewer points, and identical team counts. Same-team only,
   because the two-team rule makes a cross-team swap unsafe.
2. Build the choose-5 flex frontier once, keyed by `(home_count, budget)` —
   the team count rides in the state so the two-team rule is answered
   exactly rather than repaired afterwards.
3. Enumerate captains against that frontier at `cap − captain_salary`,
   requiring at least one flex from the other side when the captain would
   otherwise make it a one-team roster. A captain who turns up inside his own
   best five triggers one rebuild without him.

Pinned against brute force on random pools, plus targeted tests for the two
rules (`tests/test_dfs_showdown.py`). A live 95-player MLB board solves in
about 0.1 seconds.

**Projections** are the flex-slot numbers; the 1.5x is applied by the
optimizer, never baked into the input. MLB uses the contextual model
(`docs/DFS_MODEL.md`); football uses the FantasyPros consensus.

## Eligibility, and one live bug worth remembering

`velocity.dfs.pipeline.eligible_board` drops players who cannot take the
field, on every MLB format:

* a DK status of IL/IR/O/OUT/NA/SUSP removes the player — **unless** DK also
  flags him probable for tonight. The probable marker (`playerGameAttributes`
  id 1) is a statement about *this game*; the status is a roster designation
  DK has not cleared. Live proof: on the 8/25 LAD @ ATL board DK carried
  Tyler Glasnow as `IL` **and** flagged him probable, and statsapi had him
  announced as the starter. The old ordering dropped the game's actual
  starting pitcher from both the classic and the showdown pool.
* the P pool keeps only DK's flagged probables — DK lists every rostered
  pitcher, and a non-probable never starts.

## Building it

```bash
python scripts/build_dfs_lineup.py --league mlb \
    --salaries artifacts/dk_salaries/dk_salaries_mlb_<stamp>.parquet \
    --fp artifacts/mlbstats/mlb_player_stats_<stamp>.parquet \
    --out artifacts/slate
```

Solves every classic slate grouping (main / Early / Night / Turbo) **and**
every showdown board on the snapshot. Showdown output is a separate
`dfs_showdown_{league}_{stamp}.parquet` carrying all solved boards, with a
card and captions rendered for the strongest one. `--no-showdown` skips it.
