-- The player-prop board, with the venue resolved the way the game board
-- resolves it. Same reason: no board in this genre shows a bettor a raw
-- feed code, and doing it here means every page inherits the name.
select
  * exclude (note),
  -- `note` is written only on a row the gate declined to fund, so on a
  -- slate where everything cleared pandas writes it as all-NaN float64 and
  -- a page filtering on it dies converting '' to DOUBLE (docs/SITE.md).
  nullif(cast(note as varchar), 'nan') as note,
  case book
    when 'draftkings' then 'DraftKings'
    when 'fanduel' then 'FanDuel'
    when 'betmgm' then 'BetMGM'
    when 'betrivers' then 'BetRivers'
    when 'pointsbetus' then 'PointsBet'
    when 'williamhill_us' then 'Caesars'
    when 'betonlineag' then 'BetOnline'
    when 'lowvig' then 'LowVig'
    when 'bovada' then 'Bovada'
    when 'mybookieag' then 'MyBookie'
    when 'betus' then 'BetUS'
    when 'fanatics' then 'Fanatics'
    when 'espnbet' then 'ESPN BET'
    when 'hardrockbet' then 'Hard Rock'
    when 'ballybet' then 'Bally Bet'
    when 'pinnacle' then 'Pinnacle'
    when 'novig' then 'Novig'
    when 'prophetx' then 'ProphetX'
    when 'kalshi' then 'Kalshi'
    when 'polymarket' then 'Polymarket'
    else book
  end as venue_label,
  case market
    when 'pass_yards' then 'Pass yds'
    when 'rush_yards' then 'Rush yds'
    when 'receiving_yards' then 'Rec yds'
    when 'receptions' then 'Receptions'
    when 'pass_tds' then 'Pass TDs'
    when 'rush_tds' then 'Rush TDs'
    when 'rec_tds' then 'Rec TDs'
    when 'anytime_td' then 'Anytime TD'
    else replace(market, '_', ' ')
  end as market_label
from 'sources/velocity/data/props.parquet'
