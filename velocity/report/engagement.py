"""Post ledger + engagement math — measuring which post style actually lands.

The experiment: one account, two post styles (``dfs`` lineups daily, ``wager``
plays only when the publish gate opens). The question is which style earns
traction — and answering it naively is how you fool yourself, because the two
styles differ in almost every way that moves raw engagement:

* **Cadence.** DFS posts every day, wager posts only on qualifying days.
  Summed likes would just measure posting volume.
* **Timing.** Slate posts land at different hours; reach varies by hour.
* **Outcome mood.** A wager post the day after a red night is read
  differently from one after a green night. Comparing an unpaired mix of
  those to a DFS baseline confounds style with results.

So the ledger records, per post, the style, the timestamp, the follower
count AT POST TIME, and the day's result context — and the comparison is
per-post, follower-normalized, and split by outcome context. Engagement
numbers arrive by hand or by export; nothing here calls a social API.

Pure functions of frames; offline-testable.
"""

from __future__ import annotations

import pandas as pd

STYLES = ("dfs", "wager")

# One row per published post.
POST_COLUMNS = [
    "post_id",        # stable id (the platform's, or our own)
    "style",          # "dfs" | "wager"
    "posted_at",      # timestamp (UTC)
    "league",
    "reference",      # the artifact behind it (sheet file, lineup stamp, ...)
    "n_plays",        # wager: plays in the post; dfs: lineups/bats shown
    "followers",      # follower count AT POST TIME — the normalizer
    "prior_day_result",  # "green" | "red" | "quiet" — the mood it landed into
    "impressions",
    "likes",
    "reposts",
    "replies",
    "profile_clicks",
]

_METRICS = ("impressions", "likes", "reposts", "replies", "profile_clicks")


def empty_ledger() -> pd.DataFrame:
    """A typed, empty post ledger."""
    return pd.DataFrame({
        "post_id": pd.Series(dtype=str),
        "style": pd.Series(dtype=str),
        "posted_at": pd.Series(dtype="datetime64[ns]"),
        "league": pd.Series(dtype=str),
        "reference": pd.Series(dtype=str),
        "n_plays": pd.Series(dtype="float64"),
        "followers": pd.Series(dtype="float64"),
        "prior_day_result": pd.Series(dtype=str),
        **{m: pd.Series(dtype="float64") for m in _METRICS},
    })


def engagement_rate(ledger: pd.DataFrame) -> pd.DataFrame:
    """Add per-post normalized rates.

    ``engagement`` is the interaction count (likes + reposts + replies);
    ``per_follower`` divides it by the follower count at post time, which is
    the only way a post from a 200-follower week compares to one from a
    2,000-follower week. ``per_impression`` is the platform-native rate where
    impressions are known.
    """
    frame = ledger.copy()
    for column in _METRICS:
        if column not in frame.columns:
            frame[column] = float("nan")
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame["engagement"] = (
        frame[["likes", "reposts", "replies"]].fillna(0.0).sum(axis=1)
    )
    if "followers" not in frame.columns:
        frame["followers"] = float("nan")
    followers = pd.to_numeric(frame["followers"], errors="coerce")
    frame["per_follower"] = frame["engagement"] / followers.where(followers > 0)
    impressions = frame["impressions"]
    frame["per_impression"] = frame["engagement"] / impressions.where(impressions > 0)
    return frame


def compare_styles(ledger: pd.DataFrame) -> pd.DataFrame:
    """Per-style medians of the normalized rates — the headline table.

    Medians, not means: engagement is heavy-tailed, and one viral post would
    otherwise decide the experiment.
    """
    if ledger.empty:
        return pd.DataFrame(columns=["style", "posts", "median_engagement",
                                     "median_per_follower", "median_per_impression"])
    frame = engagement_rate(ledger)
    rows = []
    for style, group in frame.groupby("style"):
        rows.append({
            "style": str(style),
            "posts": int(len(group)),
            "median_engagement": round(float(group["engagement"].median()), 2),
            "median_per_follower": round(
                float(group["per_follower"].median()), 5),
            "median_per_impression": round(
                float(group["per_impression"].median()), 5),
        })
    return pd.DataFrame(rows).sort_values("style").reset_index(drop=True)


def compare_by_context(ledger: pd.DataFrame) -> pd.DataFrame:
    """Style × prior-day-result — does a red night cost each style the same?

    This is the cell the whole experiment turns on. If wager posts collapse
    after a red day while DFS posts hold, that is the forgiveness gap showing
    up in our own numbers rather than in someone else's market report.
    """
    if ledger.empty:
        return pd.DataFrame(columns=["style", "prior_day_result", "posts",
                                     "median_per_follower"])
    frame = engagement_rate(ledger)
    rows = []
    for (style, context), group in frame.groupby(["style", "prior_day_result"]):
        rows.append({
            "style": str(style),
            "prior_day_result": str(context),
            "posts": int(len(group)),
            "median_per_follower": round(
                float(group["per_follower"].median()), 5),
        })
    return (pd.DataFrame(rows)
            .sort_values(["style", "prior_day_result"])
            .reset_index(drop=True))


def cadence(ledger: pd.DataFrame) -> pd.DataFrame:
    """Posts per style per week — the confound to state out loud, not hide."""
    if ledger.empty:
        return pd.DataFrame(columns=["style", "weeks", "posts", "posts_per_week"])
    frame = ledger.copy()
    frame["posted_at"] = pd.to_datetime(frame["posted_at"], errors="coerce")
    frame = frame.dropna(subset=["posted_at"])
    if frame.empty:
        return pd.DataFrame(columns=["style", "weeks", "posts", "posts_per_week"])
    span_days = max((frame["posted_at"].max() - frame["posted_at"].min()).days, 1)
    weeks = max(span_days / 7.0, 1 / 7.0)
    rows = []
    for style, group in frame.groupby("style"):
        rows.append({
            "style": str(style),
            "weeks": round(weeks, 2),
            "posts": int(len(group)),
            "posts_per_week": round(len(group) / weeks, 2),
        })
    return pd.DataFrame(rows).sort_values("style").reset_index(drop=True)


def minimum_posts_for_signal(
    ledger: pd.DataFrame, *, effect: float = 0.30
) -> pd.DataFrame:
    """Roughly how many posts per style before a difference means anything.

    Uses each style's observed spread to estimate the posts needed to detect
    a relative ``effect`` difference in per-follower engagement (two-sided,
    the usual 80% power rule of thumb: n ≈ 16·σ²/Δ²). Stated as a guard
    against calling the experiment three days in — engagement is noisy and
    the temptation to declare a winner early is the real risk here.
    """
    frame = engagement_rate(ledger)
    rows = []
    for style, group in frame.groupby("style"):
        values = group["per_follower"].dropna()
        if len(values) < 2 or values.mean() == 0:
            rows.append({"style": str(style), "posts": int(len(group)),
                         "needed_per_style": None})
            continue
        cv = float(values.std(ddof=1) / abs(values.mean()))
        needed = int(round(16.0 * (cv ** 2) / (effect ** 2)))
        rows.append({"style": str(style), "posts": int(len(group)),
                     "observed_cv": round(cv, 3),
                     "needed_per_style": max(needed, 8)})
    return pd.DataFrame(rows)
