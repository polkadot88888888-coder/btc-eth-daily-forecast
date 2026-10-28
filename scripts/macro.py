#!/usr/bin/env python3
"""Снимок традиционного макро-рынка -> data/macro.json через FRED (ФРС Сент-Луис).

Запускается GitHub Action. Ключ FRED берётся из переменной окружения FRED_API_KEY
(хранится в секрете репозитория). Данные FRED — официальные, на конец торгового дня
(EOD), поэтому значения «вчерашнее закрытие», не live-фьючерсы. Падение одной серии
не роняет весь снимок.

Получить бесплатный ключ: https://fred.stlouisfed.org/docs/api/api_key.html
"""
import json
import os
import time
import urllib.request
from datetime import datetime, timezone

# series_id FRED -> (человекочитаемое имя, тип: 'yield' в % или 'level')
SERIES = {
    "SP500":        ("S&P 500 (индекс)",            "level"),
    "NASDAQCOM":    ("Nasdaq Composite (индекс)",   "level"),
    "DJIA":         ("Dow Jones (индекс)",          "level"),
    "DGS10":        ("UST 10Y доходность, %",       "yield"),
    "DGS2":         ("UST 2Y доходность, %",        "yield"),
    "DTWEXBGS":     ("Индекс доллара (широкий)",    "level"),
    "DCOILWTICO":   ("Нефть WTI, $/барр",           "level"),
    "DCOILBRENTEU": ("Нефть Brent, $/барр",         "level"),
    "VIXCLS":       ("VIX (волатильность акций)",   "level"),
}

BASE = "https://api.stlouisfed.org/fred/series/observations"


def fetch_series(series_id, key):
    # берём последние наблюдения по убыванию даты; пропускаем пропуски "."
    url = (f"{BASE}?series_id={series_id}&api_key={key}&file_type=json"
           "&sort_order=desc&limit=8")
    req = urllib.request.Request(url, headers={"User-Agent": "forecast-bot/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        obs = json.load(r)["observations"]
    vals = [(o["date"], float(o["value"])) for o in obs if o["value"] not in (".", "")]
    if len(vals) < 1:
        raise ValueError("нет валидных наблюдений")
    date, last = vals[0]
    prev = vals[1][1] if len(vals) > 1 else None
    return date, last, prev


def main():
    key = os.environ.get("FRED_API_KEY", "").strip()
    if not key:
        raise SystemExit("Нет FRED_API_KEY в окружении (секрет репозитория).")

    out, errors = {}, {}
    for sid, (name, kind) in SERIES.items():
        for attempt in range(3):
            try:
                date, last, prev = fetch_series(sid, key)
                row = {"name": name, "date": date, "value": last}
                if prev is not None:
                    if kind == "yield":
                        row["change_bp"] = round((last - prev) * 100, 1)
                    row["pct_change"] = round((last / prev - 1) * 100, 2) if prev else None
                out[sid] = row
                break
            except Exception as e:  # noqa: BLE001
                if attempt == 2:
                    errors[sid] = f"{type(e).__name__}: {e}"
                else:
                    time.sleep(2)

    now = datetime.now(timezone.utc)
    snapshot = {
        "fetched_at_utc": now.strftime("%Y-%m-%d %H:%M:%SZ"),
        "fetched_at_unix": int(now.timestamp()),
        "source": "FRED (Federal Reserve Bank of St. Louis), EOD",
        "note": "Данные на конец торгового дня, не live-котировки.",
        "data": out,
        "errors": errors,
    }
    os.makedirs("data", exist_ok=True)
    with open("data/macro.json", "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=2)

    print(f"data/macro.json: {len(out)} серий, {len(errors)} ошибок, "
          f"{snapshot['fetched_at_utc']}")
    for sid, row in out.items():
        extra = row.get("change_bp")
        extra = f"{extra:+} б.п." if extra is not None else f"{row.get('pct_change')}%"
        print(f"  {sid} ({row['date']}): {row['value']} ({extra})")
    for sid, err in errors.items():
        print(f"  ! {sid}: {err}")


if __name__ == "__main__":
    main()
