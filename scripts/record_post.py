"""Append a post to the engagement ledger, or refresh its metrics.

The experiment's data entry point. Engagement numbers come from the
platform by hand or by export — nothing here calls a social API.

    # log a post as it goes out (metrics filled in later)
    python scripts/record_post.py --style dfs --post-id 1234 \
        --followers 250 --reference sheet_mlb_...png --n-plays 5

    # fill in the numbers a day later
    python scripts/record_post.py --post-id 1234 --likes 40 --impressions 3100
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
from velocity.report.engagement import (
    POST_COLUMNS,
    STYLES,
    compare_by_context,
    compare_styles,
    empty_ledger,
    minimum_posts_for_signal,
)

LEDGER = Path("artifacts/engagement/posts.parquet")


def main() -> None:
    parser = argparse.ArgumentParser(description="Record a post / its engagement")
    parser.add_argument("--ledger", default=str(LEDGER))
    parser.add_argument("--post-id", required=False)
    parser.add_argument("--style", choices=STYLES)
    parser.add_argument("--league", default="")
    parser.add_argument("--reference", default="")
    parser.add_argument("--n-plays", type=float, default=None)
    parser.add_argument("--followers", type=float, default=None)
    parser.add_argument("--prior-day-result", choices=["green", "red", "quiet"],
                        default=None)
    for metric in ("impressions", "likes", "reposts", "replies", "profile-clicks"):
        parser.add_argument(f"--{metric}", type=float, default=None)
    parser.add_argument("--report", action="store_true",
                        help="print the comparison tables and exit")
    args = parser.parse_args()

    path = Path(args.ledger)
    ledger = pd.read_parquet(path) if path.exists() else empty_ledger()

    if args.report:
        if ledger.empty:
            print("ledger is empty — nothing to compare yet")
            return
        print("=== per-style (medians; engagement is heavy-tailed) ===")
        print(compare_styles(ledger).to_string(index=False))
        print("\n=== style x prior-day result — the forgiveness cell ===")
        print(compare_by_context(ledger).to_string(index=False))
        print("\n=== posts needed before this means anything ===")
        print(minimum_posts_for_signal(ledger).to_string(index=False))
        return

    if not args.post_id:
        raise SystemExit("--post-id is required (or pass --report)")
    post_id = str(args.post_id)
    updates = {
        "style": args.style, "league": args.league or None,
        "reference": args.reference or None, "n_plays": args.n_plays,
        "followers": args.followers, "prior_day_result": args.prior_day_result,
        "impressions": args.impressions, "likes": args.likes,
        "reposts": args.reposts, "replies": args.replies,
        "profile_clicks": args.profile_clicks,
    }
    updates = {k: v for k, v in updates.items() if v is not None}

    existing = ledger["post_id"].astype(str) == post_id if not ledger.empty else None
    if existing is not None and existing.any():
        for key, value in updates.items():
            ledger.loc[existing, key] = value
        print(f"updated post {post_id}: {sorted(updates)}")
    else:
        if not args.style:
            raise SystemExit("--style is required when logging a NEW post")
        row = dict.fromkeys(POST_COLUMNS)
        row.update({"post_id": post_id,
                    "posted_at": pd.Timestamp(datetime.now(UTC)).tz_localize(None)})
        row.update(updates)
        ledger = pd.concat([ledger, pd.DataFrame([row])], ignore_index=True)
        print(f"logged {args.style} post {post_id}")

    path.parent.mkdir(parents=True, exist_ok=True)
    ledger.to_parquet(path, index=False)
    print(f"ledger now holds {len(ledger)} post(s) -> {path}")


if __name__ == "__main__":
    main()
