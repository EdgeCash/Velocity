"""Evaluation — calibration, proper scoring, and bankroll metrics.

A model is worthless until it is measured the way it will be used. These are the
metrics that decide whether a phase is actually done (DESIGN §7.2), in priority
order: **calibration** (a prerequisite for Kelly to work at all), **CLV** (the
primary skill signal), proper scores (**Brier**/**log-loss**), and then the
bankroll story (**ROI**, hit rate, drawdown).
"""
