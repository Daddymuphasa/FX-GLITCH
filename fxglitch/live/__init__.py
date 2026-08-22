"""The live layer: what turns a backtested strategy into a running process.

    state.py    what survives a crash, and the ids that make a retry safe
    guards.py   the reasons to refuse to trade
    runner.py   the loop, and the translation from strategy intent to orders

The design constraint that shapes all three: the strategy classes in
strategies/ are imported and driven AS THEY ARE. Not reimplemented, not
subclassed, not adapted. If live logic is ever a second copy of backtest
logic, the backtest stops being evidence about the thing that is running.
"""
