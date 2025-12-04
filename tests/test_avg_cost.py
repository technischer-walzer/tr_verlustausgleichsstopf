import sys
from pathlib import Path
from datetime import datetime
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import compute_avg_cost as cv


def make_event(side: str, *, isin: str, shares: float, price: float, fee: float = 0.0, dt: str = "2025-01-01T10:00:00.000+0000", instrument_type: Optional[str] = None):
    """Build a minimal TR-like event with net cash totals.

    Buys: -(price*shares + fee); Sells: price*shares - fee (fees reduce proceeds).
    """

    subtitle = "Kauforder" if side == "buy" else "Verkaufsorder"
    if side == "buy":
        total = -(price * shares + fee)
    else:
        total = price * shares - fee

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
    assert abs(sell["total"] - 599.0) < 1e-6  # sell net after fee


def test_avg_cost_profit():
    trades = [
        parse(make_event("buy", isin="US0000000001", shares=10, price=100, fee=0)),
        parse(make_event("sell", isin="US0000000001", shares=4, price=150, fee=0, dt="2025-03-01T00:00:00.000+0000")),
    ]
    realized, per_sale, warnings = cv.avg_realized(trades, year=2025)
    assert not warnings
    # average cost stays 100 → cost basis 4*100=400, profit 200
    assert abs(realized - 200.0) < 1e-6
    assert len(per_sale) == 1


def test_single_pot_and_derivatives():
    stock_sell = parse(make_event("sell", isin="US0000000002", shares=1, price=50, fee=0, dt="2025-04-01T00:00:00.000+0000"))
    stock_buy = parse(make_event("buy", isin="US0000000002", shares=1, price=30, fee=0, dt="2025-03-01T00:00:00.000+0000"))
    deriv_buy = parse(make_event("buy", isin="DE000DERIV01", shares=2, price=10, fee=0, dt="2025-03-02T00:00:00.000+0000", instrument_type="derivative"))
    deriv_sell = parse(make_event("sell", isin="DE000DERIV01", shares=2, price=15, fee=0, dt="2025-04-02T00:00:00.000+0000", instrument_type="derivative"))
    trades = [stock_buy, stock_sell, deriv_buy, deriv_sell]
    realized, per_sale, warnings = cv.avg_realized(trades, year=2025)
    assert warnings  # warn that derivatives share the same pool
    # single pot: profit 20 + 10 = 30 regardless of instrument_type
    assert abs(realized - 30.0) < 1e-6


def test_warning_on_inventory_shortage():
    buy = parse(make_event("buy", isin="US0000000003", shares=1, price=10, fee=0))
    sell = parse(make_event("sell", isin="US0000000003", shares=2, price=12, fee=0, dt="2025-05-01T00:00:00.000+0000"))
    realized, per_sale, warnings = cv.avg_realized([buy, sell], year=2025)
    assert warnings  # should warn about missing inventory
    assert len(per_sale) == 1


def test_avg_cost_multiple_buys():
    """Average cost method pools lots; cost basis uses weighted average."""
    trades = [
        parse(make_event("buy", isin="US0000000004", shares=10, price=100, fee=0, dt="2025-01-01T00:00:00.000+0000")),
        parse(make_event("buy", isin="US0000000004", shares=5, price=120, fee=0, dt="2025-02-01T00:00:00.000+0000")),
        parse(make_event("sell", isin="US0000000004", shares=12, price=150, fee=0, dt="2025-03-01T00:00:00.000+0000")),
    ]
    realized, per_sale, warnings = cv.avg_realized(trades, year=2025)
    assert not warnings
    # avg cost = (10*100 + 5*120) / 15 = 106.666..., cost basis 12*avg = 1280
    # Proceeds: 12*150 = 1800, profit 520
    assert abs(realized - 520.0) < 1e-6
    assert len(per_sale) == 1
    assert abs(per_sale[0]["cost_basis"] - 1280.0) < 1e-6


def test_realized_loss_negative():
    """Losses stay negative with average cost."""
    trades = [
        parse(make_event("buy", isin="US0000000005", shares=10, price=100, fee=0)),
        parse(make_event("sell", isin="US0000000005", shares=10, price=80, fee=0, dt="2025-03-01T00:00:00.000+0000")),
    ]
    realized, per_sale, warnings = cv.avg_realized(trades, year=2025)
    assert not warnings
    # cost basis 10*100=1000, proceeds 10*80=800 → loss -200
    assert abs(realized - (-200.0)) < 1e-6
    assert len(per_sale) == 1
    assert per_sale[0]["profit"] < 0


def test_year_filtering():
    """Test that only sales in the target year are counted."""
    trades = [
        parse(make_event("buy", isin="US0000000006", shares=10, price=100, fee=0, dt="2024-01-01T00:00:00.000+0000")),
        parse(make_event("sell", isin="US0000000006", shares=5, price=160, fee=0, dt="2024-12-01T00:00:00.000+0000")),
        parse(make_event("sell", isin="US0000000006", shares=3, price=150, fee=0, dt="2025-03-01T00:00:00.000+0000")),
    ]
    # Calculate for 2025 only
    realized, per_sale, warnings = cv.avg_realized(trades, year=2025)
    assert not warnings
    # 2024 sale consumed 5 shares from inventory (not counted in 2025 profit)
    # 2025 sale: 3 @ 150 = 450, cost basis 3 @ 100 = 300 → profit 150
    assert abs(realized - 150.0) < 1e-6
    assert len(per_sale) == 1  # only 2025 sale


def test_partial_consumption_avg_cost():
    """Average cost remains unchanged until new buys; partial sells reduce quantity and cost proportionally."""
    trades = [
        parse(make_event("buy", isin="US0000000007", shares=10, price=100, fee=0, dt="2025-01-01T00:00:00.000+0000")),
        parse(make_event("sell", isin="US0000000007", shares=3, price=150, fee=0, dt="2025-02-01T00:00:00.000+0000")),
        parse(make_event("sell", isin="US0000000007", shares=5, price=140, fee=0, dt="2025-03-01T00:00:00.000+0000")),
    ]
    realized, per_sale, warnings = cv.avg_realized(trades, year=2025)
    assert not warnings
    # First sale: 3 @ 150 - 3 @ 100 = 150 profit
    # Second sale: 5 @ 140 - 5 @ 100 = 200 profit
    assert abs(realized - 350.0) < 1e-6
    assert len(per_sale) == 2
    assert abs(per_sale[0]["profit"] - 150.0) < 1e-6
    assert abs(per_sale[1]["profit"] - 200.0) < 1e-6


def test_fees_in_cost_basis():
    """Fees are ignored for cost/proceeds (Austrian private capital assets)."""
    trades = [
        parse(make_event("buy", isin="US0000000008", shares=10, price=100, fee=5, dt="2025-01-01T00:00:00.000+0000")),
        parse(make_event("sell", isin="US0000000008", shares=10, price=150, fee=3, dt="2025-02-01T00:00:00.000+0000")),
    ]
    realized, per_sale, warnings = cv.avg_realized(trades, year=2025)
    assert not warnings
    # Buy cost excludes fee: 10*100 = 1000
    # Sell proceeds exclude fee: 10*150 = 1500
    # Profit: 500
    assert abs(realized - 500.0) < 1e-6
    assert len(per_sale) == 1


def test_multiple_isins_separate_pools():
    """Average cost tracking stays per ISIN but results aggregate into one pot."""
    trades = [
        parse(make_event("buy", isin="US0000000009", shares=10, price=100, fee=0, dt="2025-01-01T00:00:00.000+0000")),
        parse(make_event("buy", isin="US0000000010", shares=10, price=50, fee=0, dt="2025-01-02T00:00:00.000+0000")),
        parse(make_event("sell", isin="US0000000009", shares=5, price=150, fee=0, dt="2025-02-01T00:00:00.000+0000")),
        parse(make_event("sell", isin="US0000000010", shares=5, price=80, fee=0, dt="2025-02-02T00:00:00.000+0000")),
    ]
    realized, per_sale, warnings = cv.avg_realized(trades, year=2025)
    assert not warnings
    # ISIN 009 profit 250, ISIN 010 profit 150, total 400
    assert abs(realized - 400.0) < 1e-6
    assert len(per_sale) == 2


def test_fractional_shares():
    """Test that fractional shares are handled correctly (common with TR savings plans)."""
    trades = [
        parse(make_event("buy", isin="US0000000011", shares=10.5, price=100, fee=0, dt="2025-01-01T00:00:00.000+0000")),
        parse(make_event("sell", isin="US0000000011", shares=3.7, price=150, fee=0, dt="2025-02-01T00:00:00.000+0000")),
    ]
    realized, per_sale, warnings = cv.avg_realized(trades, year=2025)
    assert not warnings
    # Cost basis: 3.7 * 100 = 370
    # Proceeds: 3.7 * 150 = 555
    # Profit: 555 - 370 = 185
    assert abs(realized - 185.0) < 1e-6
    assert len(per_sale) == 1
    assert abs(per_sale[0]["cost_basis"] - 370.0) < 1e-6
