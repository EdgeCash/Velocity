-- Per-team pass and rush splits, both sides of the ball: the shape the
-- one-number rating hides. `epa_adjusted` corrects the raw average by the
-- units actually faced, centered so zero is league average.
select * from 'sources/velocity/data/unit_splits.parquet'
