#!/usr/bin/env python3
"""Оценочная карта ликвидаций BTC/ETH (как «Liquidation Map» у Coinglass), БЕСПЛАТНО.

Coinglass отдаёт карту только с платным ключом, поэтому считаем её сами тем же
принципом, что и их «модельная» карта:
  1. Берём часовые свечи и историю открытого интереса (OI) за ~7 дней.
  2. Рост OI за час = новые позиции, открытые примерно по средней цене часа.
  3. Делим их на лонги/шорты и по типичным плечам (10x/25x/50x/100x) считаем
     цену ликвидации: лонг ≈ вход·(1−1/плечо), шорт ≈ вход·(1+1/плечо).
  4. Уровни, которые цена уже прошла, считаем «сгоревшими»; при падении OI
     все живые позиции пропорционально уменьшаем (люди закрываются сами).
  5. Складываем в корзины по цене → кластеры ниже цены (лонги) и выше (шорты).

Источники (по очереди, что доступно):
  okx      — свечи + история OI (BTC-USDT-SWAP / ETH-USDT-SWAP), количества в монетах;
  deribit  — свечи Deribit, OI-истории нет → новые позиции оцениваются по объёму
             (только относительная сила, без количеств в монетах).

Это ОЦЕНКА по одной бирже, а не точные данные всего рынка. Сильные кластеры
совпадают с Coinglass по расположению; абсолютные объёмы у всего рынка больше.

Запуск:  python3 scripts/liqmap.py            → пишет data/liqmap.json (BTC и ETH)
         python3 scripts/liqmap.py BTC --print → человекочитаемая сводка
"""
import json, os, sys, time, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "data", "liqmap.json")
UA = {"User-Agent": "btc-eth-daily/liqmap"}
HOURS = 168                      # 7 дней
LEVERAGE = {10: 0.30, 25: 0.30, 50: 0.25, 100: 0.15}   # доля позиций по плечам
MM = 0.005                       # поддерживающая маржа ~0.5%
BIN = {"BTC": 250.0, "ETH": 10.0}
RANGE = 0.15                     # смотрим ±15% от цены
TOP = 3


def get(url, timeout=25):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


# ---------- источники ----------

def okx(coin):
    inst = f"{coin}-USDT-SWAP"
    candles, after = [], ""
    while len(candles) < HOURS + 1:
        url = f"https://www.okx.com/api/v5/market/history-candles?instId={inst}&bar=1H&limit=100" + (f"&after={after}" if after else "")
        d = get(url)["data"]
        if not d:
            break
        candles += d
        after = d[-1][0]
    oi, end = [], ""
    while len(oi) < HOURS + 1:
        url = f"https://www.okx.com/api/v5/rubik/stat/contracts/open-interest-history?instId={inst}&period=1H&limit=100" + (f"&end={end}" if end else "")
        d = get(url)["data"]
        if not d:
            break
        oi += d
        end = str(int(d[-1][0]) - 1)
    oi_by_ts = {int(r[0]): float(r[2]) for r in oi}          # r[2] = OI в монетах
    rows = []
    for c in candles:
        ts = int(c[0])
        if ts in oi_by_ts:
            rows.append({"ts": ts, "o": float(c[1]), "h": float(c[2]), "l": float(c[3]),
                         "c": float(c[4]), "oi": oi_by_ts[ts]})
    rows.sort(key=lambda r: r["ts"])
    if len(rows) < 48:
        raise RuntimeError(f"okx: мало совпадающих точек ({len(rows)})")
    return rows[-(HOURS + 1):], "okx"


def deribit(coin):
    now = int(time.time() * 1000)
    url = ("https://www.deribit.com/api/v2/public/get_tradingview_chart_data?"
           f"instrument_name={coin}-PERPETUAL&resolution=60&start_timestamp={now - (HOURS + 1) * 3600_000}&end_timestamp={now}")
    d = get(url)["result"]
    rows = [{"ts": t, "o": o, "h": h, "l": l, "c": c, "vol": v}
            for t, o, h, l, c, v in zip(d["ticks"], d["open"], d["high"], d["low"], d["close"], d["volume"])]
    if len(rows) < 48:
        raise RuntimeError("deribit: мало свечей")
    return rows, "deribit"


# ---------- модель ----------

def build(coin, rows, source):
    price = rows[-1]["c"]
    bins = BIN[coin]
    active = []                                   # [уровень, сторона, объём]
    for i in range(1, len(rows)):
        r, prev = rows[i], rows[i - 1]
        # 1) сжечь уровни, которые цена прошла за этот час
        for p in active:
            if p[2] > 0 and ((p[1] == "long" and r["l"] <= p[0]) or (p[1] == "short" and r["h"] >= p[0])):
                p[2] = 0.0
        # 2) новые позиции
        if "oi" in r:
            d_oi = r["oi"] - prev["oi"]
            if d_oi < 0 and prev["oi"] > 0:           # закрытия → пропорционально уменьшить живые
                k = max(r["oi"] / prev["oi"], 0)
                for p in active:
                    p[2] *= k
            new = max(d_oi, 0.0)
        else:
            new = r["vol"] * 0.05                       # без OI: доля объёма (только относительная сила)
        if new <= 0:
            continue
        entry = (r["o"] + r["h"] + r["l"] + r["c"]) / 4
        long_share = 0.55 if r["c"] >= r["o"] else 0.45   # растущий час — чуть больше лонгов
        for lev, w in LEVERAGE.items():
            active.append([entry * (1 - 1 / lev + MM), "long", new * long_share * w])
            active.append([entry * (1 + 1 / lev - MM), "short", new * (1 - long_share) * w])

    agg = {}
    for lvl, side, amt in active:
        if amt <= 0 or abs(lvl / price - 1) > RANGE:
            continue
        if (side == "long" and lvl >= price) or (side == "short" and lvl <= price):
            continue
        b = round(lvl / bins) * bins
        agg[(side, b)] = agg.get((side, b), 0.0) + amt

    def side_list(side):
        lv = sorted([(b, a) for (s, b), a in agg.items() if s == side],
                    key=lambda x: -x[0] if side == "long" else x[0])   # от цены наружу
        cum, out = 0.0, []
        for b, a in lv:
            cum += a
            out.append({"price": b, "amount": a, "cum": cum, "dist_pct": (b / price - 1) * 100})
        return out

    longs, shorts = side_list("long"), side_list("short")
    total = sum(x["amount"] for x in longs + shorts) or 1.0
    def top(lst):
        best = sorted(lst, key=lambda x: -x["amount"])[:TOP]
        res = []
        for x in sorted(best, key=lambda x: abs(x["dist_pct"])):
            res.append({"price": round(x["price"]), "amount": round(x["amount"], 1) if source == "okx" else None,
                        "cum": round(x["cum"], 1) if source == "okx" else None,
                        "share_pct": round(100 * x["amount"] / total, 1), "dist_pct": round(x["dist_pct"], 1)})
        return res
    L, S = sum(x["amount"] for x in longs), sum(x["amount"] for x in shorts)
    return {
        "coin": coin, "price": round(price, 2), "source": source,
        "units": coin if source == "okx" else "relative",
        "long_below": top(longs), "short_above": top(shorts),
        "long_total": round(L, 1) if source == "okx" else None,
        "short_total": round(S, 1) if source == "okx" else None,
        "bias": "ниже больше (лонги)" if L > S * 1.2 else "выше больше (шорты)" if S > L * 1.2 else "примерно поровну",
    }


def one(coin):
    errs = []
    for fn in (okx, deribit):
        try:
            rows, src = fn(coin)
            return build(coin, rows, src)
        except Exception as e:  # noqa: BLE001
            errs.append(f"{fn.__name__}: {e}")
    return {"coin": coin, "error": "; ".join(errs)}


def fmt(m):
    if "error" in m:
        return f"{m['coin']}: данных нет ({m['error']})"
    u = m["coin"] if m["source"] == "okx" else "% силы"
    def line(x):
        amt = f"~{x['amount']:g} {u}" if x["amount"] is not None else f"{x['share_pct']}% силы"
        return f"  ${x['price']:,} ({x['dist_pct']:+.1f}%): {amt}" + (f", накоплено до уровня ~{x['cum']:g} {u}" if x["cum"] is not None else "")
    out = [f"{m['coin']} цена ${m['price']:,} · источник {m['source']} · перевес: {m['bias']}",
           "Лонги сгорят ниже:"] + [line(x) for x in m["long_below"]] + \
          ["Шорты сгорят выше:"] + [line(x) for x in m["short_above"]]
    return "\n".join(out)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    coins = [a.upper() for a in args] or ["BTC", "ETH"]
    res = {c: one(c) for c in coins}
    if "--print" in sys.argv:
        print("\n\n".join(fmt(m) for m in res.values()))
        return
    data = {"fetched_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "method": "оценка: рост OI → позиции по плечам 10/25/50/100x → уровни ликвидации, пройденные ценой убраны",
            "data": res}
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    print(OUT)
    print("\n\n".join(fmt(m) for m in res.values()))


if __name__ == "__main__":
    main()
