"""Card identity for the summer leagues — codes + colors, no marks.

The NCAAF posture: team identity on MLB/WNBA cards is an abbreviation block
in a brand-adjacent color, never a logo (licensing hygiene — MLB and WNBA
marks are not open-use the way NFL club logos have been treated here).
Keys are the providers' full display names — statsapi/ESPN and The Odds API
agree on them — so alias resolution is exact-match with the normalized
fallback the resolver already applies.

Colors are picked for legibility on the dark card surface (a near-black
brand color would vanish; those teams wear their secondary).
"""

from __future__ import annotations

# name → (code, hex). MLB: 30 clubs (the Athletics are city-less on every
# feed since the Sacramento years).
MLB_IDENTITY: dict[str, tuple[str, str]] = {
    "Arizona Diamondbacks": ("ARI", "#A71930"),
    "Athletics": ("ATH", "#EFB21E"),
    "Atlanta Braves": ("ATL", "#CE1141"),
    "Baltimore Orioles": ("BAL", "#DF4601"),
    "Boston Red Sox": ("BOS", "#BD3039"),
    "Chicago Cubs": ("CHC", "#0E3386"),
    "Chicago White Sox": ("CWS", "#C4CED4"),
    "Cincinnati Reds": ("CIN", "#C6011F"),
    "Cleveland Guardians": ("CLE", "#E31937"),
    "Colorado Rockies": ("COL", "#33006F"),
    "Detroit Tigers": ("DET", "#FA4616"),
    "Houston Astros": ("HOU", "#EB6E1F"),
    "Kansas City Royals": ("KC", "#004687"),
    "Los Angeles Angels": ("LAA", "#BA0021"),
    "Los Angeles Dodgers": ("LAD", "#005A9C"),
    "Miami Marlins": ("MIA", "#00A3E0"),
    "Milwaukee Brewers": ("MIL", "#FFC52F"),
    "Minnesota Twins": ("MIN", "#0C2341"),
    "New York Mets": ("NYM", "#FF5910"),
    "New York Yankees": ("NYY", "#1C2841"),
    "Philadelphia Phillies": ("PHI", "#E81828"),
    "Pittsburgh Pirates": ("PIT", "#FDB827"),
    "San Diego Padres": ("SD", "#FFC425"),
    "San Francisco Giants": ("SF", "#FD5A1E"),
    "Seattle Mariners": ("SEA", "#005C5C"),
    "St. Louis Cardinals": ("STL", "#C41E3A"),
    "Tampa Bay Rays": ("TB", "#8FBCE6"),
    "Texas Rangers": ("TEX", "#003278"),
    "Toronto Blue Jays": ("TOR", "#134A8E"),
    "Washington Nationals": ("WSH", "#AB0003"),
}

# WNBA: the 2026 fifteen (Valkyries 2025; Tempo + Fire 2026).
WNBA_IDENTITY: dict[str, tuple[str, str]] = {
    "Atlanta Dream": ("ATL", "#E31837"),
    "Chicago Sky": ("CHI", "#418FDE"),
    "Connecticut Sun": ("CON", "#DC4405"),
    "Dallas Wings": ("DAL", "#C4D600"),
    "Golden State Valkyries": ("GSV", "#963CBD"),
    "Indiana Fever": ("IND", "#FFCD00"),
    "Las Vegas Aces": ("LVA", "#BA0C2F"),
    "Los Angeles Sparks": ("LAS", "#FFC72C"),
    "Minnesota Lynx": ("MIN", "#78BE20"),
    "New York Liberty": ("NYL", "#6ECEB2"),
    "Phoenix Mercury": ("PHX", "#CB6015"),
    "Portland Fire": ("POR", "#E03A3E"),
    "Seattle Storm": ("SEA", "#2C5234"),
    "Toronto Tempo": ("TOR", "#753BBD"),
    "Washington Mystics": ("WAS", "#0C2340"),
}

# NHL: 32 clubs, NHL's own abbreviations (the datasets' team keys). Both
# Utah identities and the accented Montréal spelling resolve. Dark-wearing
# clubs (LAK) use a legible secondary.
NHL_IDENTITY: dict[str, tuple[str, str]] = {
    "Anaheim Ducks": ("ANA", "#F47A38"),
    "Arizona Coyotes": ("ARI", "#8C2633"),
    "Boston Bruins": ("BOS", "#FFB81C"),
    "Buffalo Sabres": ("BUF", "#3B6EDC"),
    "Calgary Flames": ("CGY", "#D2001C"),
    "Carolina Hurricanes": ("CAR", "#CE1126"),
    "Chicago Blackhawks": ("CHI", "#CF0A2C"),
    "Colorado Avalanche": ("COL", "#8A2432"),
    "Columbus Blue Jackets": ("CBJ", "#4A6E9C"),
    "Dallas Stars": ("DAL", "#006847"),
    "Detroit Red Wings": ("DET", "#CE1126"),
    "Edmonton Oilers": ("EDM", "#FF4C00"),
    "Florida Panthers": ("FLA", "#C8102E"),
    "Los Angeles Kings": ("LAK", "#A2AAAD"),
    "Minnesota Wild": ("MIN", "#2E7D5B"),
    "Montreal Canadiens": ("MTL", "#AF1E2D"),
    "Montréal Canadiens": ("MTL", "#AF1E2D"),
    "Nashville Predators": ("NSH", "#FFB81C"),
    "New Jersey Devils": ("NJD", "#CE1126"),
    "New York Islanders": ("NYI", "#3D7DC6"),
    "New York Rangers": ("NYR", "#3E66C4"),
    "Ottawa Senators": ("OTT", "#DA1A32"),
    "Philadelphia Flyers": ("PHI", "#F74902"),
    "Pittsburgh Penguins": ("PIT", "#FCB514"),
    "San Jose Sharks": ("SJS", "#008599"),
    "Seattle Kraken": ("SEA", "#68A2B9"),
    "St Louis Blues": ("STL", "#3D6DD6"),
    "St. Louis Blues": ("STL", "#3D6DD6"),
    "Tampa Bay Lightning": ("TBL", "#3B6EDC"),
    "Toronto Maple Leafs": ("TOR", "#3F63AE"),
    "Utah Hockey Club": ("UTA", "#6CACE4"),
    "Utah Mammoth": ("UTA", "#6CACE4"),
    "Vancouver Canucks": ("VAN", "#00843D"),
    "Vegas Golden Knights": ("VGK", "#B4975A"),
    "Washington Capitals": ("WSH", "#C8102E"),
    "Winnipeg Jets": ("WPG", "#5578B5"),
}

IDENTITY_BY_LEAGUE = {"mlb": MLB_IDENTITY, "wnba": WNBA_IDENTITY,
                      "nhl": NHL_IDENTITY}


def league_identity(
    league: str, teams: list[str]
) -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    """``(aliases, colors, code_to_team)`` for the card pipeline.

    Mirrors the NCAAF identity contract: provider name → code aliases, code →
    hex color, code → the datasets' team key. A team the table doesn't know
    falls back to an upper-cased trigram with no color (the renderer's
    neutral) — it never blocks a card.
    """
    table = IDENTITY_BY_LEAGUE.get(league, {})
    aliases: dict[str, str] = {}
    colors: dict[str, str] = {}
    code_to_team: dict[str, str] = {}
    for name in teams:
        code, color = table.get(
            str(name), (str(name)[:3].upper() or "TBD", "")
        )
        aliases[str(name)] = code
        if color:
            colors[code] = color
        code_to_team[code] = str(name)
    return aliases, colors, code_to_team
