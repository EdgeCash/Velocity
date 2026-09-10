-- Open positions, with the venue named the way the board names it. The
-- ledger stores the raw book code; no page should show one.
select
  * exclude (book),
  book as book_key,
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
  end as book
from 'sources/velocity/data/ledger_open.parquet'
