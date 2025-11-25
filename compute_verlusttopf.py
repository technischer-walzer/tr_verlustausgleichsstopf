#!/usr/bin/env python3
"""FIFO-basierter Verlusttopf-Rechner für Trade Republic Timeline-Exports (all_events.json)."""

import json
import re
import argparse
from datetime import datetime
from collections import defaultdict, deque
from pathlib import Path

# Helper to clean and convert German/European formatted money strings

def parse_money(text):
    if text is None:
        return None
    # normalize spaces and currency symbols
    clean = str(text).replace('\xa0', '').replace('€', '').replace('$', '').replace(' ', '')
    # remove thousand separators (.) and use dot as decimal
    clean = clean.replace('.', '').replace(',', '.')
    try:
        return float(clean)
    except ValueError:
        # fall back to regex extraction
        m = re.search(r'-?\d+(?:\.\d+)?', clean)
        return float(m.group(0)) if m else None


def parse_shares(text):
    if text is None:
        return None
    clean = str(text).replace('\xa0', '').replace(' ', '')
    clean = clean.replace(',', '.').replace('.', '.')
    try:
        return float(clean)
    except ValueError:
        m = re.search(r'-?\d+(?:\.\d+)?', clean)
        return float(m.group(0)) if m else None


def find_instrument_type(obj):
    if isinstance(obj, dict):
        if 'instrumentType' in obj:
            return obj['instrumentType']
        for v in obj.values():
            found = find_instrument_type(v)
            if found:
                return found
    elif isinstance(obj, list):
        for v in obj:
            found = find_instrument_type(v)
            if found:
                return found
    return None


def parse_trade_event(ev):
    subtitle = (ev.get('subtitle') or '').lower()
    # quick status guard: skip cancelled/expired orders
    if any(word in subtitle for word in ('storniert', 'abgebrochen', 'abgelaufen')):
        return None

    # prefer explicit status flag if present
    status = (ev.get('status') or '').lower()
    if status and status != 'executed':
        return None

    if 'verkauf' in subtitle or 'sell' in subtitle:
        side = 'sell'
    elif 'kauf' in subtitle or 'buy' in subtitle:
        side = 'buy'
    else:
        return None

    ts_raw = ev.get('timestamp')
    ts = datetime.fromisoformat(ts_raw.replace('+0000', '+00:00')) if ts_raw else None
    amount_net = ev.get('amount', {}).get('value')

    isin = None
    instrument_type = None
    shares = price_per_share = fee = total = None

    sections = ev.get('details', {}).get('sections', [])

    for sec in sections:
        # ISIN is often the payload of the header action
        act = sec.get('action') or {}
        payload = act.get('payload') if isinstance(act, dict) else None
        if isinstance(payload, str) and len(payload) == 12:
            isin = payload

        data = sec.get('data')
        if not isinstance(data, list):
            continue
        for entry in data:
            title = entry.get('title') or ''
            detail = entry.get('detail', {}) if isinstance(entry, dict) else {}

            # instrument type is nested in customerSupportChat context
            instrument_type = instrument_type or find_instrument_type(detail)
            instrument_type = instrument_type or find_instrument_type(entry)

            # parse fee and sum from Übersicht table
            if title == 'Gebühr':
                fee = parse_money(detail.get('displayValue', {}).get('text') or detail.get('text'))
            if title == 'Summe':
                total = parse_money(detail.get('displayValue', {}).get('text') or detail.get('text'))

            # transaction block can be nested in action payload
            inner_payload = detail.get('action', {}).get('payload') if isinstance(detail.get('action'), dict) else None
            if isinstance(inner_payload, dict):
                for s2 in inner_payload.get('sections', []):
                    if s2.get('type') != 'table':
                        continue
                    for row in s2.get('data', []):
                        rtitle = row.get('title') or ''
                        rdetail = row.get('detail', {}) if isinstance(row, dict) else {}
                        val = rdetail.get('displayValue', {}).get('text') or rdetail.get('text')
                        if rtitle in ('Aktien', 'Stück', 'Anteile'):
                            shares = parse_shares(val)
                        elif rtitle in ('Aktienkurs', 'Preis', 'Ausführungskurs'):
                            price_per_share = parse_money(val)
                        elif rtitle == 'Summe' and total is None:
                            total = parse_money(val)

            # quick parse from text like "1 × 156,60 €"
            txt = detail.get('text') if isinstance(detail, dict) else None
            if txt and '×' in txt:
                m = re.search(r'(-?\d+[\.,]?\d*)\s*×\s*(-?\d+[\.,]?\d*)', txt)
                if m:
                    shares = shares or parse_shares(m.group(1))
                    price_per_share = price_per_share or parse_money(m.group(2))

    instrument_type = instrument_type or 'stock'  # default guess

    # If total not found, fall back to amount_net
    if total is None and amount_net is not None:
        total = amount_net

    # If fee missing but amount_net and transaction sum exist, infer
    if fee is None and total is not None and amount_net is not None and side == 'buy':
        # for buys, amount_net is negative; total already negative including fee
        fee = 0.0

    return {
        'timestamp': ts,
        'side': side,
        'isin': isin,
        'instrument_type': instrument_type,
        'shares': shares,
        'price': price_per_share,
        'total': total,
        'fee': fee,
        'title': ev.get('title')
    }


def load_trades(events_path: Path):
    if not events_path.exists():
        raise SystemExit(f"{events_path} not found. Run 'pytr dl_docs <outdir>' to get all_events.json.")
    with open(events_path, 'r', encoding='utf-8') as f:
        events = json.load(f)
    trades = []
    for ev in events:
        if not isinstance(ev, dict):
            continue
        t = parse_trade_event(ev)
        if t:
            trades.append(t)
    trades.sort(key=lambda x: x['timestamp'])
    return trades


def fifo_realized(trades, year=2025):
    positions = defaultdict(deque)  # isin -> deque of (shares, cost_total)
    realized = {'stock': 0.0, 'other': 0.0}
    per_sale = []
    warnings = []

    for tr in trades:
        cat = 'stock' if tr['instrument_type'] == 'stock' else 'other'
        if tr['side'] == 'buy':
            if tr['shares'] is None or tr['total'] is None:
                continue
            cost_total = tr['total']
            # ensure sign: buys are negative totals in export; store positive cost
            cost_total_abs = abs(cost_total)
            positions[tr['isin']].append([tr['shares'], cost_total_abs])
        else:  # sell
            if tr['timestamp'].year != year:
                continue
            if tr['shares'] is None or tr['total'] is None:
                continue
            sell_qty = tr['shares']
            proceeds = tr['total']  # sells are positive net cash in export
            remaining = sell_qty
            cost_sum = 0.0
            while remaining > 1e-9 and positions[tr['isin']]:
                lot_shares, lot_cost = positions[tr['isin']][0]
                take = min(remaining, lot_shares)
                cost_per_share = lot_cost / lot_shares
                cost_sum += take * cost_per_share
                lot_shares -= take
                remaining -= take
                if lot_shares <= 1e-9:
                    positions[tr['isin']].popleft()
                else:
                    positions[tr['isin']][0][0] = lot_shares
                    positions[tr['isin']][0][1] = lot_shares * cost_per_share
            if remaining > 1e-6:
                warnings.append(f"Kein/zu wenig Bestand für {tr['title']} ({tr['isin']}) – {remaining:.4f} Stück ohne Anschaffungskosten angesetzt.")
            profit = proceeds - cost_sum
            realized[cat] += profit
            per_sale.append({
                'date': tr['timestamp'].date(),
                'isin': tr['isin'],
                'title': tr['title'],
                'category': cat,
                'shares': sell_qty,
                'proceeds': proceeds,
                'cost_basis': cost_sum,
                'profit': profit,
            })
    return realized, per_sale, warnings


def main():
    parser = argparse.ArgumentParser(description="Compute German Verlusttopf (FIFO) from Trade Republic timeline JSON")
    parser.add_argument('--events', default='all_events.json', type=Path, help="Path to all_events.json from pytr dl_docs")
    parser.add_argument('--year', type=int, default=datetime.now().year, help="Tax year to evaluate (default: current year)")
    args = parser.parse_args()

    trades = load_trades(args.events)
    realized, per_sale, warnings = fifo_realized(trades, year=args.year)

    print(f'Realisierte Gewinne/Verluste {args.year}')
    print(f"  Aktien-Verlusttopf: {realized['stock']:.2f} EUR")
    print(f"  Sonstiger Verlusttopf: {realized['other']:.2f} EUR")
    print('\nDetails pro Verkauf:')
    for s in per_sale:
        print(f"  {s['date']} {s['title']} ({s['isin']}) | {s['shares']} Stk | Erlös {s['proceeds']:.2f} | Kosten {s['cost_basis']:.2f} | PnL {s['profit']:.2f} | Topf {s['category']}")

    # Write CSV for further analysis
    import csv
    out_path = Path(f'verlusttopf_{args.year}_sales.csv')
    with out_path.open('w', newline='', encoding='utf-8') as f:
        w = csv.writer(f, delimiter=';')
        w.writerow(['date', 'title', 'isin', 'category', 'shares', 'proceeds_eur', 'cost_basis_eur', 'profit_eur'])
        for s in per_sale:
            w.writerow([s['date'], s['title'], s['isin'], s['category'], s['shares'], f"{s['proceeds']:.2f}", f"{s['cost_basis']:.2f}", f"{s['profit']:.2f}"])
    print(f"\nCSV gespeichert: {out_path}")
    if warnings:
        print("\nWARNUNGEN:")
        for msg in warnings:
            print(" -", msg)


if __name__ == '__main__':
    main()
