"""Execution venues.

One module per place we can send an order. The platform is meant to trade
Bitunix futures, Deriv forex and DEX Screener tokens, and those three have
almost nothing in common at the wire level - different auth, different order
types, different ideas of what a "position" even is.

So nothing above this package is allowed to know which venue it is talking to.
`base.py` defines the contract; each venue module implements it and keeps its
own weirdness inside its own file.
"""
