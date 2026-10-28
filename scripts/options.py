#!/usr/bin/env python3
"""Deribit options summary: OI коллов/путов, put/call, топ-страйки, max pain, выплаты.

Usage:
    python3 scripts/options.py BTC 2026-10-30
    python3 scripts/options.py ETH 2026-12-26

Берёт публичную книгу Deribit (get_book_summary_by_currency), фильтрует по дате
экспирации, считает метрики. Ключи/авторизация не нужны. Интернет обязателен.
"""
import json
import sys
import urllib.request
from datetime import datetime, timezone

MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN",
          "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]


def deribit_code(date_str):
    """2026-10-30 -> 30OCT26 (формат инструментов Deribit)."""
    d = datetime.strptime(date_str, "%Y-%m-%d")
    return f"{d.day}{MONTHS[d.month - 1]}{d.strftime('%y')}"


def fetch(currency):
    url = ("https://www.deribit.com/api/v2/public/"
           f"get_book_summary_by_currency?currency={currency}&kind=option")
    req = urllib.request.Request(url, headers={"User-Agent": "forecast-bot/1.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)["result"]


def spot_price(currency):
    url = ("https://www.deribit.com/api/v2/public/get_index_price"
           f"?index_name={currency.lower()}_usd")
    req = urllib.request.Request(url, headers={"User-Agent": "forecast-bot/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)["result"]["index_price"]
    except Exception:
        return None


def parse(instrument):
    # BTC-30OCT26-80000-C
    parts = instrument.split("-")
    if len(parts) != 4:
        return None
    _, expiry, strike, opt = parts
    return expiry, float(strike), opt  # opt: 'C' or 'P'


def main():
    if len(sys.argv) != 3:
        print("Usage: python3 scripts/options.py <BTC|ETH> <YYYY-MM-DD>")
        sys.exit(1)
    currency = sys.argv[1].upper()
    date_str = sys.argv[2]
    target = deribit_code(date_str)

    rows = fetch(currency)
    spot = spot_price(currency)

    calls, puts = {}, {}   # strike -> OI
    for row in rows:
        p = parse(row["instrument_name"])
        if not p:
            continue
        expiry, strike, opt = p
        if expiry != target:
            continue
        oi = row.get("open_interest") or 0
        (calls if opt == "C" else puts)[strike] = \
            (calls if opt == "C" else puts).get(strike, 0) + oi

    if not calls and not puts:
        print(f"Нет данных по {currency} на экспирацию {date_str} ({target}).")
        print("Проверь дату: у Deribit экспирации обычно пт 08:00 UTC; "
              "месячные — последняя пятница месяца.")
        return

    strikes = sorted(set(calls) | set(puts))
    call_oi = sum(calls.values())
    put_oi = sum(puts.values())
    pc = (put_oi / call_oi) if call_oi else float("inf")

    def max_pain():
        best_s, best_val = None, None
        for s in strikes:
            val = sum(oi * max(0, s - k) for k, oi in calls.items()) \
                + sum(oi * max(0, k - s) for k, oi in puts.items())
            if best_val is None or val < best_val:
                best_s, best_val = s, val
        return best_s

    mp = max_pain()

    def payout_at(s):
        c = sum(oi * max(0, s - k) for k, oi in calls.items())
        p = sum(oi * max(0, k - s) for k, oi in puts.items())
        return c, p

    top_calls = sorted(calls.items(), key=lambda x: -x[1])[:5]
    top_puts = sorted(puts.items(), key=lambda x: -x[1])[:5]

    print(f"=== {currency} · экспирация {date_str} ({target}) ===")
    if spot:
        print(f"Индекс-цена сейчас: ${spot:,.0f}")
    print(f"OI коллов: {call_oi:,.1f} | OI путов: {put_oi:,.1f} | "
          f"put/call: {pc:.2f}")
    print(f"MAX PAIN: ${mp:,.0f}")
    print("\nТоп-5 страйков по коллам (strike: OI):")
    for k, v in top_calls:
        print(f"  ${k:,.0f}: {v:,.1f}")
    print("Топ-5 страйков по путам (strike: OI):")
    for k, v in top_puts:
        print(f"  ${k:,.0f}: {v:,.1f}")
    print("\nСуммарная внутренняя стоимость (выплаты держателям) "
          "при цене вокруг max pain:")
    step = 2000 if currency == "BTC" else 100
    for d in range(-3, 4):
        s = mp + d * step
        if s <= 0:
            continue
        c, p = payout_at(s)
        tag = "  <- max pain" if d == 0 else ""
        print(f"  ${s:,.0f}: коллы {c:,.0f} + путы {p:,.0f} = "
              f"{c + p:,.0f}{tag}")


if __name__ == "__main__":
    main()
