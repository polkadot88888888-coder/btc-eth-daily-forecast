#!/usr/bin/env python3
"""Собирает «дофаминовый» дашборд site/index.html: минимум текста, максимум смысла
с первого взгляда. Полный отчёт спрятан под спойлер, внизу — короткая выжимка.

Данные: последний reports/*.md (+ встроенный блок <!--CARD {json} CARD--> от агента),
journal.md, data/macro.json, живые цены Deribit. Без внешних зависимостей.
Запускается GitHub Action после каждого коммита — ноутбук не нужен.
"""
import glob
import html
import json
import os
import re
import urllib.request
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = "https://github.com/polkadot88888888-coder/btc-eth-daily-forecast"


# ---------- данные ----------

def latest_report():
    files = glob.glob(os.path.join(ROOT, "reports", "*.md"))
    if not files:
        return None, ""
    def key(p):
        m = re.match(r"(\d{4}-\d{2}-\d{2})", os.path.basename(p))
        return (m.group(1) if m else "0", os.path.basename(p))
    best = max(files, key=key)
    return os.path.basename(best), open(best, encoding="utf-8").read()


def all_dates():
    return sorted({os.path.basename(p)[:-3]
                   for p in glob.glob(os.path.join(ROOT, "reports", "*.md"))},
                  reverse=True)


def extract_card(md):
    """Структурный блок от агента: <!--CARD {json} CARD-->. Нет — пусто."""
    m = re.search(r"<!--\s*CARD\s*(\{.*?\})\s*CARD\s*-->", md, re.S)
    if not m:
        return None, md
    body = md[:m.start()] + md[m.end():]
    try:
        return json.loads(m.group(1)), body
    except Exception:
        return None, body


def parse_levels(md):
    """Фолбэк: вытащить уровни из строк «Уровни BTC: поддержка … сопротивление …»."""
    def grab(coin):
        m = re.search(rf"Уровни\s+{coin}.*?поддержк\w*[:\s]*([^\n]*?)"
                      rf"сопротивлен\w*[:\s]*([^\n]*)", md, re.I)
        if not m:
            return [], []
        nums = lambda s: re.findall(r"\$?[\d][\d\s.,]*[kкК]?", s)
        sup = [x.strip(" .") for x in m.group(1).split("/") if x.strip()][:3]
        res = [x.strip(" .") for x in m.group(2).split("/") if x.strip()][:3]
        return sup, res
    b_s, b_r = grab("BTC")
    e_s, e_r = grab("ETH")
    return {"btc_sup": b_s, "btc_res": b_r, "eth_sup": e_s, "eth_res": e_r}


def parse_journal():
    path = os.path.join(ROOT, "journal.md")
    brier = {"count": "—", "score": "—", "comment": ""}
    opens = []
    if not os.path.exists(path):
        return brier, opens
    text = open(path, encoding="utf-8").read()
    m = re.search(r"Сводный Брайер-скор.*?\n\|.*?\|\n\|[-| ]+\|\n\|(.+?)\|\n",
                  text, re.S)
    if m:
        c = [x.strip() for x in m.group(1).split("|")]
        if len(c) >= 3:
            brier = {"count": c[0], "score": c[1], "comment": c[2]}
    seg = re.search(r"## Открытые прогнозы(.+?)\n---", text, re.S)
    if seg:
        for line in seg.group(1).splitlines():
            if not line.strip().startswith("|"):
                continue
            c = [x.strip() for x in line.strip().strip("|").split("|")]
            if len(c) < 5 or c[0] in ("№", "") or set(c[0]) <= {"-"}:
                continue
            try:
                p = float(c[4])
            except ValueError:
                continue
            opens.append({"n": c[0], "text": c[1], "deadline": c[3], "p": p})
    return brier, opens


def load_macro():
    p = os.path.join(ROOT, "data", "macro.json")
    return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else {}


def live_prices():
    out = {}
    base = "https://www.deribit.com/api/v2/public/ticker?instrument_name="
    for sym, k in (("BTC-PERPETUAL", "BTC"), ("ETH-PERPETUAL", "ETH")):
        try:
            req = urllib.request.Request(base + sym,
                                         headers={"User-Agent": "site/1.0"})
            r = json.load(urllib.request.urlopen(req, timeout=20))["result"]
            out[k] = {"price": r.get("index_price") or r.get("mark_price"),
                      "chg": r.get("price_change")}
        except Exception:
            out[k] = None
    return out


# ---------- мини-markdown (для спойлера с полным отчётом) ----------

def md_inline(s):
    s = html.escape(s)
    s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"`(.+?)`", r"<code>\1</code>", s)
    return s


def md_to_html(md):
    lines, out, i = md.splitlines(), [], 0
    while i < len(lines):
        ln = lines[i]
        if not ln.strip():
            i += 1; continue
        if re.match(r"^\s*---\s*$", ln):
            out.append("<hr>"); i += 1; continue
        m = re.match(r"^(#{1,6})\s+(.*)", ln)
        if m:
            lvl = min(len(m.group(1)) + 1, 6)
            out.append(f"<h{lvl}>{md_inline(m.group(2))}</h{lvl}>"); i += 1; continue
        if ln.lstrip().startswith("|") and i + 1 < len(lines) and \
           re.match(r"^\s*\|[-:\s|]+\|\s*$", lines[i + 1]):
            head = [c.strip() for c in ln.strip().strip("|").split("|")]
            i += 2; rows = []
            while i < len(lines) and lines[i].lstrip().startswith("|"):
                rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            th = "".join(f"<th>{md_inline(c)}</th>" for c in head)
            trs = "".join("<tr>" + "".join(f"<td>{md_inline(c)}</td>" for c in r) + "</tr>"
                          for r in rows)
            out.append(f'<div class="tw"><table><thead><tr>{th}</tr></thead>'
                       f"<tbody>{trs}</tbody></table></div>")
            continue
        if re.match(r"^\s*[-*]\s+", ln):
            items = []
            while i < len(lines) and re.match(r"^\s*[-*]\s+", lines[i]):
                items.append("<li>" + md_inline(re.sub(r"^\s*[-*]\s+", "", lines[i])) + "</li>")
                i += 1
            out.append("<ul>" + "".join(items) + "</ul>"); continue
        buf = [ln]; i += 1
        while i < len(lines) and lines[i].strip() and \
                not re.match(r"^(#{1,6}\s|\s*[-*]\s|\s*\||\s*---\s*$)", lines[i]):
            buf.append(lines[i]); i += 1
        out.append("<p>" + "<br>".join(md_inline(b) for b in buf) + "</p>")
    return "\n".join(out)


# ---------- рендер ----------

def fmt_price(v):
    if v is None:
        return "—"
    return f"${v:,.0f}" if v >= 100 else f"${v:,.2f}"


def chg_badge(chg):
    if chg is None:
        return ""
    cls = "up" if chg >= 0 else "down"
    return f'<span class="chg {cls}">{"▲" if chg>=0 else "▼"} {abs(chg):.2f}%</span>'


def prob_color(p):
    return "#34d399" if p >= 0.6 else "#fbbf24" if p >= 0.4 else "#60a5fa"


def chips(vals, kind):
    cls = "sup" if kind == "sup" else "res"
    return "".join(f'<span class="chip {cls}">{md_inline(str(v))}</span>'
                   for v in vals if str(v).strip())


def macro_tiles(macro):
    data = macro.get("data", {})
    order = [("SP500", "S&P 500"), ("NASDAQCOM", "Nasdaq"),
             ("DGS10", "10Y", "%"), ("DCOILWTICO", "WTI"), ("VIXCLS", "VIX")]
    out = ""
    for it in order:
        sid, label = it[0], it[1]
        suffix = it[2] if len(it) > 2 else ""
        row = data.get(sid)
        if not row:
            continue
        val, pct, bp = row.get("value"), row.get("pct_change"), row.get("change_bp")
        if suffix == "%":
            valtxt, sub = f"{val:.2f}%", (f"{bp:+.0f}б.п." if bp is not None else "")
            cls = "up" if (bp or 0) >= 0 else "down"
        else:
            valtxt = f"{val:,.0f}" if val and val >= 100 else f"{val:,.2f}"
            sub = f"{pct:+.2f}%" if pct is not None else ""
            cls = "up" if (pct or 0) >= 0 else "down"
        out += (f'<div class="tile"><div class="tl">{label}</div>'
                f'<div class="tv">{valtxt}</div><div class="ts {cls}">{sub}</div></div>')
    return out


def build():
    name, md = latest_report()
    date = (name or "").replace(".md", "")
    card, md_body = extract_card(md)
    lv = parse_levels(md_body)
    if card:
        for k in ("btc_sup", "btc_res", "eth_sup", "eth_res"):
            if card.get(k):
                lv[k] = card[k]
    brier, opens = parse_journal()
    macro = load_macro()
    prices = live_prices()
    btc, eth = prices.get("BTC") or {}, prices.get("ETH") or {}
    updated = datetime.now(timezone.utc).astimezone().strftime("%d.%m %H:%M")

    # настроение
    dir_ = (card or {}).get("dir", "flat")
    mood = (card or {}).get("mood", "")
    headline = (card or {}).get("headline", "")
    mood_cls = {"up": "mup", "down": "mdn"}.get(dir_, "mfl")
    mood_ico = {"up": "🚀", "down": "🩸", "flat": "➖"}.get(dir_, "➖")
    mood_html = ""
    if mood or headline:
        mood_html = (f'<div class="mood {mood_cls}"><div class="mi">{mood_ico}</div>'
                     f'<div><div class="ml">{md_inline(mood)}</div>'
                     f'<div class="mh">{md_inline(headline)}</div></div></div>')

    # топ-ставки: по убыванию уверенности |p-0.5|
    ranked = sorted(opens, key=lambda o: abs(o["p"] - 0.5), reverse=True)
    def bet_card(o, big=False):
        pct = int(round(o["p"] * 100))
        col = prob_color(o["p"])
        cl = "bet big" if big else "bet"
        return (f'<div class="{cl}"><div class="bt">{md_inline(o["text"])}</div>'
                f'<div class="bm">до {html.escape(o["deadline"])}</div>'
                f'<div class="bar"><div class="fill" style="width:{pct}%;background:{col}">'
                f'</div><span class="pp">{pct}%</span></div></div>')
    top = "".join(bet_card(o, big=True) for o in ranked[:3])
    rest = "".join(bet_card(o) for o in ranked[3:])
    rest_block = (f'<details class="more"><summary>Ещё прогнозы · {len(ranked)-3}'
                  f'</summary>{rest}</details>') if len(ranked) > 3 else ""

    # брайер
    try:
        sv = float(brier["score"])
        bpos = min(max(sv / 0.5, 0), 1) * 100
        bcol = "#34d399" if sv < 0.25 else "#fbbf24" if sv < 0.3 else "#f87171"
    except (ValueError, TypeError):
        bpos, bcol = 50, "#60a5fa"

    # уровни
    lvl_html = ""
    if any(lv.values()):
        lvl_html = f"""<div class="sect"><h2>Ключевые уровни</h2>
        <div class="lvgrid">
          <div><div class="lvc btc">BTC</div>
            <div class="lvrow"><span class="lk">держит</span>{chips(lv.get("btc_sup",[]),"sup")}</div>
            <div class="lvrow"><span class="lk">мешает</span>{chips(lv.get("btc_res",[]),"res")}</div></div>
          <div><div class="lvc eth">ETH</div>
            <div class="lvrow"><span class="lk">держит</span>{chips(lv.get("eth_sup",[]),"sup")}</div>
            <div class="lvrow"><span class="lk">мешает</span>{chips(lv.get("eth_res",[]),"res")}</div></div>
        </div></div>"""

    # выжимка внизу (от агента или собранная)
    tldr = (card or {}).get("tldr") or []
    if not tldr:
        tldr = [f"💰 BTC {fmt_price(btc.get('price'))}, ETH {fmt_price(eth.get('price'))}.",
                f"🎯 Точность прогнозов (Брайер): {brier['score']} за {brier['count']} шт.",
                ("🔥 Главная ставка: " + md_inline(ranked[0]["text"]) +
                 f" — {int(round(ranked[0]['p']*100))}%.") if ranked else ""]
    tldr_html = "".join(f"<li>{md_inline(t)}</li>" for t in tldr if str(t).strip())

    report_html = md_to_html(md_body or "Отчёта пока нет.")
    archive = "".join(f'<a href="{REPO}/blob/main/reports/{html.escape(d)}.md" '
                      f'target="_blank" rel="noopener">{html.escape(d)}</a>'
                      for d in all_dates()[:14])

    return TEMPLATE.format(
        date=html.escape(date), updated=html.escape(updated),
        btc_price=fmt_price(btc.get("price")), btc_chg=chg_badge(btc.get("chg")),
        eth_price=fmt_price(eth.get("price")), eth_chg=chg_badge(eth.get("chg")),
        mood_html=mood_html, levels=lvl_html,
        top_bets=top or "<p>Открытых ставок нет.</p>", rest=rest_block,
        macro=macro_tiles(macro),
        brier_score=html.escape(str(brier["score"])), bpos=bpos, bcol=bcol,
        brier_count=html.escape(str(brier["count"])),
        tldr=tldr_html, report_html=report_html, archive=archive)


TEMPLATE = """<!doctype html>
<html lang="ru"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>BTC/ETH · {date}</title>
<style>
:root{{--bg:#07090f;--card:#121724;--card2:#1a2132;--bd:#242c3e;--tx:#eef1f7;
--mut:#8b93a7;--btc:#f7931a;--eth:#8b9dff;--up:#34d399;--down:#f87171}}
*{{box-sizing:border-box}}
body{{margin:0;background:radial-gradient(1200px 700px at 50% -15%,#17233f 0%,var(--bg) 55%);
color:var(--tx);font:16px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
-webkit-font-smoothing:antialiased;padding:0 15px 70px}}
.wrap{{max-width:780px;margin:0 auto}}
header{{display:flex;justify-content:space-between;align-items:baseline;flex-wrap:wrap;gap:6px;padding:26px 2px 4px}}
h1{{font-size:18px;margin:0;font-weight:800;letter-spacing:.2px}}
.upd{{color:var(--mut);font-size:12px}}
.hero{{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin:12px 0}}
.coin{{background:linear-gradient(160deg,var(--card2),var(--card));border:1px solid var(--bd);
border-radius:20px;padding:16px 18px;position:relative;overflow:hidden}}
.coin .nm{{font-weight:800;font-size:13px;letter-spacing:1.5px}}
.coin.btc .nm{{color:var(--btc)}}.coin.eth .nm{{color:var(--eth)}}
.coin .pr{{font-size:33px;font-weight:900;margin-top:4px;letter-spacing:-1px}}
.coin .glow{{position:absolute;right:-28px;top:-28px;width:110px;height:110px;border-radius:50%;filter:blur(42px);opacity:.4}}
.coin.btc .glow{{background:var(--btc)}}.coin.eth .glow{{background:var(--eth)}}
.chg{{font-size:14px;font-weight:700}}.chg.up{{color:var(--up)}}.chg.down{{color:var(--down)}}
.mood{{display:flex;gap:14px;align-items:center;border-radius:18px;padding:16px 18px;margin:12px 0;border:1px solid}}
.mood .mi{{font-size:30px}}.mood .ml{{font-weight:800;font-size:17px}}.mood .mh{{font-size:14px;opacity:.92;margin-top:2px}}
.mup{{background:linear-gradient(120deg,#0e2a1f,#101a2a);border-color:#1f5137}}
.mdn{{background:linear-gradient(120deg,#2a1116,#13131f);border-color:#5a2730}}
.mfl{{background:linear-gradient(120deg,#20222e,#13131f);border-color:#39405a}}
.sect{{background:var(--card);border:1px solid var(--bd);border-radius:18px;padding:16px 18px;margin:12px 0}}
.sect h2{{font-size:12px;letter-spacing:1.2px;text-transform:uppercase;color:var(--mut);margin:0 0 12px}}
.lvgrid{{display:grid;grid-template-columns:1fr 1fr;gap:16px}}
.lvc{{font-weight:800;font-size:13px;letter-spacing:1px;margin-bottom:6px}}
.lvc.btc{{color:var(--btc)}}.lvc.eth{{color:var(--eth)}}
.lvrow{{display:flex;flex-wrap:wrap;gap:6px;align-items:center;margin:5px 0}}
.lk{{font-size:11px;color:var(--mut);width:46px}}
.chip{{font-size:13px;font-weight:700;padding:3px 9px;border-radius:8px}}
.chip.sup{{background:#0f2a20;color:#6ee7b7;border:1px solid #1f5137}}
.chip.res{{background:#2a1317;color:#fca5a5;border:1px solid #5a2730}}
.bet{{background:var(--card2);border:1px solid var(--bd);border-radius:12px;padding:11px 13px;margin-bottom:9px}}
.bet.big{{padding:13px 15px}}.bet .bt{{font-weight:700;font-size:15px}}.bet.big .bt{{font-size:16px}}
.bet .bm{{color:var(--mut);font-size:12px;margin:3px 0 8px}}
.bar{{height:24px;border-radius:8px;background:#0c1220;position:relative;overflow:hidden}}
.fill{{height:100%;border-radius:8px;transition:width .7s cubic-bezier(.2,.9,.3,1)}}
.pp{{position:absolute;right:10px;top:0;line-height:24px;font-size:12px;font-weight:800}}
.more{{margin-top:4px}}.more summary{{cursor:pointer;color:var(--eth);font-size:13px;font-weight:600;padding:6px 0}}
.macro{{display:grid;grid-template-columns:repeat(5,1fr);gap:9px}}
.tile{{background:var(--card2);border:1px solid var(--bd);border-radius:12px;padding:9px 6px;text-align:center}}
.tl{{color:var(--mut);font-size:10px;letter-spacing:.3px}}.tv{{font-size:16px;font-weight:800;margin:2px 0}}
.ts{{font-size:11px;font-weight:700}}.ts.up{{color:var(--up)}}.ts.down{{color:var(--down)}}
.brier{{display:flex;align-items:center;gap:14px;flex-wrap:wrap}}
.bignum{{font-size:34px;font-weight:900;line-height:1}}
.track{{flex:1;min-width:160px;height:10px;border-radius:6px;background:#0c1220;position:relative}}
.track .mk{{position:absolute;top:-4px;left:50%;width:2px;height:18px;background:var(--mut)}}
.track .dot{{position:absolute;top:-3px;width:16px;height:16px;border-radius:50%;transform:translateX(-50%)}}
.bcom{{color:var(--mut);font-size:12px;flex-basis:100%}}
.tldr{{background:linear-gradient(135deg,#1a2740,#141a2b);border:1px solid #33405f;
border-radius:20px;padding:18px 20px;margin:16px 0}}
.tldr h2{{color:#aab6ff}}.tldr ul{{margin:0;padding:0;list-style:none}}
.tldr li{{font-size:16px;font-weight:600;padding:8px 0;border-bottom:1px solid #222b40}}
.tldr li:last-child{{border:0}}
details.report{{background:var(--card);border:1px solid var(--bd);border-radius:18px;padding:4px 18px;margin:12px 0}}
details.report summary{{cursor:pointer;font-size:13px;font-weight:700;color:var(--mut);padding:13px 0;letter-spacing:.5px;text-transform:uppercase}}
.report h2{{font-size:16px;margin:16px 0 8px;color:#cdd3e0}}.report h3{{font-size:14px;color:#aeb6c6}}
.report strong{{color:#fff}}.report code{{background:#0c1220;padding:1px 6px;border-radius:5px;font-size:13px}}
.report hr{{border:0;border-top:1px solid var(--bd);margin:14px 0}}.report p{{margin:7px 0;color:#c2c9d6}}
.tw{{overflow-x:auto}}table{{border-collapse:collapse;width:100%;font-size:13px}}
th,td{{border:1px solid var(--bd);padding:5px 8px;text-align:left}}th{{background:#0c1220;color:var(--mut)}}
.arch{{display:flex;flex-wrap:wrap;gap:7px}}.arch a{{color:var(--eth);text-decoration:none;font-size:12px;
background:var(--card2);border:1px solid var(--bd);padding:3px 9px;border-radius:8px}}
.disc{{color:var(--mut);font-size:11px;text-align:center;margin-top:22px}}
@media(max-width:540px){{.hero,.lvgrid{{grid-template-columns:1fr}}.macro{{grid-template-columns:repeat(3,1fr)}}
.coin .pr{{font-size:30px}}.tldr li{{font-size:15px}}}}
</style></head>
<body><div class="wrap">
<header><h1>📈 BTC / ETH · {date}</h1><span class="upd">⟳ {updated}</span></header>

<div class="hero">
  <div class="coin btc"><div class="glow"></div><div class="nm">BITCOIN</div>
    <div class="pr">{btc_price}</div>{btc_chg}</div>
  <div class="coin eth"><div class="glow"></div><div class="nm">ETHEREUM</div>
    <div class="pr">{eth_price}</div>{eth_chg}</div>
</div>

{mood_html}

{levels}

<div class="sect"><h2>🎯 Главные ставки дня</h2>{top_bets}{rest}</div>

<div class="sect"><h2>Макро · на закрытии</h2><div class="macro">{macro}</div></div>

<div class="sect"><h2>Насколько точны прогнозы</h2>
  <div class="brier"><div class="bignum" style="color:{bcol}">{brier_score}</div>
  <div class="track"><div class="mk"></div><div class="dot" style="left:{bpos}%;background:{bcol}"></div></div>
  <div class="bcom">{brier_count} закрытых · 0 — идеально, 0.25 — как монетка. Чем ниже, тем лучше.</div></div></div>

<details class="report"><summary>📄 Полный отчёт — развернуть</summary>{report_html}</details>

<div class="sect"><h2>Архив</h2><div class="arch">{archive}</div></div>

<div class="tldr"><h2>⚡ Короче говоря</h2><ul>{tldr}</ul></div>

<div class="disc">Я не финансовый советник · это эксперимент с прогнозами, а не торговый сигнал</div>
</div></body></html>"""


def main():
    os.makedirs(os.path.join(ROOT, "site"), exist_ok=True)
    out = os.path.join(ROOT, "site", "index.html")
    open(out, "w", encoding="utf-8").write(build())
    print(f"site/index.html готов ({os.path.getsize(out)} байт)")


if __name__ == "__main__":
    main()
