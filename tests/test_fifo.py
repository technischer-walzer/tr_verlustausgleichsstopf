import compute_verlusttopf as cv
from datetime import datetime


def make_event(side: str, *, isin: str, shares: float, price: float, fee: float = 0.0, dt: str = "2025-01-01T10:00:00.000+0000", instrument_type: str | None = None):
    subtitle = "Kauforder" if side == "buy" else "Verkaufsorder"
    total = (price * shares + fee) * (-1 if side == "buy" else 1)

    # Build a minimal event matching TR structure that our parser expects
    # Use German number format (comma as decimal separator)
    def fmt(val):
        return f"{val:.2f}".replace('.', ',')

    transaction_rows = [
        {"title": "Aktien", "detail": {"text": str(shares), "displayValue": {"text": str(shares)}}},
        {"title": "Aktienkurs", "detail": {"text": fmt(price), "displayValue": {"text": fmt(price)}}},
        {"title": "Summe", "detail": {"text": fmt(price*shares), "displayValue": {"text": fmt(price*shares)}}},
    ]
    inner_payload = {"sections": [{"type": "table", "data": transaction_rows}]}

    overview = [
        {"title": "Transaktion", "detail": {"action": {"payload": inner_payload}, "text": f"{shares} × {fmt(price)}"}},
        {"title": "Gebühr", "detail": {"text": fmt(fee), "displayValue": {"text": fmt(fee)}}},
        {"title": "Summe", "detail": {"text": fmt(total), "displayValue": {"text": fmt(total)}}},
    ]

    if instrument_type:
        overview[0]["instrumentType"] = instrument_type

    event = {
        "id": "dummy",
        "timestamp": dt,
        "title": "TEST",
        "subtitle": subtitle,
        "status": "EXECUTED",
        "amount": {"currency": "EUR", "value": total, "fractionDigits": 2},
        "action": {"type": "timelineDetail", "payload": "dummy"},
        "details": {
            "sections": [
                {"type": "header", "title": "header", "action": {"payload": isin}},
                {"type": "table", "title": "Übersicht", "data": overview},
            ]
        },
    }
    return event


def parse(ev):
    return cv.parse_trade_event(ev)


def test_parse_basic_buy_sell():
    buy = parse(make_event("buy", isin="US0000000001", shares=10, price=100, fee=1))
    sell = parse(make_event("sell", isin="US0000000001", shares=4, price=150, fee=1, dt="2025-02-01T10:00:00.000+0000"))
    assert buy["side"] == "buy"
    assert sell["side"] == "sell"
    assert abs(buy["total"] + 1001.0) < 1e-6  # buy totals stored negative
    assert abs(sell["total"] - 601.0) < 1e-6


def test_fifo_realized_stock_profit():
    trades = [
        parse(make_event("buy", isin="US0000000001", shares=10, price=100, fee=0)),
        parse(make_event("sell", isin="US0000000001", shares=4, price=150, fee=0, dt="2025-03-01T00:00:00.000+0000")),
    ]
    realized, per_sale, warnings = cv.fifo_realized(trades, year=2025)
    assert not warnings
    # cost basis 4*100=400, proceeds 4*150=600 → profit 200
    assert abs(realized["stock"] - 200.0) < 1e-6
    assert len(per_sale) == 1


def test_fifo_separate_pots():
    stock_sell = parse(make_event("sell", isin="US0000000002", shares=1, price=50, fee=0, dt="2025-04-01T00:00:00.000+0000"))
    stock_buy = parse(make_event("buy", isin="US0000000002", shares=1, price=30, fee=0, dt="2025-03-01T00:00:00.000+0000"))
    deriv_buy = parse(make_event("buy", isin="DE000DERIV01", shares=2, price=10, fee=0, dt="2025-03-02T00:00:00.000+0000", instrument_type="derivative"))
    deriv_sell = parse(make_event("sell", isin="DE000DERIV01", shares=2, price=15, fee=0, dt="2025-04-02T00:00:00.000+0000", instrument_type="derivative"))
    trades = [stock_buy, stock_sell, deriv_buy, deriv_sell]
    realized, per_sale, warnings = cv.fifo_realized(trades, year=2025)
    assert not warnings
    # stock profit 20, other profit 10
    assert abs(realized["stock"] - 20.0) < 1e-6
    assert abs(realized["other"] - 10.0) < 1e-6


def test_warning_on_inventory_shortage():
    buy = parse(make_event("buy", isin="US0000000003", shares=1, price=10, fee=0))
    sell = parse(make_event("sell", isin="US0000000003", shares=2, price=12, fee=0, dt="2025-05-01T00:00:00.000+0000"))
    realized, per_sale, warnings = cv.fifo_realized([buy, sell], year=2025)
    assert warnings  # should warn about missing inventory
    assert len(per_sale) == 1
