#!/usr/bin/env python3
"""Направление дня по трём «финальным активам»: доллар (DXY), золото, Bitcoin.

Идея: активы связаны. Если сегодня доллар растёт, а BTC исторически с долларом в
обратной связи — на BTC давит вниз. Для каждого актива считаем «кросс-активное
давление» = сумма (корреляция × сегодняшнее движение связанного актива) и говорим,
куда это тянет СЕГОДНЯ. Это ЛИН (склонность), а не гарантия: рынок частично случаен.

Источники (все свежие, без стороннего коннектора, крутится в GitHub Action):
- BTC — Deribit (дневные свечи + сегодня).
- Доллар DXY — строим из ECB-курсов frankfurter.dev (свежо ~1–2 дня, есть история).
- Золото XAU — спот gold-api.com (свежо) + накопленная история из crossasset_state.
- VIX, Nasdaq — FRED (контекст режима; помечаем, если запаздывают).

Пишет data/directions.json. Нужен FRED_API_KEY (тот же секрет).
"""
import json, os, time, math, statistics, urllib.request, urllib.parse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "data", "directions.json")
CA_STATE = os.path.join(ROOT, "data", "crossasset_state.json")
FRED = os.environ.get("FRED_API_KEY", "").strip()
UA = {"User-Agent": "btc-eth-daily/directions"}

# Приоры корреляций, пока нет измеренной истории (напр. золото накапливается).
PRIOR = {("BTC", "Gold"): 0.15, ("DXY", "Gold"): -0.35, ("Gold", "VIX"): 0.10}
# Типичное дневное движение (%) каждого актива — нормируем, чтобы VIX (огромные
# дневные скачки) не забивал FX/крипту просто из-за своей волатильности.
TYPICAL = {"BTC": 2.5, "DXY": 0.3, "Gold": 0.9, "VIX": 7.0, "Nasdaq": 1.1}
CAP = 6.0  # потолок нормированного движения (экстремум силён, но не бесконечен)


def get(url):
    return json.load(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=25))


def rets(series):
    """series: dict date->value → dict date->дневная доходность (по возр. дате)."""
    days = sorted(series)
    out = {}
    for a, b in zip(days[:-1], days[1:]):
        if series[a]:
            out[b] = (series[b] - series[a]) / series[a]
    return out


def corr_of(ra, rb):
    days = sorted(set(ra) & set(rb))
    if len(days) < 10:
        return None
    a = [ra[d] for d in days][-20:]
    b = [rb[d] for d in days][-20:]
    if len(a) < 8:
        return None
    try:
        return round(statistics.correlation(a, b), 2)
    except Exception:
        return None


def last_move(series):
    """Последнее дневное движение в % и свежесть (дней назад последняя точка)."""
    days = sorted(series)
    if len(days) < 2:
        return None, None
    mv = (series[days[-1]] - series[days[-2]]) / series[days[-2]] * 100
    old = int((time.time() - time.mktime(time.strptime(days[-1], "%Y-%m-%d"))) // 86400)
    return round(mv, 2), old


# ─── источники ────────────────────────────────────────────────────────────
def btc_series(days=45):
    now = int(time.time() * 1000)
    q = urllib.parse.urlencode({"instrument_name": "BTC-PERPETUAL", "resolution": "1D",
                                "start_timestamp": now - days*86400*1000, "end_timestamp": now})
    r = get("https://www.deribit.com/api/v2/public/get_tradingview_chart_data?" + q)["result"]
    return {time.strftime("%Y-%m-%d", time.gmtime(t/1000)): c for t, c in zip(r["ticks"], r["close"])}


def dxy_series():
    """DXY по официальной формуле ICE из ECB-курсов (frankfurter.dev)."""
    start = time.strftime("%Y-%m-%d", time.gmtime(time.time() - 40*86400))
    end = time.strftime("%Y-%m-%d", time.gmtime())
    url = f"https://api.frankfurter.dev/v1/{start}..{end}?base=USD&symbols=EUR,JPY,GBP,CAD,CHF,SEK"
    rates = get(url).get("rates", {})
    out = {}
    for d, r in rates.items():
        try:
            eurusd, gbpusd = 1/r["EUR"], 1/r["GBP"]
            out[d] = (50.14348112 * eurusd**-0.576 * r["JPY"]**0.136 * gbpusd**-0.119
                      * r["CAD"]**0.091 * r["SEK"]**0.042 * r["CHF"]**0.036)
        except (KeyError, ZeroDivisionError):
            continue
    return out


def gold_series():
    """Спот сегодня (gold-api) + накопленная история из crossasset_state."""
    hist = {}
    try:
        hist = dict(json.load(open(CA_STATE)).get("gold_hist") or {})
    except Exception:
        pass
    try:
        g = get("https://api.gold-api.com/price/XAU")
        hist[time.strftime("%Y-%m-%d", time.gmtime())] = float(g["price"])
    except Exception as e:
        print("gold fail:", e)
    return hist


def fred_series(sid, limit=45):
    if not FRED:
        return {}
    q = urllib.parse.urlencode({"series_id": sid, "api_key": FRED, "file_type": "json",
                                "sort_order": "desc", "limit": limit})
    d = get("https://api.stlouisfed.org/fred/series/observations?" + q)
    out = {}
    for o in d.get("observations", []):
        try:
            out[o["date"]] = float(o["value"])
        except (ValueError, KeyError):
            continue
    return out


# ─── модель ───────────────────────────────────────────────────────────────
def main():
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    raw = {}
    for name, fn in (("BTC", btc_series), ("DXY", dxy_series), ("Gold", gold_series),
                     ("VIX", lambda: fred_series("VIXCLS")), ("Nasdaq", lambda: fred_series("NASDAQCOM"))):
        try:
            raw[name] = fn()
        except Exception as e:
            print(name, "fail:", e)
            raw[name] = {}
    R = {k: rets(v) for k, v in raw.items()}
    move = {k: last_move(v) for k, v in raw.items()}        # name -> (move%, days_old)

    def corr(a, b):
        c = corr_of(R.get(a, {}), R.get(b, {}))
        if c is not None:
            return c, "изм."
        p = PRIOR.get((a, b)) or PRIOR.get((b, a))
        return (p, "оц.") if p is not None else (None, None)

    # Какие драйверы учитываем для каждого «финального актива».
    DRV = {"DXY": ["VIX", "Nasdaq", "BTC"],
           "Gold": ["DXY", "VIX", "BTC"],
           "BTC": ["Nasdaq", "DXY", "VIX", "Gold"]}
    LABEL = {"DXY": "Доллар (DXY)", "Gold": "Золото (XAU)", "BTC": "Bitcoin",
             "VIX": "VIX", "Nasdaq": "Nasdaq"}
    LEVEL = {"DXY": lambda: raw["DXY"] and f"{sorted(raw['DXY'].items())[-1][1]:.2f}",
             "Gold": lambda: raw["Gold"] and f"${sorted(raw['Gold'].items())[-1][1]:,.0f}",
             "BTC": lambda: raw["BTC"] and f"${sorted(raw['BTC'].items())[-1][1]/1000:,.1f}k"}

    assets = []
    for tgt in ("DXY", "Gold", "BTC"):
        lean = 0.0
        drivers = []
        for x in DRV[tgt]:
            c, src = corr(tgt, x)
            mv, old = move.get(x, (None, None))
            if c is None or mv is None:
                continue
            stale = old is not None and old > 4
            norm = max(-CAP, min(CAP, mv / TYPICAL.get(x, 1.0)))
            contrib = c * norm
            if not stale:
                lean += contrib
            drivers.append({"from": LABEL[x], "corr": c, "src": src, "move": mv,
                            "push": "up" if contrib > 0 else "down",
                            "stale": stale, "mag": abs(contrib)})
        drivers.sort(key=lambda d: -d["mag"])
        if abs(lean) < 0.05 or not drivers:
            d, conf = "flat", "низкая"
        else:
            d = "up" if lean > 0 else "down"
            fresh = [x for x in drivers if not x["stale"]]
            conf = "средняя" if fresh and any(x["src"] == "изм." for x in fresh) else "низкая"
        top = [x for x in drivers if not x["stale"]][:2]
        why_bits = []
        for x in top:
            arrow = "↑" if x["move"] >= 0 else "↓"
            rel = "обратная" if x["corr"] < 0 else "прямая"
            why_bits.append(f"{x['from']} {arrow}{abs(x['move']):.2f}% ({rel} связь {x['corr']:+.2f}"
                            + ("" if x["src"] == "изм." else ", оценка") + ")")
        mv_t, old_t = move.get(tgt, (None, None))
        try:
            lvl = LEVEL[tgt]()
        except Exception:
            lvl = None
        assets.append({"key": tgt, "name": LABEL[tgt], "level": lvl,
                       "move": mv_t, "stale": (old_t or 0) > 4,
                       "dir": d, "conf": conf, "lean": round(lean, 2),
                       "why": "; ".join(why_bits) if why_bits else "свежих драйверов нет",
                       "drivers": drivers[:3]})
    data = {"assets": assets, "updated": int(time.time()),
            "note": "Склонность на день из кросс-активного давления, не гарантия."}
    json.dump(data, open(OUT, "w"), ensure_ascii=False, indent=2)
    print("directions.json:", json.dumps(data, ensure_ascii=False))


if __name__ == "__main__":
    main()
