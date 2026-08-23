from fxglitch.intelligence import build_context, parse_telegram_signal
from fxglitch.signals import SignalFeed


def test_parse_telegram_signal_extracts_basic_fields():
    sig = parse_telegram_signal("BUY V75 ENTRY: 8237 SL: 8199 TP: 8290")

    assert sig.symbol == "V75"
    assert sig.direction == "long"
    assert sig.entry == 8237.0
    assert sig.stop_loss == 8199.0
    assert sig.take_profit == 8290.0


def test_build_context_includes_feed_bias():
    feed = SignalFeed()
    context = build_context(
        symbols=["V75", "XAUUSD"],
        strategies=["trend_pullback", "breakout"],
        feed=feed,
        telegram=[],
    )

    assert context["symbols"] == ["V75", "XAUUSD"]
    assert context["strategies"] == ["trend_pullback", "breakout"]
    assert context["signal_count"] == 0
