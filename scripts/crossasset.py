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
    # Копим дневную историю золота (для корреляции — бесплатного дневного ряда нет).
    hist = prev.get("gold_hist") or {}
    hist[time.strftime("%Y-%m-%d", time.gmtime())] = price
    prev["gold_hist"] = dict(sorted(hist.items())[-60:])
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


def corr_vs(btc, series):
    """Корреляция, бета и последнее дневное движение актива к BTC (~20 дней).
    series — dict date->value. Возвращает (corr, beta, last_move_%, days) или None."""
    days = sorted(set(series) & set(btc))
    if len(days) < 10:
        return None
    rb, ra = [], []
    for a, b in zip(days[:-1], days[1:]):
        if series[a] and btc[a]:
            ra.append((series[b] - series[a]) / series[a])
            rb.append((btc[b] - btc[a]) / btc[a])
    rb, ra = rb[-20:], ra[-20:]
    if len(rb) < 8:
        return None
    try:
        corr = statistics.correlation(rb, ra)
        beta = statistics.covariance(rb, ra) / statistics.variance(ra)
    except Exception:
        return None
    last_date = days[-1]
    days_old = int((time.time() - time.mktime(time.strptime(last_date, "%Y-%m-%d"))) // 86400)
    return round(corr, 2), round(beta, 2), round(ra[-1] * 100, 2), len(rb), days_old


def gold_hist_series():
    try:
        return {k: v for k, v in (json.load(open(STATE)).get("gold_hist") or {}).items()}
    except Exception:
        return {}


def direction(corr, move, days_old):
    """Куда исторически тянет BTC при последнем движении актива."""
    if days_old > 4:
        return "flat", f"связь {corr:+.2f}; данные FRED запаздывают ~{days_old}дн — смотри свежий график"
    if abs(corr) < 0.3 or move is None or abs(move) < 0.05:
        return "flat", "связь слабая — явного сигнала нет"
    bias = corr * move                      # знак corr × знак движения актива
    if bias > 0:
        return "up", "тянет BTC вверх"
    return "down", "давит BTC вниз"


def radar(btc):
    """BTC против Nasdaq, доллара (DXY), VIX и золота: связь + куда тянет сегодня."""
    specs = [
        ("Nasdaq", lambda: dict(fred_series("NASDAQCOM", 45)), "акции"),
        ("DXY (доллар)", lambda: dict(fred_series("DTWEXBGS", 45)), "доллар"),
        ("VIX (страх)", lambda: dict(fred_series("VIXCLS", 45)), "волатильность акций"),
        ("Золото", gold_hist_series, "защитный актив"),
    ]
    out = []
    for name, getter, kind in specs:
        try:
            ser = getter()
        except Exception as e:
            print(name, "fail:", e)
            ser = {}
        r = corr_vs(btc, ser) if ser else None
        if not r:
            out.append({"asset": name, "kind": kind, "status": "накопление данных"})
            continue
        corr, beta, move, n, days_old = r
        d, word = direction(corr, move, days_old)
        out.append({"asset": name, "kind": kind, "corr": corr, "beta": beta,
                    "move": move, "dir": d, "word": word, "days": n, "stale": days_old > 4})
    return out


def main():
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    tiles = [t for t in (
        gold_tile(),
        fx_tile("DEXUSEU", "EUR/USD"),
        fx_tile("DEXJPUS", "USD/JPY"),
        fx_tile("DEXUSUK", "GBP/USD"),
    ) if t]
    try:
        btc = btc_daily(45)
    except Exception as e:
        print("btc daily fail:", e)
        btc = {}
    rad = radar(btc) if btc else []
    # Обратная совместимость: корреляция с Nasdaq отдельным полем.
    nd = next((x for x in rad if x["asset"] == "Nasdaq" and "corr" in x), None)
    cb = None
    if nd:
        c = nd["corr"]
        word = ("сильно связан — идёт за акциями" if c >= 0.6 else
                "умеренно связан с акциями" if c >= 0.3 else
                "движется против акций" if c <= -0.3 else
                "живёт своей жизнью, слабая связь с акциями")
        cb = {"corr": c, "beta": nd["beta"], "days": nd["days"], "word": word}
    data = {"tiles": tiles, "corr": cb, "radar": rad, "updated": int(time.time())}
    json.dump(data, open(OUT, "w"), ensure_ascii=False, indent=2)
    print("crossasset.json:", json.dumps(data, ensure_ascii=False))


if __name__ == "__main__":
    main()
