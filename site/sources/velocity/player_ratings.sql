-- Per-player usage and efficiency, with EPA per dropback and CPOE for NFL
-- passers. Every rate carries its volume, and a rate below the conventional
-- minimum is null rather than noisy.
select * from 'sources/velocity/data/player_ratings.parquet'
