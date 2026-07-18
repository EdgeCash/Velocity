"""Backtesting — walk-forward, point-in-time evaluation of the whole pipeline.

A backtest is only honest if it is **walk-forward** (train on weeks ≤ t, predict
week t+1, never fit on the future) and **point-in-time** (every feature is
reconstructable as-of kickoff). This package runs that loop over a season,
wiring projections → historical lines → staked bets → graded bankroll, and hands
the results to :mod:`velocity.eval.metrics`.
"""
