#!/usr/bin/env python3
"""Кросс-активы: золото, Forex и связь BTC с Nasdaq.

То, чего не было в ежедневке и что есть в «Macro & Cross-Asset Screener»:
- 🥇 Золото (XAU) — спот с gold-api.com (без ключа) + дневное изменение
  по сохранённому вчерашнему снимку (data/crossasset_state.json).
- 💱 Forex EUR/USD, USD/JPY, GBP/USD — из FRED (DEXUSEU/DEXJPUS/DEXUSUK).
- 🔗 Корреляция и бета BTC ↔ Nasdaq за ~20 торговых дней: BTC с Deribit
  (дневные свечи), Nasdaq из FRED (NASDAQCOM). Показывает, идёт ли BTC
  сейчас «за акциями» или живёт своей жизнью.

Пишет data/crossasset.json. Запускается в GitHub Action рядом с macro.py.
Нужен FRED_API_KEY (тот же секрет, что у макро).
"""
import json, os, time, statistics, urllib.request, urllib.parse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "data", "crossasset.json")
STATE = os.path.join(ROOT, "data", "crossasset_state.json")
FRED = os.environ.get("FRED_API_KEY", "").strip()
UA = {"User-Agent": "btc-eth-daily/crossasset"}


def get(url, headers=None, timeout=25):
    req = urllib.request.Request(url, headers={**UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def fred_series(sid, limit=60):
    """Возвращает list[(date, float)] по возрастанию даты."""
    q = urllib.parse.urlencode({"series_id": sid, "api_key": FRED,
                                "file_type": "json", "sort_order": "desc", "limit": limit})
    d = get(f"https://api.stlouisfed.org/fred/series/observations?{q}")
    out = []
    for o in d.get("observations", []):
        try:
            out.append((o["date"], float(o["value"])))
        except (ValueError, KeyError):
            continue
    return list(reversed(out))


def fx_tile(sid, label, invert=False):
    s = fred_series(sid, limit=5)
    if len(s) < 2:
        return None
    (_, prev), (_, cur) = s[-2], s[-1]
    val, pv = (1/cur, 1/prev) if invert else (cur, prev)
    pct = (val - pv) / pv * 100 if pv else 0.0
    return {"label": label, "val": f"{val:.4f}" if val < 10 else f"{val:.2f}",
            "pct": f"{pct:+.2f}%", "up": pct >= 0}


def gold_tile():
    try:
        g = get("https://api.gold-api.com/price/XAU")
        price = float(g["price"])
    except Exception as e:
        print("gold fail:", e)
        return None
    prev = {}
    try:
        prev = json.load(open(STATE))
    except Exception:
        pass
    pp = prev.get("gold")
    pct = (price - pp) / pp * 100 if pp else None
    prev["gold"] = price
    json.dump(prev, open(STATE, "w"))
    return {"label": "Золото XAU", "val": f"${price:,.0f}",
            "pct": (f"{pct:+.2f}%" if pct is not None else "—"),
            "up": (pct is None or pct >= 0)}


def btc_daily(days=45):
    now = int(time.time() * 1000)
    ago = now - days * 86400 * 1000
    q = urllib.parse.urlencode({"instrument_name": "BTC-PERPETUAL", "resolution": "1D",
                                "start_timestamp": ago, "end_timestamp": now})
    r = get(f"https://www.deribit.com/api/v2/public/get_tradingview_chart_data?{q}")["result"]
    out = {}
    for ts, c in zip(r["ticks"], r["close"]):
        day = time.strftime("%Y-%m-%d", time.gmtime(ts / 1000))
        out[day] = c
    return out


def corr_beta():
    """Pearson-корреляция и бета дневных доходностей BTC к Nasdaq (~20 дней)."""
    try:
        ndx = dict(fred_series("NASDAQCOM", limit=45))
        btc = btc_daily(45)
    except Exception as e:
        print("corr fail:", e)
        return None
    days = sorted(set(ndx) & set(btc))
    if len(days) < 12:
        return None
    rb, rn = [], []
    for a, b in zip(days[:-1], days[1:]):
        if ndx[a] and btc[a]:
            rn.append((ndx[b] - ndx[a]) / ndx[a])
            rb.append((btc[b] - btc[a]) / btc[a])
    rb, rn = rb[-20:], rn[-20:]
    if len(rb) < 8:
        return None
    try:
        corr = statistics.correlation(rb, rn)
        beta = statistics.covariance(rb, rn) / statistics.variance(rn)
    except Exception as e:
        print("stat fail:", e)
        return None
    if corr >= 0.6:
        word = "сильно связан — идёт за акциями"
    elif corr >= 0.3:
        word = "умеренно связан с акциями"
    elif corr <= -0.3:
        word = "движется против акций"
    else:
        word = "живёт своей жизнью, слабая связь с акциями"
    return {"corr": round(corr, 2), "beta": round(beta, 2), "days": len(rb), "word": word}


def main():
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    tiles = [t for t in (
        gold_tile(),
        fx_tile("DEXUSEU", "EUR/USD"),
        fx_tile("DEXJPUS", "USD/JPY"),
        fx_tile("DEXUSUK", "GBP/USD"),
    ) if t]
    cb = corr_beta()
    data = {"tiles": tiles, "corr": cb, "updated": int(time.time())}
    json.dump(data, open(OUT, "w"), ensure_ascii=False, indent=2)
    print("crossasset.json:", json.dumps(data, ensure_ascii=False))


if __name__ == "__main__":
    main()
