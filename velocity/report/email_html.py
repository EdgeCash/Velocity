"""Slate email — the daily plays as one self-contained HTML message.

The delivery layer for the live slate: the workflow renders the persisted slate
parquets (game plays, props, parlays) into a single HTML body + subject line and
mails it after each run. Email clients ignore stylesheets, so every style is
inline and the layout is plain tables — boring on purpose, so it renders the
same in Gmail on a phone as anywhere else.

Pure and offline-testable: :func:`render_slate_email` takes the frames the
runner persisted and returns ``(subject, html)``. Reading the parquet folder and
sending are the thin script/workflow's job. An empty slate still renders — a
"no plays today" email is the heartbeat that says the pipeline ran.
"""

from __future__ import annotations

from collections.abc import Mapping

import pandas as pd

_BODY_STYLE = "font-family:Arial,Helvetica,sans-serif;color:#1a1a1a;font-size:14px"
_H2_STYLE = "font-size:16px;margin:18px 0 6px"
_TABLE_STYLE = "border-collapse:collapse;width:100%"
_CELL_STYLE = "border:1px solid #d0d0d0;padding:4px 8px;text-align:left"
_NOTE_STYLE = "color:#666;font-size:12px;margin:6px 0"


def _matchup_map(games_map: pd.DataFrame | None) -> dict[str, str]:
    """``game_id → "Away @ Home"`` from the persisted games map (may be None)."""
    if games_map is None or games_map.empty:
        return {}
    return {
        str(row["game_id"]): f"{row['away_team']} @ {row['home_team']}"
        for row in games_map.to_dict("records")
    }


def _with_matchup(frame: pd.DataFrame, matchups: Mapping[str, str]) -> pd.DataFrame:
    """Readable copy: a leading ``matchup`` column replacing the opaque game id."""
    out = frame.copy()
    drop = [c for c in ("league", "generated_at") if c in out.columns]
    out = out.drop(columns=drop)
    if "game_id" in out.columns and matchups:
        out.insert(0, "matchup", out["game_id"].astype(str).map(matchups).fillna(""))
        out = out.drop(columns=["game_id"])
    return out


def _table(frame: pd.DataFrame) -> str:
    html = frame.to_html(index=False, border=0, na_rep="")
    # Inline every cell style — email clients strip <style> blocks.
    html = html.replace("<table", f'<table style="{_TABLE_STYLE}"', 1)
    html = html.replace("<th>", f'<th style="{_CELL_STYLE};background:#f2f2f2">')
    return html.replace("<td>", f'<td style="{_CELL_STYLE}">')


def _section(title: str, frame: pd.DataFrame | None, empty_note: str) -> str:
    body = _table(frame) if frame is not None and not frame.empty else (
        f'<p style="{_NOTE_STYLE}">{empty_note}</p>'
    )
    return f'<h2 style="{_H2_STYLE}">{title}</h2>\n{body}'


def render_slate_email(
    plays: pd.DataFrame | None,
    props: pd.DataFrame | None,
    parlays: pd.DataFrame | None,
    *,
    games_map: pd.DataFrame | None = None,
    league: str = "mlb",
    generated_at: object = None,
) -> tuple[str, str]:
    """Render the day's slate as ``(subject, html_body)``.

    ``plays``/``props``/``parlays`` are the runner's persisted frames (any may be
    ``None`` or empty); ``games_map`` (the persisted ``games_*`` parquet) turns
    opaque provider game ids into "Away @ Home" matchups. The subject carries the
    play counts so the inbox line alone says whether today is worth opening.
    """
    matchups = _matchup_map(games_map)
    n_plays = 0 if plays is None else len(plays)
    n_props = 0 if props is None else len(props)
    n_parlays = 0 if parlays is None else len(parlays)

    when = None if generated_at is None else pd.Timestamp(generated_at)  # type: ignore[arg-type]
    date_tag = f" ({when.strftime('%b %-d')})" if when is not None else ""
    tag = league.upper()
    if n_plays or n_props or n_parlays:
        subject = (
            f"Velocity {tag}: {n_plays} play{'s' if n_plays != 1 else ''}, "
            f"{n_props} prop{'s' if n_props != 1 else ''}, "
            f"{n_parlays} parlay{'s' if n_parlays != 1 else ''}{date_tag}"
        )
    else:
        subject = f"Velocity {tag}: no plays today{date_tag}"

    sections = [
        _section(
            "Game plays",
            None if plays is None else _with_matchup(plays, matchups),
            "No game bet cleared the edge threshold.",
        ),
        _section(
            "Player props",
            None if props is None else _with_matchup(props, matchups),
            "No prop cleared the edge threshold.",
        ),
        _section(
            "Parlays",
            None if parlays is None else _with_matchup(parlays, matchups),
            "No parlay cleared the combined-EV bar.",
        ),
    ]
    parlay_note = (
        f'<p style="{_NOTE_STYLE}">same_game=True parlays assume the product payout; '
        "books reprice correlated SGPs, so treat that EV as an upper bound.</p>"
        if n_parlays
        else ""
    )
    generated_note = (
        f'<p style="{_NOTE_STYLE}">Generated {when} UTC. Stakes assume the workflow '
        "bankroll; place at the listed number or better — a worse number erodes the "
        "edge. Nothing is placed automatically.</p>"
        if when is not None
        else f'<p style="{_NOTE_STYLE}">Place at the listed number or better; nothing '
        "is placed automatically.</p>"
    )
    html = (
        f'<div style="{_BODY_STYLE}">\n'
        + "\n".join(sections)
        + f"\n{parlay_note}{generated_note}</div>\n"
    )
    return subject, html
