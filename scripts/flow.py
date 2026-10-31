#!/usr/bin/env python3
"""Опционный поток Deribit: крупные/блочные сделки и скью put/call.

Аналог «кто что покупает» из разбора друга. Публичные эндпоинты, без ключа.

Usage: python3 scripts/flow.py BTC   (или ETH)
"""
import json
import sys
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone

BASE = "https://www.deribit.com/api/v2/public/"
MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN",
          "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]


def get(method, **params):
    q = "&".join(f"{k}={v}" for k, v in params.items())
    url = f"{BASE}{method}?{q}"
    req = urllib.request.Request(url, headers={"User-Agent": "flow/1.0"})
    return json.load(urllib.request.urlopen(req, timeout=40))["result"]


def parse(instr):
    p = instr.split("-")
    if len(p) != 4:
        return None
    return p[1], float(p[2]), p[3]  # expiry, strike, C/P


def main():
    cur = (sys.argv[1] if len(sys.argv) > 1 else "BTC").upper()
    big = 25 if cur == "BTC" else 250        # порог «крупной» в монетах
    print(f"=== {cur}: опционный поток (Deribit) ===")

    # --- крупные / блочные сделки за последние ~N ---
    try:
        trades = get("get_last_trades_by_currency", currency=cur,
                     kind="option", count=1000, sorting="desc")["trades"]
    except Exception as e:  # noqa: BLE001
        print(f"сделки недоступны: {e}")
        trades = []

    call_v = put_v = 0.0
    notable = []
    for t in trades:
        pr = parse(t["instrument_name"])
        if not pr:
            continue
        expiry, strike, cp = pr
        amt = t.get("amount", 0)
        if cp == "C":
            call_v += amt
        else:
            put_v += amt
        is_block = bool(t.get("block_trade_id"))
        if is_block or amt >= big:
            idx = t.get("index_price") or 0
            prem = t.get("price", 0) * amt * idx
            notable.append({
                "exp": expiry, "k": strike, "cp": cp,
                "side": t.get("direction", "?"), "amt": amt,
                "prem": prem, "iv": t.get("iv"), "block": is_block,
                "ts": t.get("timestamp", 0)})

    pc = (put_v / call_v) if call_v else float("inf")
    print(f"Объём опционов (последние сделки): коллы {call_v:,.0f} {cur}, "
          f"путы {put_v:,.0f} {cur}, put/call по объёму {pc:.2f}")

    notable.sort(key=lambda x: -x["prem"])
    print(f"\nКрупнейшие сделки (блок или ≥ {big} {cur}):")
    if not notable:
        print("  крупных сделок не найдено.")
    for n in notable[:10]:
        tag = "BLOCK" if n["block"] else "big"
        side = "покупка" if n["side"] == "buy" else "продажа"
        kind = "колл" if n["cp"] == "C" else "пут"
        ivs = f", IV {n['iv']:.0f}" if n.get("iv") else ""
        dt = datetime.fromtimestamp(n["ts"] / 1000, timezone.utc).strftime("%d.%m %H:%MZ")
        print(f"  [{tag}] {side} {kind} {n['exp']} ${n['k']:,.0f} · "
              f"{n['amt']:,.0f} {cur} · ~${n['prem']:,.0f} премии{ivs} · {dt}")

    # --- скью: IV путов против коллов по ближайшей значимой экспирации ---
    try:
        book = get("get_book_summary_by_currency", currency=cur, kind="option")
    except Exception as e:  # noqa: BLE001
        print(f"\nскью недоступен: {e}")
        return
    byexp = defaultdict(list)
    spot = None
    for row in book:
        pr = parse(row["instrument_name"])
        if not pr:
            continue
        expiry, strike, cp = pr
        iv = row.get("mark_iv")
        if row.get("underlying_price"):
            spot = row["underlying_price"]
        if iv:
            byexp[expiry].append((strike, cp, iv))
    # ближайшая экспирация с заметным числом страйков
    def exp_key(e):
        try:
            d = int(e[:-5]); mon = MONTHS.index(e[-5:-2]); yr = 2000 + int(e[-2:])
            return datetime(yr, mon + 1, d, tzinfo=timezone.utc)
        except Exception:
            return datetime(2100, 1, 1, tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    future = sorted((e for e in byexp if exp_key(e) > now), key=exp_key)
    print("\nСкью (IV, ближайшие экспирации, OTM-пут минус OTM-колл ~±10%):")
    for e in future[:3]:
        rows = byexp[e]
        if not spot:
            continue
        put_iv = [iv for k, cp, iv in rows if cp == "P" and 0.85 * spot <= k <= 0.95 * spot]
        call_iv = [iv for k, cp, iv in rows if cp == "C" and 1.05 * spot <= k <= 1.15 * spot]
        if put_iv and call_iv:
            pm, cm = sum(put_iv) / len(put_iv), sum(call_iv) / len(call_iv)
            tag = "путы дороже (страх вниз)" if pm > cm else "коллы дороже (аппетит вверх)"
            print(f"  {e}: пут-IV {pm:.1f} vs колл-IV {cm:.1f} → скью {pm-cm:+.1f} п. ({tag})")


if __name__ == "__main__":
    main()
