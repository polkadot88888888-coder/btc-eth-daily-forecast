#!/usr/bin/env python3
"""Футуристичный дашборд BTC/ETH: непрозрачные карточки, большие графики цены с уровнями,
кольца вероятности, кривая точности (Брайер), опционный поток, обратный отсчёт, выжимка.

Данные: последний reports/*.md (+ <!--CARD {json} CARD-->), journal.md, data/macro.json,
живые цены и свечи Deribit. Без внешних зависимостей (моно-шрифт — Google Fonts).
GitHub Action пересобирает после каждого коммита — ноутбук не нужен.
"""
import glob
import html
import json
import math
import os
import re
import time
import urllib.request
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = "https://github.com/polkadot88888888-coder/btc-eth-daily-forecast"
DBT = "https://www.deribit.com/api/v2/public/"


# ---------- данные ----------

def latest_report():
    files = glob.glob(os.path.join(ROOT, "reports", "*.md"))
    if not files:
        return None, ""
    def k(p):
        m = re.match(r"(\d{4}-\d{2}-\d{2})", os.path.basename(p))
        return (m.group(1) if m else "0", os.path.basename(p))
    best = max(files, key=k)
    return os.path.basename(best), open(best, encoding="utf-8").read()


def all_dates():
    return sorted({os.path.basename(p)[:-3]
                   for p in glob.glob(os.path.join(ROOT, "reports", "*.md"))},
                  reverse=True)


def extract_card(md):
    m = re.search(r"<!--\s*CARD\s*(\{.*?\})\s*CARD\s*-->", md, re.S)
    if not m:
        return {}, md
    body = md[:m.start()] + md[m.end():]
    try:
        return json.loads(m.group(1)), body
    except Exception:
        return {}, body


def lvl_num(s):
    s = str(s).lower().replace("$", "").replace(" ", "").replace(",", "")
    mult = 1
    if s.endswith("k") or s.endswith("к"):
        mult, s = 1000, s[:-1]
    try:
        return float(s) * mult
    except ValueError:
        return None


def parse_levels(md):
    def grab(coin):
        m = re.search(rf"Уровни\s+{coin}.*?поддержк\w*[:\s]*([^\n]*?)"
                      rf"сопротивлен\w*[:\s]*([^\n]*)", md, re.I)
        if not m:
            return [], []
        s = [x.strip(" .") for x in m.group(1).split("/") if x.strip()][:3]
        r = [x.strip(" .") for x in m.group(2).split("/") if x.strip()][:3]
        return s, r
    bs, br = grab("BTC"); es, er = grab("ETH")
    return {"btc_sup": bs, "btc_res": br, "eth_sup": es, "eth_res": er}


def parse_journal():
    path = os.path.join(ROOT, "journal.md")
    brier = {"count": "—", "score": "—"}
    opens, closed = [], []
    if not os.path.exists(path):
        return brier, opens, closed
    text = open(path, encoding="utf-8").read()
    m = re.search(r"Сводный Брайер-скор.*?\n\|.*?\|\n\|[-| ]+\|\n\|(.+?)\|\n",
                  text, re.S)
    if m:
        c = [x.strip() for x in m.group(1).split("|")]
        if len(c) >= 2:
            brier = {"count": c[0], "score": c[1]}
    seg = re.search(r"## Открытые прогнозы(.+?)\n---", text, re.S)
    if seg:
        for line in seg.group(1).splitlines():
            if not line.strip().startswith("|"):
                continue
            c = [x.strip() for x in line.strip().strip("|").split("|")]
            if len(c) < 5 or c[0] in ("№", "") or set(c[0]) <= {"-"}:
                continue
            try:
                opens.append({"n": c[0], "text": c[1], "deadline": c[3],
                              "p": float(c[4])})
            except ValueError:
                pass
    seg = re.search(r"## Закрытые прогнозы(.+)", text, re.S)
    if seg:
        for line in seg.group(1).splitlines():
            if not line.strip().startswith("|"):
                continue
            c = [x.strip() for x in line.strip().strip("|").split("|")]
            if len(c) < 7 or c[0] in ("№", "") or set(c[0]) <= {"-"}:
                continue
            if c[5] in ("1", "0"):
                try:
                    closed.append((int(c[5]), float(c[6])))
                except ValueError:
                    closed.append((int(c[5]), None))
    return brier, opens, closed


def load_macro():
    p = os.path.join(ROOT, "data", "macro.json")
    return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else {}


def live_prices():
    out = {}
    for sym, k in (("BTC-PERPETUAL", "BTC"), ("ETH-PERPETUAL", "ETH")):
        try:
            req = urllib.request.Request(DBT + "ticker?instrument_name=" + sym,
                                         headers={"User-Agent": "site/1.0"})
            r = json.load(urllib.request.urlopen(req, timeout=20))["result"]
            out[k] = {"price": r.get("index_price") or r.get("mark_price"),
                      "chg": r.get("price_change")}
        except Exception:
            out[k] = None
    return out


def candles(sym, hours=168):
    try:
        now = int(time.time() * 1000)
        start = now - hours * 3600 * 1000
        url = (DBT + f"get_tradingview_chart_data?instrument_name={sym}-PERPETUAL"
               f"&resolution=60&start_timestamp={start}&end_timestamp={now}")
        req = urllib.request.Request(url, headers={"User-Agent": "site/1.0"})
        r = json.load(urllib.request.urlopen(req, timeout=25))["result"]
        return [c for c in r.get("close", []) if c]
    except Exception:
        return []


# ---------- мини-markdown ----------

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
                rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")]); i += 1
            th = "".join(f"<th>{md_inline(c)}</th>" for c in head)
            trs = "".join("<tr>" + "".join(f"<td>{md_inline(c)}</td>" for c in r) + "</tr>" for r in rows)
            out.append(f'<div class="tw"><table><thead><tr>{th}</tr></thead><tbody>{trs}</tbody></table></div>')
            continue
        if re.match(r"^\s*[-*]\s+", ln):
            items = []
            while i < len(lines) and re.match(r"^\s*[-*]\s+", lines[i]):
                items.append("<li>" + md_inline(re.sub(r"^\s*[-*]\s+", "", lines[i])) + "</li>"); i += 1
            out.append("<ul>" + "".join(items) + "</ul>"); continue
        buf = [ln]; i += 1
        while i < len(lines) and lines[i].strip() and \
                not re.match(r"^(#{1,6}\s|\s*[-*]\s|\s*\||\s*---\s*$)", lines[i]):
            buf.append(lines[i]); i += 1
        out.append("<p>" + "<br>".join(md_inline(b) for b in buf) + "</p>")
    return "\n".join(out)


# ---------- визуальные компоненты ----------

def fmt_price(v):
    if v is None:
        return "—"
    return f"${v:,.0f}" if v >= 100 else f"${v:,.2f}"


def price_chart(sym, cur, vals, sup, res, up):
    """Большой график: линия+заливка, min/max, последняя цена, линии уровней."""
    if len(vals) < 3:
        return f'<div class="chartbox"><div class="cl">{sym}</div>' \
               '<div class="muted">нет данных</div></div>'
    W, H, pad = 100.0, 46.0, 5.0
    lo, hi = min(vals), max(vals)
    rng = (hi - lo) or 1
    n = len(vals)
    y = lambda v: pad + (H - 2 * pad) * (1 - (v - lo) / rng)
    x = lambda i: i / (n - 1) * W
    line = " ".join(f"{x(i):.2f},{y(v):.2f}" for i, v in enumerate(vals))
    area = f"0,{H} {line} {W},{H}"
    col = "var(--up)" if up else "var(--down)"
    gid = f"cg{sym}"
    last = vals[-1]
    # линии уровней в диапазоне графика
    lvls = ""
    for v, cls in ([(lvl_num(s), "sup") for s in sup] + [(lvl_num(r), "res") for r in res]):
        if v and lo <= v <= hi:
            yy = y(v)
            c2 = "var(--up)" if cls == "sup" else "var(--down)"
            lvls += (f'<line x1="0" y1="{yy:.1f}" x2="100" y2="{yy:.1f}" stroke="{c2}" '
                     f'stroke-width=".4" stroke-dasharray="2 2" opacity=".55"/>')
    dot_y = y(last)
    return (
        f'<div class="chartbox"><div class="chdr"><span class="cl {sym.lower()}">{sym}</span>'
        f'<span class="cmm">нед.: {fmt_price(hi)} / {fmt_price(lo)}</span></div>'
        f'<svg class="bigspark" viewBox="0 0 100 46" preserveAspectRatio="none">'
        f'<defs><linearGradient id="{gid}" x1="0" y1="0" x2="0" y2="1">'
        f'<stop offset="0" stop-color="{col}" stop-opacity=".30"/>'
        f'<stop offset="1" stop-color="{col}" stop-opacity="0"/></linearGradient></defs>'
        f'{lvls}<polygon points="{area}" fill="url(#{gid})"/>'
        f'<polyline points="{line}" fill="none" stroke="{col}" stroke-width="1.4" '
        f'vector-effect="non-scaling-stroke" stroke-linejoin="round"/>'
        f'<circle cx="100" cy="{dot_y:.1f}" r="1.6" fill="{col}"/></svg></div>')


def brier_curve(closed):
    vals = [b for _, b in closed if b is not None]
    if len(vals) < 2:
        return '<span class="muted">мало данных для кривой</span>'
    run, s = [], 0.0
    for i, b in enumerate(vals):
        s += b
        run.append(s / (i + 1))
    lo, hi = min(min(run), 0.25), max(max(run), 0.25)
    rng = (hi - lo) or 1
    n = len(run)
    y = lambda v: 4 + 30 * (v - lo) / rng          # меньше = выше (лучше)
    x = lambda i: i / (n - 1) * 100
    line = " ".join(f"{x(i):.2f},{y(v):.2f}" for i, v in enumerate(run))
    base = y(0.25)
    end = run[-1]
    col = "var(--up)" if end < 0.25 else "var(--down)" if end > 0.30 else "#fbbf5a"
    return (f'<svg class="curve" viewBox="0 0 100 38" preserveAspectRatio="none">'
            f'<line x1="0" y1="{base:.1f}" x2="100" y2="{base:.1f}" stroke="var(--mut)" '
            f'stroke-width=".4" stroke-dasharray="2 2"/>'
            f'<polyline points="{line}" fill="none" stroke="{col}" stroke-width="1.6" '
            f'vector-effect="non-scaling-stroke" stroke-linejoin="round"/></svg>'
            f'<div class="muted" style="font-size:11px;margin-top:3px">линия вниз = модель умнеет; '
            f'пунктир — уровень «монетки» 0.25</div>')


def ring_svg(pct, col):
    r = 26; c = 2 * math.pi * r
    off = c * (1 - pct / 100)
    return (f'<svg class="ring" viewBox="0 0 64 64"><circle cx="32" cy="32" r="{r}" '
            f'stroke="#ffffff16" stroke-width="7" fill="none"/>'
            f'<circle class="rp" cx="32" cy="32" r="{r}" stroke="{col}" stroke-width="7" '
            f'fill="none" stroke-linecap="round" stroke-dasharray="{c:.1f}" '
            f'stroke-dashoffset="{off:.1f}" transform="rotate(-90 32 32)"/>'
            f'<text x="32" y="37" text-anchor="middle" class="rt">{pct}%</text></svg>')


def prob_color(p):
    return "#2dd4a7" if p >= 0.6 else "#fbbf5a" if p >= 0.4 else "#56b3f5"


def chips(vals, kind):
    cls = "sup" if kind == "sup" else "res"
    return "".join(f'<span class="chip {cls} mono">{md_inline(str(v))}</span>'
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
                f'<div class="tv mono">{valtxt}</div><div class="ts {cls}">{sub}</div></div>')
    return out


def history_strip(closed):
    if not closed:
        return '<span class="muted">пока нет закрытых прогнозов</span>'
    outs = [o for o, _ in closed]
    hits = sum(outs)
    acc = round(hits / len(outs) * 100)
    dots = "".join(f'<i class="dot {"hit" if o else "miss"}"></i>' for o in outs[-44:])
    return (f'<div class="histacc"><b class="mono">{acc}%</b> попаданий · {hits}/{len(outs)}</div>'
            f'<div class="dots">{dots}</div>')


def build():
    name, md = latest_report()
    date = (name or "").replace(".md", "")
    card, body = extract_card(md)
    lv = parse_levels(body)
    for k in ("btc_sup", "btc_res", "eth_sup", "eth_res"):
        if card.get(k):
            lv[k] = card[k]
    brier, opens, closed = parse_journal()
    macro = load_macro()
    prices = live_prices()
    btc, eth = prices.get("BTC") or {}, prices.get("ETH") or {}
    updated = datetime.now(timezone.utc).astimezone().strftime("%d.%m %H:%M")
    btc_up = (btc.get("chg") or 0) >= 0
    eth_up = (eth.get("chg") or 0) >= 0

    charts = (price_chart("BTC", "BTC", candles("BTC"), lv["btc_sup"], lv["btc_res"], btc_up) +
              price_chart("ETH", "ETH", candles("ETH"), lv["eth_sup"], lv["eth_res"], eth_up))

    def chg(c, up):
        if c is None:
            return ""
        return (f'<span class="chg {"up" if up else "down"}">'
                f'{"▲" if up else "▼"} {abs(c):.2f}%</span>')

    dir_ = card.get("dir", "flat")
    mood_cls = {"up": "mup", "down": "mdn"}.get(dir_, "mfl")
    mood_ico = {"up": "🚀", "down": "🩸", "flat": "➖"}.get(dir_, "➖")
    mood_html = ""
    if card.get("mood") or card.get("headline"):
        mood_html = (f'<div class="mood {mood_cls} fade" style="--d:.05s"><div class="mi">{mood_ico}</div>'
                     f'<div><div class="ml">{md_inline(card.get("mood",""))}</div>'
                     f'<div class="mh">{md_inline(card.get("headline",""))}</div></div></div>')
    flow_html = ""
    if card.get("flow"):
        flow_html = (f'<div class="flow fade" style="--d:.1s"><span class="fi">🧠</span>'
                     f'<div><div class="fk">Поток умных денег</div>'
                     f'<div class="fv">{md_inline(card["flow"])}</div></div></div>')

    ranked = sorted(opens, key=lambda o: abs(o["p"] - 0.5), reverse=True)
    def bet(o):
        pct = int(round(o["p"] * 100))
        return (f'<div class="bet">{ring_svg(pct, prob_color(o["p"]))}'
                f'<div class="bi"><div class="bt">{md_inline(o["text"])}</div>'
                f'<div class="bm">до {html.escape(o["deadline"])}</div></div></div>')
    top = "".join(bet(o) for o in ranked[:3])
    rest = "".join(f'<div class="betr"><span class="mono" style="color:{prob_color(o["p"])}">'
                   f'{int(round(o["p"]*100))}%</span> {md_inline(o["text"])} '
                   f'<span class="muted">· до {html.escape(o["deadline"])}</span></div>'
                   for o in ranked[3:])
    rest_block = (f'<details class="more"><summary>Ещё ставки · {len(ranked)-3}</summary>'
                  f'{rest}</details>') if len(ranked) > 3 else ""

    try:
        sv = float(brier["score"])
        bcol = "#2dd4a7" if sv < 0.25 else "#fbbf5a" if sv < 0.3 else "#fb7185"
    except (ValueError, TypeError):
        bcol = "#56b3f5"

    lvl_html = ""
    if any(lv.values()):
        lvl_html = (
            '<div class="card fade" style="--d:.15s"><div class="h">Ключевые уровни</div>'
            '<div class="lvgrid">'
            f'<div><div class="lvc btc">BTC</div>'
            f'<div class="lvrow"><span class="lk">держит</span>{chips(lv["btc_sup"],"sup")}</div>'
            f'<div class="lvrow"><span class="lk">мешает</span>{chips(lv["btc_res"],"res")}</div></div>'
            f'<div><div class="lvc eth">ETH</div>'
            f'<div class="lvrow"><span class="lk">держит</span>{chips(lv["eth_sup"],"sup")}</div>'
            f'<div class="lvrow"><span class="lk">мешает</span>{chips(lv["eth_res"],"res")}</div></div>'
            '</div></div>')

    tldr = card.get("tldr") or []
    if not tldr:
        tldr = [f"💰 BTC {fmt_price(btc.get('price'))}, ETH {fmt_price(eth.get('price'))}.",
                f"🎯 Точность (Брайер): {brier['score']} за {brier['count']} шт."]
    tldr_html = "".join(f"<li>{md_inline(t)}</li>" for t in tldr if str(t).strip())

    report_html = md_to_html(body or "Отчёта пока нет.")
    archive = "".join(f'<a href="{REPO}/blob/main/reports/{html.escape(d)}.md" '
                      f'target="_blank" rel="noopener">{html.escape(d)}</a>'
                      for d in all_dates()[:14])

    return PAGE(
        date=html.escape(date), updated=html.escape(updated),
        btc_price=fmt_price(btc.get("price")), btc_chg=chg(btc.get("chg"), btc_up),
        eth_price=fmt_price(eth.get("price")), eth_chg=chg(eth.get("chg"), eth_up),
        charts=charts, mood=mood_html, flow=flow_html, levels=lvl_html,
        top=top or '<p class="muted">Открытых ставок нет.</p>', rest=rest_block,
        macro=macro_tiles(macro), brier=html.escape(str(brier["score"])), bcol=bcol,
        brier_count=html.escape(str(brier["count"])), history=history_strip(closed),
        curve=brier_curve(closed), tldr=tldr_html, report=report_html, archive=archive)


CSS = """
:root{--bg:#070a16;--card:#121a2e;--card2:#172038;--ink:#eef1ff;--mut:#99a1c4;--line:#273253;
--btc:#f7a23b;--eth:#a78bfa;--up:#2dd4a7;--down:#fb7185;--cy:#38bdf8;
--mono:"JetBrains Mono",ui-monospace,SFMono-Regular,Menlo,monospace}
*{box-sizing:border-box}
body{margin:0;color:var(--ink);font:16px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
-webkit-font-smoothing:antialiased;background:var(--bg);overflow-x:hidden;padding:0 15px 70px}
.mono{font-family:var(--mono);font-feature-settings:"tnum"}
.bgfx{position:fixed;inset:0;z-index:-1;overflow:hidden}
.bgfx b{position:absolute;border-radius:50%;filter:blur(90px);opacity:.28;animation:drift 24s ease-in-out infinite}
.bgfx .a{width:420px;height:420px;background:#4f46e5;left:-140px;top:-140px}
.bgfx .b{width:360px;height:360px;background:#0ea5a3;right:-140px;top:80px;animation-delay:-8s}
.bgfx .c{width:340px;height:340px;background:#7c3aed;left:35%;bottom:-180px;animation-delay:-15s}
@keyframes drift{0%,100%{transform:translate(0,0)}50%{transform:translate(36px,26px)}}
.wrap{max-width:780px;margin:0 auto}
header{display:flex;justify-content:space-between;align-items:baseline;gap:6px;flex-wrap:wrap;padding:24px 2px 6px}
h1{font-size:18px;margin:0;font-weight:800}
.upd{color:var(--mut);font-size:12px;font-family:var(--mono)}
.fade{opacity:0;transform:translateY(10px);animation:up .55s cubic-bezier(.2,.8,.2,1) forwards;animation-delay:var(--d,0s)}
@keyframes up{to{opacity:1;transform:none}}
.card,.coin,.mood,.flow,.tldr,details.report{background:var(--card);border:1px solid var(--line);border-radius:20px}
.hero{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin:12px 0}
.coin{position:relative;overflow:hidden;padding:16px 18px}
.coin .nm{font-weight:800;font-size:12px;letter-spacing:1.6px}
.coin.btc .nm{color:var(--btc)}.coin.eth .nm{color:var(--eth)}
.coin .pr{font-size:30px;font-weight:800;margin-top:3px;letter-spacing:-.5px;font-family:var(--mono)}
.coin .glow{position:absolute;right:-30px;top:-30px;width:110px;height:110px;border-radius:50%;filter:blur(46px);opacity:.3;animation:pulse 4s ease-in-out infinite}
.coin.btc .glow{background:var(--btc)}.coin.eth .glow{background:var(--eth)}
@keyframes pulse{0%,100%{opacity:.22}50%{opacity:.4}}
.chg{font-size:13px;font-weight:700;font-family:var(--mono)}.chg.up{color:var(--up)}.chg.down{color:var(--down)}
.cd{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin:12px 0}
.cdbox{background:var(--card2);border:1px solid var(--line);border-radius:14px;padding:11px 13px;text-align:center}
.cdk{font-size:10px;letter-spacing:.6px;color:var(--mut);text-transform:uppercase}
.cdv{font-size:19px;font-weight:800;font-family:var(--mono);margin-top:3px}
.card{padding:16px 18px;margin:12px 0}
.h{font-size:11.5px;letter-spacing:1.3px;text-transform:uppercase;color:var(--mut);margin-bottom:12px}
.chartbox{margin:6px 0 2px}.chartbox+.chartbox{margin-top:16px;padding-top:14px;border-top:1px solid var(--line)}
.chdr{display:flex;justify-content:space-between;align-items:baseline}
.cl{font-weight:800;font-size:13px;letter-spacing:1px}.cl.btc{color:var(--btc)}.cl.eth{color:var(--eth)}
.cmm{font-size:11px;color:var(--mut);font-family:var(--mono)}
.bigspark{width:100%;height:92px;display:block;margin-top:6px}
.mood{display:flex;gap:14px;align-items:center;padding:15px 17px;margin:12px 0}
.mood .mi{font-size:28px}.mood .ml{font-weight:800;font-size:17px}.mood .mh{font-size:13.5px;opacity:.92;margin-top:2px}
.mup{border-color:#1f5a45}.mdn{border-color:#5a2a3b}.mfl{border-color:#39405a}
.flow{display:flex;gap:13px;align-items:center;padding:13px 16px;margin:12px 0;border-color:#2b3a63}
.flow .fi{font-size:22px}.flow .fk{font-size:11px;letter-spacing:.8px;color:var(--cy);text-transform:uppercase}
.flow .fv{font-weight:600;font-size:14.5px;margin-top:2px}
.lvgrid{display:grid;grid-template-columns:1fr 1fr;gap:16px}
.lvc{font-weight:800;font-size:13px;letter-spacing:1px;margin-bottom:6px}
.lvc.btc{color:var(--btc)}.lvc.eth{color:var(--eth)}
.lvrow{display:flex;flex-wrap:wrap;gap:6px;align-items:center;margin:5px 0}
.lk{font-size:11px;color:var(--mut);width:46px}
.chip{font-size:13px;font-weight:700;padding:3px 10px;border-radius:9px}
.chip.sup{background:#0f2a21;color:#7ff0cb;border:1px solid #1f5a45}
.chip.res{background:#2a1317;color:#ffb0b8;border:1px solid #5a2a35}
.bet{display:flex;gap:14px;align-items:center;padding:12px 2px;border-bottom:1px solid var(--line)}
.bet:last-child{border:0}
.ring{width:54px;height:54px;flex:none}.ring .rp{animation:dash 1s ease forwards}
@keyframes dash{from{stroke-dashoffset:163.4}}
.ring .rt{fill:var(--ink);font-size:16px;font-weight:800;font-family:var(--mono)}
.bt{font-weight:650;font-size:15px}.bm{color:var(--mut);font-size:12px;margin-top:3px}
.more summary{cursor:pointer;color:var(--eth);font-size:13px;font-weight:600;padding:9px 0 3px}
.betr{font-size:14px;padding:6px 0;border-bottom:1px solid var(--line)}
.macro{display:grid;grid-template-columns:repeat(5,1fr);gap:9px}
.tile{background:var(--card2);border:1px solid var(--line);border-radius:13px;padding:9px 6px;text-align:center}
.tl{color:var(--mut);font-size:10px}.tv{font-size:16px;font-weight:800;margin:2px 0}
.ts{font-size:11px;font-weight:700;font-family:var(--mono)}.ts.up{color:var(--up)}.ts.down{color:var(--down)}
.briw{display:flex;align-items:center;gap:16px;flex-wrap:wrap}
.bignum{font-size:38px;font-weight:800;line-height:1;font-family:var(--mono)}
.histacc{font-size:13px;color:var(--mut)}.histacc b{color:var(--ink);font-size:17px}
.dots{display:flex;flex-wrap:wrap;gap:4px;margin-top:8px}
.dot{width:11px;height:11px;border-radius:3px;display:inline-block}
.dot.hit{background:var(--up);box-shadow:0 0 6px #2dd4a780}.dot.miss{background:var(--down)}
.curve{width:100%;height:60px;display:block;margin-top:8px}
.tldr{padding:18px 20px;margin:16px 0;border-color:#3a2f6b;background:#141633}
.tldr .h{color:#c3b6ff}.tldr ul{margin:0;padding:0;list-style:none}
.tldr li{font-size:16px;font-weight:600;padding:9px 0;border-bottom:1px solid #ffffff12}
.tldr li:last-child{border:0}
details.report{padding:4px 18px;margin:12px 0}
details.report>summary{cursor:pointer;font-size:12px;font-weight:700;color:var(--mut);padding:13px 0;letter-spacing:.6px;text-transform:uppercase;list-style:none}
details.report>summary::-webkit-details-marker{display:none}
.report h2{font-size:16px;margin:16px 0 8px;color:#d7ddf4}.report h3{font-size:14px;color:#aeb6d6}
.report strong{color:#fff}.report code{background:#ffffff14;padding:1px 6px;border-radius:5px;font-size:13px}
.report hr{border:0;border-top:1px solid var(--line);margin:14px 0}.report p{margin:7px 0;color:#c4cbe4}
.tw{overflow-x:auto}table{border-collapse:collapse;width:100%;font-size:13px}
th,td{border:1px solid var(--line);padding:5px 8px;text-align:left}th{background:#ffffff0a;color:var(--mut)}
.arch{display:flex;flex-wrap:wrap;gap:7px}.arch a{color:var(--eth);text-decoration:none;font-size:12px;
background:var(--card2);border:1px solid var(--line);padding:3px 9px;border-radius:9px;font-family:var(--mono)}
.muted{color:var(--mut)}
.disc{color:var(--mut);font-size:11px;text-align:center;margin-top:22px}
@media(max-width:540px){.hero,.lvgrid{grid-template-columns:1fr}.macro{grid-template-columns:repeat(3,1fr)}
.coin .pr{font-size:27px}.tldr li{font-size:15px}}
@media(prefers-reduced-motion:reduce){.fade,.bgfx b,.coin .glow,.ring .rp{animation:none;opacity:1;transform:none}}
"""

JS = """
function pad(n){return String(n).padStart(2,'0')}
function nextForecast(){var n=new Date();var t=new Date(Date.UTC(n.getUTCFullYear(),n.getUTCMonth(),n.getUTCDate(),12,8,0));
if(t<=n)t=new Date(t.getTime()+864e5);return t}
function nextExpiry(){var n=new Date();var t=new Date(Date.UTC(n.getUTCFullYear(),n.getUTCMonth(),n.getUTCDate(),8,0,0));
while(t<=n||t.getUTCDay()!==5){t=new Date(t.getTime()+864e5);t=new Date(Date.UTC(t.getUTCFullYear(),t.getUTCMonth(),t.getUTCDate(),8,0,0))}return t}
function fmt(ms){if(ms<0)ms=0;var s=Math.floor(ms/1000);var d=Math.floor(s/86400);s-=d*86400;
var h=Math.floor(s/3600);s-=h*3600;var m=Math.floor(s/60);s-=m*60;
return (d>0?d+'д ':'')+pad(h)+':'+pad(m)+':'+pad(s)}
function tick(){var now=Date.now();
var f=document.getElementById('cdF'),e=document.getElementById('cdE');
if(f)f.textContent=fmt(nextForecast().getTime()-now);
if(e)e.textContent=fmt(nextExpiry().getTime()-now)}
setInterval(tick,1000);tick();
"""


def PAGE(**v):
    return f"""<!doctype html>
<html lang="ru"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex"><title>BTC/ETH · {v['date']}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@500;700;800&display=swap" rel="stylesheet">
<style>{CSS}</style></head>
<body><div class="bgfx"><b class="a"></b><b class="b"></b><b class="c"></b></div>
<div class="wrap">
<header><h1>📈 BTC / ETH · {v['date']}</h1><span class="upd">⟳ {v['updated']}</span></header>

<div class="hero">
  <div class="coin btc fade" style="--d:0s"><div class="glow"></div>
    <div class="nm">BITCOIN</div><div class="pr">{v['btc_price']}</div>{v['btc_chg']}</div>
  <div class="coin eth fade" style="--d:.06s"><div class="glow"></div>
    <div class="nm">ETHEREUM</div><div class="pr">{v['eth_price']}</div>{v['eth_chg']}</div>
</div>

<div class="cd fade" style="--d:.1s">
  <div class="cdbox"><div class="cdk">⏱ до прогноза</div><div class="cdv" id="cdF">—</div></div>
  <div class="cdbox"><div class="cdk">🗓 до экспирации</div><div class="cdv" id="cdE">—</div></div>
</div>

<div class="card fade" style="--d:.13s"><div class="h">График цены · неделя</div>{v['charts']}</div>

{v['mood']}
{v['flow']}
{v['levels']}

<div class="card fade" style="--d:.2s"><div class="h">🎯 Главные ставки дня</div>{v['top']}{v['rest']}</div>

<div class="card fade" style="--d:.25s"><div class="h">Макро · на закрытии</div><div class="macro">{v['macro']}</div></div>

<div class="card fade" style="--d:.3s"><div class="h">Точность прогнозов</div>
  <div class="briw"><div class="bignum" style="color:{v['bcol']}">{v['brier']}</div>
  <div style="flex:1;min-width:150px">{v['history']}</div></div>
  {v['curve']}
  <div class="muted" style="font-size:12px;margin-top:8px">Брайер {v['brier']} · {v['brier_count']} закрытых · 0 — идеально, 0.25 — как монетка.</div></div>

<details class="report"><summary>📄 Полный отчёт — развернуть</summary>{v['report']}</details>

<div class="card"><div class="h">Архив</div><div class="arch">{v['archive']}</div></div>

<div class="tldr"><div class="h">⚡ Короче говоря</div><ul>{v['tldr']}</ul></div>
<div class="disc">Я не финансовый советник · это эксперимент с прогнозами, а не торговый сигнал</div>
</div><script>{JS}</script></body></html>"""


def main():
    os.makedirs(os.path.join(ROOT, "site"), exist_ok=True)
    out = os.path.join(ROOT, "site", "index.html")
    open(out, "w", encoding="utf-8").write(build())
    print(f"site/index.html готов ({os.path.getsize(out)} байт)")


if __name__ == "__main__":
    main()
