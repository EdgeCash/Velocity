"""What stat keys the FantasyPros projections feed actually serves.

Two BettingPros prop slugs are worth pricing and cannot be mapped yet:
``rushing-attempts`` (57 rows on the 2026-09-16 NFL board, the largest
unmapped slug) and ``passing-attempts`` (28). Both are ordinary count props
and both have banked actuals to calibrate against — ``player_weeks`` carries
``carries`` and ``attempts``. The one missing piece is a per-player
*projection*, and nothing in this codebase reads a rush-attempt or
pass-attempt key today, so whether the feed serves one is an open question.

It is not a question to answer by reasoning. ``BP_PROP_SLUG_TO_MARKET`` was
originally written from a spec read, and a test then pinned a guess so firmly
that it held a real bug in place for three weeks (#207). So this asks the
feed.

``FP_API_KEY`` is a GitHub Actions secret the sandbox never sees, which is why
this runs in CI rather than locally. The key travels in the ``x-api-key``
header, not the URL, so there is no credential echo to scrub here — but this
prints aggregates only, never the raw payload, because a projections dump is
paid provider data and an Actions log is not a private place.

    python scripts/inspect_fp_stat_keys.py --sport nfl --season 2026 --week 3

Read the KEYS THAT LOOK LIKE VOLUME COUNTS section: if a rush-attempt or
pass-attempt key is there with sensible non-zero coverage, the two slugs can
be mapped. If it is absent, they stay unmapped and this script is the record
of why.
"""

from __future__ import annotations

import argparse
from collections import Counter
from typing import Any

import pandas as pd
from velocity.ingest.fantasypros import FantasyProsClient, normalize_projections
from velocity.models.props_football import _TD_STATS, FP_STAT_TO_MARKET

# Substrings that mark a key as a plausible attempt/completion volume stat.
# Deliberately loose — the point is to see the candidates, not to pre-judge
# which spelling the feed uses.
_VOLUME_HINTS = ("att", "cmp", "comp", "carr", "rush_a", "pass_a", "target", "tgt")


def stat_table(frame: pd.DataFrame) -> pd.DataFrame:
    """Per stat key: how many players carry it, how many non-zero, and its mean."""
    value = pd.to_numeric(frame["value"], errors="coerce")
    grouped = frame.assign(_v=value).groupby("stat")["_v"]
    out = pd.DataFrame(
        {
            "rows": grouped.size(),
            "non_zero": grouped.apply(lambda s: int((s.fillna(0) != 0).sum())),
            "mean": grouped.mean().round(3),
            "max": grouped.max().round(2),
        }
    ).reset_index()
    out["mapped_to"] = out["stat"].map(
        lambda k: FP_STAT_TO_MARKET.get(str(k), "(td component)"
                                        if str(k) in _TD_STATS else "")
    )
    return out.sort_values(["mapped_to", "non_zero"], ascending=[True, False])


def looks_like_volume(stat: str) -> bool:
    key = str(stat).lower()
    return any(hint in key for hint in _VOLUME_HINTS)


def describe(table: pd.DataFrame, sport: str, season: int, week: int) -> list[str]:
    lines = [
        f"FantasyPros {sport.upper()} projections — season {season}, week {week}",
        f"{len(table)} distinct stat key(s)",
        "",
        "ALL KEYS SERVED",
    ]
    for r in table.to_dict("records"):
        mark = "mapped  " if r["mapped_to"] else "UNMAPPED"
        target = f" -> {r['mapped_to']}" if r["mapped_to"] else ""
        lines.append(
            f"    {mark} {str(r['stat']):<22} {int(r['non_zero']):>5} non-zero "
            f"of {int(r['rows']):>5}  mean {r['mean']}  max {r['max']}{target}"
        )

    candidates = table[table["stat"].map(looks_like_volume)]
    lines += ["", "KEYS THAT LOOK LIKE VOLUME COUNTS (the open question)"]
    if candidates.empty:
        lines.append("    none — the feed serves no attempt/completion key.")
        lines.append("    rushing-attempts and passing-attempts stay unmapped;")
        lines.append("    mapping them would abstain silently rather than honestly.")
    else:
        for r in candidates.to_dict("records"):
            state = f"already mapped to {r['mapped_to']}" if r["mapped_to"] else "AVAILABLE"
            lines.append(
                f"    {str(r['stat']):<22} {int(r['non_zero']):>5} non-zero, "
                f"mean {r['mean']}  [{state}]"
            )
        lines.append("")
        lines.append("    An AVAILABLE rush-attempt key unblocks BP 'rushing-attempts'")
        lines.append("    (57 rows); a pass-attempt key unblocks 'passing-attempts' (28).")
        lines.append("    Calibrate against player_weeks 'carries' / 'attempts' first.")
    return lines


def raw_key_census(payload: Any) -> Counter[str]:
    """Every key seen on a player object, straight off the payload.

    ``normalize_projections`` drops non-numeric values, so a key whose value is
    a string ("18/25") would vanish from the melted frame. This census sees it,
    which matters: a completions projection served as part of a compound
    string is exactly the kind of thing the long frame hides.
    """
    seen: Counter[str] = Counter()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if any(k in node for k in ("name", "player_name", "fpid", "player_id")):
                seen.update(node.keys())
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(payload)
    return seen


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Report the stat keys the FantasyPros projections feed serves"
    )
    parser.add_argument("--sport", default="nfl")
    parser.add_argument("--season", type=int, required=True)
    parser.add_argument("--week", type=int, default=0,
                        help="0 = full-season projections")
    parser.add_argument("--position", default="ALL")
    args = parser.parse_args()

    client = FantasyProsClient.from_env()
    payload = client.raw_projections(
        args.sport, args.season, position=args.position, week=args.week
    )
    frame = normalize_projections(payload, season=args.season, week=args.week)

    if frame.empty:
        print("the feed returned no projection rows — nothing to report")
        return

    table = stat_table(frame)
    for line in describe(table, args.sport, args.season, args.week):
        print(line)

    melted = set(table["stat"].astype(str))
    census = raw_key_census(payload)
    hidden = {
        k: n for k, n in census.items()
        if k not in melted and looks_like_volume(k)
    }
    print("\nVOLUME-LIKE KEYS PRESENT IN THE PAYLOAD BUT NOT IN THE LONG FRAME")
    if hidden:
        for key, count in sorted(hidden.items(), key=lambda kv: -kv[1]):
            print(f"    {key:<22} on {count} player object(s) — non-numeric value,"
                  f" so normalize_projections drops it")
    else:
        print("    none (every volume-like key the payload carries survives the melt)")


if __name__ == "__main__":
    main()
