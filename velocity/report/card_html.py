"""Render per-game matchup cards to a standalone HTML page.

Draws the card dicts from :mod:`velocity.report.cards` — team header with logos
and records, the probable starters with headshots and season lines, a
Model Projection panel, and the Moneyline / Run Line / Total recommendation bar
(PLAY / LEAN / PASS). Logos and headshots load from MLB's official static CDN by
team / player id; when an id is missing the card falls back to text, so it always
renders.

Server-side string rendering (no client JS): the emitted file is a plain,
self-contained HTML artifact. The CDN images load when it's opened in a browser
(the pipeline output); they are blocked inside the sandboxed Artifact previewer.
"""

from __future__ import annotations

import html
from pathlib import Path
from typing import Any


def team_logo_url(team_id: str | None) -> str | None:
    return None if team_id is None else f"https://www.mlbstatic.com/team-logos/{team_id}.svg"


def headshot_url(player_id: str | None) -> str | None:
    return None if player_id is None else f"https://midfield.mlbstatic.com/v1/people/{player_id}/spots/120"


def _logo(team_id: str | None) -> str:
    url = team_logo_url(team_id)
    return "" if url is None else (
        f'<img class="logo" src="{url}" alt="" loading="lazy" '
        f"onerror=\"this.style.display='none'\">"
    )


def _mug(player_id: str | None) -> str:
    url = headshot_url(player_id)
    return "" if url is None else (
        f'<img class="mug" src="{url}" alt="" loading="lazy" onerror="this.remove()">'
    )


def _sp_line(sp: dict[str, Any]) -> str:
    hand = f' <span class="hand">{html.escape(sp["hand"])}HP</span>' if sp.get("hand") else ""
    line = f'<span class="spline">{html.escape(sp["line"])}</span>' if sp.get("line") else ""
    return f'<b>{html.escape(sp["name"])}</b>{hand}{line}'


def _record(team: dict[str, Any]) -> str:
    return f'<span class="rec">{html.escape(team["record"])}</span>' if team.get("record") else ""


def _rec_cell(rec: dict[str, Any]) -> str:
    call = str(rec["call"]).lower()
    pick = html.escape(str(rec["pick"]))
    return (
        f'<div class="rec {call}"><div class="lab">{html.escape(rec["label"])}</div>'
        f'<div class="conf">{float(rec["conf"]):.1f}</div>'
        f'<div class="pick">{pick}</div>'
        f'<div class="badge">{str(rec["call"]).title()}</div></div>'
    )


def _card(c: dict[str, Any]) -> str:
    away, home = c["away"], c["home"]
    p = c["proj"]
    recs = "".join(_rec_cell(r) for r in c["recs"])
    return f"""
  <article class="game">
    <div class="teams">
      <div class="team away">{_logo(away["logo_id"])}
        <div class="id"><span class="abbr">{html.escape(str(away["code"]))}</span>
          <span class="name">{html.escape(away["name"])} {_record(away)}</span></div>
      </div>
      <div class="at">@</div>
      <div class="team home">
        <div class="id"><span class="abbr">{html.escape(str(home["code"]))}</span>
          <span class="name">{_record(home)} {html.escape(home["name"])}</span></div>
        {_logo(home["logo_id"])}
      </div>
    </div>
    <div class="pitchers">
      <div class="sp away">{_mug(c["away_sp"]["id"])}<div>{_sp_line(c["away_sp"])}</div></div>
      <div class="role">SP · SP</div>
      <div class="sp home"><div>{_sp_line(c["home_sp"])}</div>{_mug(c["home_sp"]["id"])}</div>
    </div>
    <div class="proj">
      <h4>Model projection</h4>
      <div class="score">
        <div><div class="n">{p["away_runs"]:.1f}</div><div class="who">{html.escape(str(away["code"]))}</div></div>
        <div class="dash">–</div>
        <div><div class="n">{p["home_runs"]:.1f}</div><div class="who">{html.escape(str(home["code"]))}</div></div>
      </div>
      <div class="pgrid">
        <div class="cell"><span class="k">Proj total</span><span class="v">{p["total"]:.1f}</span></div>
        <div class="cell"><span class="k">Fair line</span><span class="v">{html.escape(p["fair_line"])}</span></div>
        <div class="cell"><span class="k">{html.escape(str(home["code"]))} win</span><span class="v">{p["home_win"]}%</span></div>
      </div>
    </div>
    <div class="recs">{recs}</div>
  </article>"""


_STYLE = """
:root{--bg:#0b0f14;--panel:#131b24;--panel2:#0f161d;--line:#232e3a;--text:#e6edf3;
--muted:#8b98a5;--cyan:#38cfdd;--green:#2ecc71;--amber:#f5b301;--slate:#6b7684;
--mono:ui-monospace,"SF Mono",Menlo,Consolas,monospace;
--sans:system-ui,-apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;}
*{box-sizing:border-box}
body{margin:0;background:radial-gradient(1200px 600px at 50% -10%,#10202b 0%,var(--bg) 55%);
color:var(--text);font-family:var(--sans);-webkit-font-smoothing:antialiased;padding:26px 14px 52px;}
.wrap{max-width:600px;margin:0 auto}
.brand{display:flex;align-items:center;gap:9px;font-weight:700}
.brand .dot{width:9px;height:9px;border-radius:50%;background:var(--cyan);box-shadow:0 0 12px var(--cyan)}
.brand h1{font-size:16px;margin:0}
.sub{color:var(--muted);font-size:12.5px;margin:5px 0 8px}
.legend{display:flex;gap:14px;margin:8px 0 18px;font-size:11px;color:var(--muted);
text-transform:uppercase;letter-spacing:.06em;flex-wrap:wrap}
.legend span{display:inline-flex;align-items:center;gap:6px}
.legend i{width:9px;height:9px;border-radius:2px;display:inline-block}
.game{background:linear-gradient(180deg,var(--panel),var(--panel2));border:1px solid var(--line);
border-radius:14px;margin-bottom:16px;overflow:hidden}
.teams{display:grid;grid-template-columns:1fr auto 1fr;align-items:center;gap:8px;padding:14px 16px 10px}
.team{display:flex;align-items:center;gap:10px;min-width:0}
.team.home{justify-content:flex-end;text-align:right}
.team .id{display:flex;flex-direction:column;gap:1px;min-width:0}
.logo{width:38px;height:38px;flex:0 0 auto;object-fit:contain}
.abbr{font-family:var(--mono);font-size:21px;font-weight:700;letter-spacing:.03em}
.name{font-size:11px;color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.name .rec{font-family:var(--mono);color:var(--text);opacity:.8}
.at{color:var(--muted);font-family:var(--mono)}
.pitchers{display:grid;grid-template-columns:1fr auto 1fr;gap:8px;padding:0 16px 12px;
font-size:12px;color:var(--muted);align-items:center}
.sp{display:flex;align-items:center;gap:8px}
.sp.home{justify-content:flex-end;text-align:right}
.mug{width:30px;height:30px;border-radius:50%;object-fit:cover;background:#22303c;flex:0 0 auto}
.pitchers b{color:var(--text);font-weight:600}
.hand{font-family:var(--mono);font-size:10px;color:var(--cyan)}
.spline{display:block;font-family:var(--mono);font-size:10.5px;opacity:.85}
.role{font-family:var(--mono);font-size:10px;letter-spacing:.05em;color:var(--cyan);text-transform:uppercase}
.proj{padding:12px 16px 4px}
.proj h4{margin:0 0 9px;font-size:10.5px;letter-spacing:.1em;text-transform:uppercase;color:var(--cyan);font-weight:700}
.score{display:flex;align-items:baseline;justify-content:center;gap:12px;margin-bottom:11px}
.score .n{font-family:var(--mono);font-size:29px;font-weight:700;font-variant-numeric:tabular-nums}
.score .dash{color:var(--muted);font-size:19px}
.score .who{font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:.05em}
.pgrid{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;padding-bottom:12px}
.pgrid .cell{display:flex;flex-direction:column;gap:3px}
.pgrid .k{font-size:9.5px;color:var(--muted);text-transform:uppercase;letter-spacing:.05em}
.pgrid .v{font-family:var(--mono);font-size:15px;font-weight:600;font-variant-numeric:tabular-nums}
.recs{display:grid;grid-template-columns:1fr 1fr 1fr;gap:1px;background:var(--line);border-top:1px solid var(--line)}
.rec{background:var(--panel2);padding:11px 6px 12px;text-align:center}
.rec .lab{font-size:9.5px;color:var(--muted);letter-spacing:.07em;text-transform:uppercase}
.rec .conf{font-family:var(--mono);font-size:26px;font-weight:700;line-height:1.15;font-variant-numeric:tabular-nums}
.rec .pick{font-size:11px;color:var(--text);font-family:var(--mono);margin-top:1px}
.rec .badge{display:inline-block;margin-top:6px;font-size:10px;font-weight:700;letter-spacing:.09em;
padding:2px 9px;border-radius:999px;text-transform:uppercase}
.play .conf{color:var(--green)}.play .badge{background:rgba(46,204,113,.14);color:var(--green)}
.lean .conf{color:var(--amber)}.lean .badge{background:rgba(245,179,1,.14);color:var(--amber)}
.pass .conf{color:var(--slate)}.pass .badge{background:rgba(107,118,132,.16);color:var(--slate)}
footer.note{color:var(--muted);font-size:11.5px;line-height:1.6;margin-top:8px;padding:13px 14px;
border:1px dashed var(--line);border-radius:10px;background:var(--panel2)}
footer.note b{color:var(--text)}
"""


def render_cards_page(cards: list[dict[str, Any]], league: str, generated_at: str) -> str:
    """Return the full HTML page for a slate of card dicts."""
    body = "".join(_card(c) for c in cards) or '<p class="sub">No games on the board.</p>'
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Velocity — {html.escape(league.upper())} Cards</title><style>{_STYLE}</style></head>
<body><div class="wrap">
<div class="brand"><span class="dot"></span><h1>Velocity — {html.escape(league.upper())} Cards</h1></div>
<div class="sub">{len(cards)} game(s) · generated {html.escape(generated_at)}</div>
<div class="legend"><span><i style="background:var(--green)"></i>Play · conf ≥ 8</span>
<span><i style="background:var(--amber)"></i>Lean · 4–8</span>
<span><i style="background:var(--slate)"></i>Pass · &lt; 4</span></div>
{body}
<footer class="note"><b>Real:</b> projected runs, total, fair line, win %, and the
Moneyline / Run Line / Total recommendations are live model output. The confidence
scale is a presentation of edge, not a calibrated number. The full descriptive stat
grid (splits, ranks, weather, park factor) is the planned next data layer.</footer>
</div></body></html>"""


def write_cards_html(
    dest: str | Path, cards: list[dict[str, Any]], *, league: str, generated_at: str
) -> Path:
    """Render and write the cards page to ``dest``; return the path."""
    dest = Path(dest)
    dest.write_text(render_cards_page(cards, league, generated_at), encoding="utf-8")
    return dest
