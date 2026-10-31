#!/usr/bin/env python3
"""Собирает красивый дашборд site/index.html из последнего отчёта, журнала и макро.

Без внешних зависимостей (свой мини-markdown). Запускается GitHub Action после
каждого коммита отчёта и деплоит результат (ноутбук не нужен). Живые цены BTC/ETH
тянутся с Deribit в момент сборки.
"""
import glob
import html
import json
import os
import re
import urllib.request
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ---------- данные ----------

def latest_report():
    files = glob.glob(os.path.join(ROOT, "reports", "*.md"))
    if not files:
        return None, None
    def key(p):
        name = os.path.basename(p)
        m = re.match(r"(\d{4}-\d{2}-\d{2})", name)
        return (m.group(1) if m else "0000-00-00", name)
    best = max(files, key=key)
    with open(best, encoding="utf-8") as f:
        return os.path.basename(best), f.read()


def all_report_dates():
    out = []
    for p in glob.glob(os.path.join(ROOT, "reports", "*.md")):
        name = os.path.basename(p)[:-3]
        out.append(name)
    return sorted(set(out), reverse=True)


def parse_journal():
    path = os.path.join(ROOT, "journal.md")
    brier = {"count": "—", "score": "—", "comment": ""}
    opens = []
    if not os.path.exists(path):
        return brier, opens
    text = open(path, encoding="utf-8").read()
    # сводный брайер — первая строка таблицы под заголовком
    m = re.search(r"Сводный Брайер-скор.*?\n\|.*?\|\n\|[-| ]+\|\n\|(.+?)\|\n",
                  text, re.S)
    if m:
        cells = [c.strip() for c in m.group(1).split("|")]
        if len(cells) >= 3:
            brier = {"count": cells[0], "score": cells[1], "comment": cells[2]}
    # открытые прогнозы
    seg = re.search(r"## Открытые прогнозы(.+?)\n---", text, re.S)
    if seg:
        for line in seg.group(1).splitlines():
            if not line.strip().startswith("|"):
                continue
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(cells) < 5 or cells[0] in ("№", "") or set(cells[0]) <= {"-"}:
                continue
            try:
                p = float(cells[4])
            except ValueError:
                continue
            opens.append({"n": cells[0], "text": cells[1],
                          "deadline": cells[3], "p": p})
    return brier, opens


def load_macro():
    path = os.path.join(ROOT, "data", "macro.json")
    if not os.path.exists(path):
        return {}
    return json.load(open(path, encoding="utf-8"))


def live_prices():
    out = {}
    base = "https://www.deribit.com/api/v2/public/ticker?instrument_name="
    for sym, key in (("BTC-PERPETUAL", "BTC"), ("ETH-PERPETUAL", "ETH")):
        try:
            req = urllib.request.Request(base + sym,
                                         headers={"User-Agent": "site/1.0"})
            r = json.load(urllib.request.urlopen(req, timeout=20))["result"]
            out[key] = {"price": r.get("index_price") or r.get("mark_price"),
                        "chg": r.get("price_change")}
        except Exception:
            out[key] = None
    return out


# ---------- мини-markdown ----------

def md_inline(s):
    s = html.escape(s)
    s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"`(.+?)`", r"<code>\1</code>", s)
    s = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"<em>\1</em>", s)
    return s


def md_to_html(md):
    lines = md.splitlines()
    out, i = [], 0
    while i < len(lines):
        ln = lines[i]
        if not ln.strip():
            i += 1
            continue
        if re.match(r"^\s*---\s*$", ln):
            out.append("<hr>")
            i += 1
            continue
        m = re.match(r"^(#{1,6})\s+(.*)", ln)
        if m:
            lvl = len(m.group(1))
            out.append(f"<h{lvl}>{md_inline(m.group(2))}</h{lvl}>")
            i += 1
            continue
        if ln.lstrip().startswith("|") and i + 1 < len(lines) and \
           re.match(r"^\s*\|[-:\s|]+\|\s*$", lines[i + 1]):
            head = [c.strip() for c in ln.strip().strip("|").split("|")]
            i += 2
            rows = []
            while i < len(lines) and lines[i].lstrip().startswith("|"):
                rows.append([c.strip() for c in
                             lines[i].strip().strip("|").split("|")])
                i += 1
            th = "".join(f"<th>{md_inline(c)}</th>" for c in head)
            trs = ""
            for row in rows:
                tds = "".join(f"<td>{md_inline(c)}</td>" for c in row)
                trs += f"<tr>{tds}</tr>"
            out.append(f'<div class="tw"><table><thead><tr>{th}</tr></thead>'
                       f"<tbody>{trs}</tbody></table></div>")
            continue
        if re.match(r"^\s*[-*]\s+", ln):
            items = []
            while i < len(lines) and re.match(r"^\s*[-*]\s+", lines[i]):
                items.append("<li>" +
                             md_inline(re.sub(r"^\s*[-*]\s+", "", lines[i])) +
                             "</li>")
                i += 1
            out.append("<ul>" + "".join(items) + "</ul>")
            continue
        buf = [ln]
        i += 1
        while i < len(lines) and lines[i].strip() and \
                not re.match(r"^(#{1,6}\s|\s*[-*]\s|\s*\|)", lines[i]) and \
                not re.match(r"^\s*---\s*$", lines[i]):
            buf.append(lines[i])
            i += 1
        out.append("<p>" + "<br>".join(md_inline(b) for b in buf) + "</p>")
    return "\n".join(out)


# ---------- рендер ----------

def fmt_price(v):
    if v is None:
        return "—"
    return f"${v:,.0f}" if v >= 100 else f"${v:,.2f}"


def chg_html(chg):
    if chg is None:
        return ""
    cls = "up" if chg >= 0 else "down"
    arrow = "▲" if chg >= 0 else "▼"
    return f'<span class="chg {cls}">{arrow} {abs(chg):.2f}%</span>'


def prob_color(p):
    if p >= 0.6:
        return "#34d399"
    if p >= 0.4:
        return "#fbbf24"
    return "#60a5fa"


def macro_tiles(macro):
    data = macro.get("data", {})
    order = [("SP500", "S&P 500"), ("NASDAQCOM", "Nasdaq"),
             ("DGS10", "10Y", "%"), ("DCOILWTICO", "WTI"), ("VIXCLS", "VIX")]
    tiles = []
    for item in order:
        sid, label = item[0], item[1]
        suffix = item[2] if len(item) > 2 else ""
        row = data.get(sid)
        if not row:
            continue
        val = row.get("value")
        pct = row.get("pct_change")
        bp = row.get("change_bp")
        if suffix == "%":
            valtxt = f"{val:.2f}%"
            sub = (f"{bp:+.0f} б.п." if bp is not None else "")
            cls = "up" if (bp or 0) >= 0 else "down"
        else:
            valtxt = f"{val:,.0f}" if val and val >= 100 else f"{val:,.2f}"
            sub = (f"{pct:+.2f}%" if pct is not None else "")
            cls = "up" if (pct or 0) >= 0 else "down"
        tiles.append(
            f'<div class="tile"><div class="tl">{label}</div>'
            f'<div class="tv">{valtxt}</div>'
            f'<div class="ts {cls}">{sub}</div></div>')
    return "".join(tiles)


def build():
    report_name, report_md = latest_report()
    date = (report_name or "").replace(".md", "")
    brier, opens = parse_journal()
    macro = load_macro()
    prices = live_prices()
    now = datetime.now(timezone.utc).astimezone()
    updated = now.strftime("%Y-%m-%d %H:%M UTC%z")

    btc = prices.get("BTC") or {}
    eth = prices.get("ETH") or {}

    # карточки прогнозов
    pred_cards = ""
    for o in opens:
        pct = int(round(o["p"] * 100))
        col = prob_color(o["p"])
        pred_cards += (
            f'<div class="pred">'
            f'<div class="pt">{md_inline(o["text"])}</div>'
            f'<div class="pmeta">до {html.escape(o["deadline"])}</div>'
            f'<div class="bar"><div class="fill" style="width:{pct}%;'
            f'background:{col}"></div><span class="pp">{pct}%</span></div>'
            f"</div>")

    try:
        score_val = float(brier["score"])
        bpos = min(max(score_val / 0.5, 0), 1) * 100
        bcol = "#34d399" if score_val < 0.25 else "#fbbf24" if score_val < 0.3 \
            else "#f87171"
    except (ValueError, TypeError):
        bpos, bcol = 0, "#60a5fa"

    report_html = md_to_html(report_md or "Отчёта пока нет.")

    repo = "https://github.com/polkadot88888888-coder/btc-eth-daily-forecast"
    archive = "".join(
        f'<a href="{repo}/blob/main/reports/{html.escape(d)}.md" '
        f'target="_blank" rel="noopener">{html.escape(d)}</a>'
        for d in all_report_dates()[:14])

    return TEMPLATE.format(
        date=html.escape(date), updated=html.escape(updated),
        btc_price=fmt_price(btc.get("price")), btc_chg=chg_html(btc.get("chg")),
        eth_price=fmt_price(eth.get("price")), eth_chg=chg_html(eth.get("chg")),
        brier_score=html.escape(str(brier["score"])),
        brier_count=html.escape(str(brier["count"])),
        brier_comment=md_inline(brier["comment"]),
        bpos=bpos, bcol=bcol,
        macro_tiles=macro_tiles(macro),
        pred_cards=pred_cards or "<p>Открытых прогнозов нет.</p>",
        pred_count=len(opens),
        report_html=report_html, archive=archive)


TEMPLATE = """<!doctype html>
<html lang="ru"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>BTC/ETH · прогноз {date}</title>
<style>
:root{{--bg:#0a0d14;--card:#141925;--card2:#1b2130;--bd:#262d3d;
--tx:#e6e9f0;--mut:#8b93a7;--btc:#f7931a;--eth:#8b9dff;--up:#34d399;--down:#f87171}}
*{{box-sizing:border-box}}
body{{margin:0;background:radial-gradient(1200px 600px at 50% -10%,#16203a 0%,var(--bg) 55%);
color:var(--tx);font:16px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
-webkit-font-smoothing:antialiased;padding:0 16px 80px}}
.wrap{{max-width:860px;margin:0 auto}}
header{{padding:28px 0 8px;display:flex;justify-content:space-between;align-items:baseline;flex-wrap:wrap;gap:8px}}
h1{{font-size:20px;margin:0;letter-spacing:.3px}}
.upd{{color:var(--mut);font-size:13px}}
.hero{{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin:14px 0}}
.coin{{background:linear-gradient(160deg,var(--card2),var(--card));border:1px solid var(--bd);
border-radius:18px;padding:18px 20px;position:relative;overflow:hidden}}
.coin .nm{{font-weight:700;font-size:14px;letter-spacing:1px}}
.coin.btc .nm{{color:var(--btc)}} .coin.eth .nm{{color:var(--eth)}}
.coin .pr{{font-size:34px;font-weight:800;margin-top:6px;letter-spacing:-.5px}}
.coin .glow{{position:absolute;right:-30px;top:-30px;width:120px;height:120px;border-radius:50%;filter:blur(40px);opacity:.35}}
.coin.btc .glow{{background:var(--btc)}} .coin.eth .glow{{background:var(--eth)}}
.chg{{font-size:14px;font-weight:600}} .chg.up{{color:var(--up)}} .chg.down{{color:var(--down)}}
.sect{{background:var(--card);border:1px solid var(--bd);border-radius:18px;padding:18px 20px;margin:14px 0}}
.sect h2{{font-size:13px;letter-spacing:1px;text-transform:uppercase;color:var(--mut);margin:0 0 14px}}
.brier{{display:flex;align-items:center;gap:16px;flex-wrap:wrap}}
.bignum{{font-size:40px;font-weight:800;line-height:1}}
.track{{flex:1;min-width:180px;height:10px;border-radius:6px;background:#0e1420;position:relative;overflow:hidden}}
.track .mk{{position:absolute;top:-4px;width:2px;height:18px;background:var(--mut);left:50%}}
.track .dot{{position:absolute;top:-3px;width:16px;height:16px;border-radius:50%;transform:translateX(-50%)}}
.bcom{{color:var(--mut);font-size:13px;flex-basis:100%}}
.macro{{display:grid;grid-template-columns:repeat(5,1fr);gap:10px}}
.tile{{background:var(--card2);border:1px solid var(--bd);border-radius:12px;padding:10px 8px;text-align:center}}
.tl{{color:var(--mut);font-size:11px;letter-spacing:.5px}} .tv{{font-size:17px;font-weight:700;margin:3px 0}}
.ts{{font-size:12px;font-weight:600}} .ts.up{{color:var(--up)}} .ts.down{{color:var(--down)}}
.pred{{background:var(--card2);border:1px solid var(--bd);border-radius:12px;padding:12px 14px;margin-bottom:10px}}
.pt{{font-weight:600;font-size:15px}} .pmeta{{color:var(--mut);font-size:12px;margin:4px 0 8px}}
.bar{{height:22px;border-radius:7px;background:#0e1420;position:relative;overflow:hidden}}
.fill{{height:100%;border-radius:7px;transition:width .6s}}
.pp{{position:absolute;right:9px;top:0;line-height:22px;font-size:12px;font-weight:700}}
.report h1{{font-size:19px;margin:18px 0 6px}} .report h2{{font-size:16px;margin:18px 0 8px;color:#cdd3e0}}
.report strong{{color:#fff}} .report code{{background:#0e1420;padding:1px 6px;border-radius:5px;font-size:13px}}
.report hr{{border:0;border-top:1px solid var(--bd);margin:16px 0}}
.report p{{margin:8px 0;color:#c7cdda}}
.tw{{overflow-x:auto;margin:10px 0}} table{{border-collapse:collapse;width:100%;font-size:13px}}
th,td{{border:1px solid var(--bd);padding:6px 9px;text-align:left}} th{{background:#0e1420;color:var(--mut)}}
.arch{{display:flex;flex-wrap:wrap;gap:8px}} .arch a{{color:var(--eth);text-decoration:none;font-size:13px;
background:var(--card2);border:1px solid var(--bd);padding:4px 10px;border-radius:8px}}
.disc{{color:var(--mut);font-size:12px;text-align:center;margin-top:24px}}
@media(max-width:560px){{.hero{{grid-template-columns:1fr}}.macro{{grid-template-columns:repeat(3,1fr)}}.coin .pr{{font-size:30px}}}}
</style></head>
<body><div class="wrap">
<header><h1>📈 Прогноз BTC / ETH · {date}</h1><span class="upd">обновлено {updated}</span></header>

<div class="hero">
  <div class="coin btc"><div class="glow"></div><div class="nm">BITCOIN</div>
    <div class="pr">{btc_price}</div>{btc_chg}</div>
  <div class="coin eth"><div class="glow"></div><div class="nm">ETHEREUM</div>
    <div class="pr">{eth_price}</div>{eth_chg}</div>
</div>

<div class="sect"><h2>Точность прогнозов · Брайер</h2>
  <div class="brier"><div class="bignum" style="color:{bcol}">{brier_score}</div>
  <div class="track"><div class="mk"></div><div class="dot" style="left:{bpos}%;background:{bcol}"></div></div>
  <div class="bcom">{brier_count} закрытых прогнозов · 0 — идеально, 0.25 — монетка. {brier_comment}</div></div>
</div>

<div class="sect"><h2>Макро · на закрытии</h2><div class="macro">{macro_tiles}</div></div>

<div class="sect"><h2>Открытые прогнозы · {pred_count}</h2>{pred_cards}</div>

<div class="sect report"><h2>Полный отчёт</h2>{report_html}</div>

<div class="sect"><h2>Архив</h2><div class="arch">{archive}</div></div>

<div class="disc">Я не финансовый советник · это эксперимент с прогнозами, а не торговый сигнал</div>
</div></body></html>"""


def main():
    os.makedirs(os.path.join(ROOT, "site"), exist_ok=True)
    out = os.path.join(ROOT, "site", "index.html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(build())
    print(f"site/index.html готов ({os.path.getsize(out)} байт)")


if __name__ == "__main__":
    main()
