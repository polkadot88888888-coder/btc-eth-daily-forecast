#!/usr/bin/env python3
"""Деривативы BTC/ETH БЕЗ стороннего MCP-коннектора.

Коннектор DOUBLETOP — это просто прокси к публичному API Coinalyze
(api.coinalyze.net/v1). Здесь мы ходим туда НАПРЯМУЮ своим ключом
COINALYZE_API_KEY (бесплатный, свой — не зависит от DOUBLETOP).

Если ключа Coinalyze нет — не падаем: берём открытый интерес и funding
прямо с публичного Deribit (без ключа), а изменение OI за сутки считаем
по сохранённому вчерашнему снимку (data/derivs_state.json). Ликвидации
(агрегированные по биржам) есть только у Coinalyze — без ключа их опускаем.

Пишет data/derivs.json. Запускается в GitHub Action (интернет открыт),
поэтому ни egress-allowlist, ни коннектор рутине больше не нужны.
"""
import json, os, time, urllib.request, urllib.parse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "data", "derivs.json")
STATE = os.path.join(ROOT, "data", "derivs_state.json")
CZ_KEY = os.environ.get("COINALYZE_API_KEY", "").strip()
UA = {"User-Agent": "btc-eth-daily/derivs"}


def get(url, headers=None, timeout=25):
    req = urllib.request.Request(url, headers={**UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def fmt_usd(v):
    v = float(v)
    if v >= 1e9:
        return f"${v/1e9:.2f}B"
    if v >= 1e6:
        return f"${v/1e6:.0f}M"
    if v >= 1e3:
        return f"${v/1e3:.0f}k"
    return f"${v:.0f}"


# ─── Coinalyze (напрямую, свой ключ) ──────────────────────────────────────
def coinalyze(path, symbols, interval="1hour", hours=24, usd=False):
    base = "https://api.coinalyze.net/v1/" + path
    params = {"symbols": symbols, "interval": interval,
              "from": int(time.time()) - hours * 3600, "to": int(time.time())}
    if usd:
        params["convert_to_usd"] = "true"
    return get(f"{base}?{urllib.parse.urlencode(params)}", headers={"api_key": CZ_KEY})


def deribit_dvol():
    """Текущий индекс ожидаемой волатильности Deribit (DVOL), без ключа."""
    try:
        now = int(time.time() * 1000)
        q = urllib.parse.urlencode({"currency": "BTC", "start_timestamp": now - 3*86400*1000,
                                    "end_timestamp": now, "resolution": "3600"})
        d = get("https://www.deribit.com/api/v2/public/get_volatility_index_data?" + q)
        rows = d["result"]["data"]
        return f"{rows[-1][4]:.0f}%" if rows else None
    except Exception as e:
        print("dvol fail:", e)
        return None


def via_coinalyze():
    # Binance BTCUSDT-перп — самый ликвидный рынок; convert_to_usd=true → всё в $.
    sym = "BTCUSDT_PERP.A"
    oi = coinalyze("open-interest-history", sym, usd=True)
    liq = coinalyze("liquidation-history", sym, usd=True)
    fnd = coinalyze("funding-rate-history", sym)
    hist = (oi[0]["history"] if oi and oi[0].get("history") else [])
    if not hist:
        raise RuntimeError("coinalyze: пустой OI")
    oi_now = hist[-1]["c"]
    oi_then = hist[0]["o"]
    oi_chg = (oi_now - oi_then) / oi_then * 100 if oi_then else 0.0
    lh = (liq[0]["history"] if liq and liq[0].get("history") else [])
    longs = sum(x.get("l", 0) for x in lh)
    shorts = sum(x.get("s", 0) for x in lh)
    total = longs + shorts
    if total < 1:
        liq24, side = "тихо", "без каскадов"
    else:
        liq24 = fmt_usd(total)
        side = ("лонги" if longs > shorts * 1.3 else
                "шорты" if shorts > longs * 1.3 else "сбалансировано")
    fh = (fnd[0]["history"] if fnd and fnd[0].get("history") else [])
    fund = f"{fh[-1]['c']:+.3f}%" if fh else None
    return {"source": "coinalyze", "oi": fmt_usd(oi_now),
            "oi_chg": f"{oi_chg:+.1f}% 24ч", "oi_chg_num": round(oi_chg, 2),
            "liq24": liq24, "liq_side": side, "funding": fund, "dvol": deribit_dvol()}


# ─── Deribit (без ключа, запасной путь для OI + funding) ──────────────────
def via_deribit():
    t = get("https://www.deribit.com/api/v2/public/ticker?instrument_name=BTC-PERPETUAL")
    r = t["result"]
    oi_now = r["open_interest"]
    fund8h = r.get("funding_8h")
    fund = f"{fund8h*100:+.3f}%" if fund8h is not None else None
    prev = {}
    try:
        prev = json.load(open(STATE))
    except Exception:
        pass
    oi_prev = prev.get("oi_raw")
    if oi_prev:
        chg = (oi_now - oi_prev) / oi_prev * 100
        oi_chg, num = f"{chg:+.1f}% 24ч", round(chg, 2)
    else:
        oi_chg, num = None, None
    json.dump({"oi_raw": oi_now, "at": int(time.time())}, open(STATE, "w"))
    return {"source": "deribit", "oi": fmt_usd(oi_now),
            "oi_chg": oi_chg, "oi_chg_num": num,
            "liq24": None, "liq_side": None, "funding": fund, "dvol": deribit_dvol()}


def verdict(d):
    bits = []
    n = d.get("oi_chg_num")
    if n is not None:
        if abs(n) < 0.5:
            bits.append("открытый интерес почти не двигался за сутки")
        elif n > 0:
            bits.append(f"открытый интерес вырос на {n:+.1f}% (набор позиций)")
        else:
            bits.append(f"открытый интерес упал на {n:.1f}% (закрытие/делёвередж)")
    f = d.get("funding")
    if f:
        pos = not str(f).lstrip().startswith("-")
        bits.append(f"funding {f} — перекос в {'лонги' if pos else 'шорты'}")
    if d.get("liq24") and d["liq24"] != "тихо":
        bits.append(f"ликвидаций за сутки {d['liq24']} (сильнее {d.get('liq_side','')})")
    elif d.get("liq24") == "тихо":
        bits.append("принудительных ликвидаций почти не было — рынок спокоен")
    src = "Coinalyze" if d.get("source") == "coinalyze" else "Deribit"
    return ("; ".join(bits).capitalize() + f". Данные {src}, напрямую, без стороннего коннектора.") if bits else ""


def main():
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    d = None
    if CZ_KEY:
        try:
            d = via_coinalyze()
        except Exception as e:
            print("coinalyze fail:", e)
    if d is None:
        d = via_deribit()
    d["verdict"] = verdict(d)
    d["updated"] = int(time.time())
    json.dump(d, open(OUT, "w"), ensure_ascii=False, indent=2)
    print("derivs.json:", json.dumps(d, ensure_ascii=False))


if __name__ == "__main__":
    main()
