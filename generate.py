#!/usr/bin/env python3
"""Amazon.co.jp filament sale pseudo-feed generator.

Scrapes Amazon.co.jp search pages (PLA/PETG/TPU/TPE filament), extracts
items on sale (price below the displayed reference price, or with a
coupon), and writes an RSS 2.0 feed (feed.xml) next to this script.
State (first-seen dates per ASIN) lives outside the repo:
~/.local/state/amz-filament-feed/state.json
"""
import re, subprocess, json, html, os, sys, datetime
from urllib.parse import quote
from xml.sax.saxutils import escape
from email.utils import formatdate

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
KEYWORDS = {
    "PLA": "PLA フィラメント 1kg",
    "PETG": "PETG フィラメント 1kg",
    "TPU": "TPU フィラメント",
    "TPE": "TPE フィラメント",
}
HERE = os.path.dirname(os.path.abspath(__file__))
FEED_PATH = os.path.join(HERE, "feed.xml")
STATE_DIR = os.path.expanduser("~/.local/state/amz-filament-feed")
STATE_PATH = os.path.join(STATE_DIR, "state.json")

def fetch(url):
    r = subprocess.run(["curl", "-s", "-L", "--max-time", "40", "-A", UA,
                        "-H", "Accept-Language: ja-JP,ja;q=0.9", url],
                       capture_output=True, text=True)
    return r.stdout

def yen(s):
    return int(s.replace(",", "")) if s else None

def parse(page):
    segs = page.split('data-component-type="s-search-result"')
    items, seen = [], set()
    for seg in segs[1:]:
        am = re.search(r'data-asin="([A-Z0-9]{10})"', seg)
        if not am:
            continue
        asin = am.group(1)
        if asin in seen:
            continue
        seen.add(asin)
        title = None
        tm = re.search(r'<h2[^>]*aria-label="([^"]{8,250})"', seg)
        if tm:
            title = tm.group(1)
        if not title:
            tm = re.search(r'<h2[^>]*>\s*<a[^>]*>(?:\s*<span[^>]*>)?([^<]{8,250})', seg)
            if tm:
                title = tm.group(1)
        if title:
            title = re.sub(r'^スポンサー広告 - ', '', html.unescape(title)).strip()
        pm = re.search(r'<span class="a-price"[^>]*>\s*<span class="a-offscreen">￥([\d,]+)</span>', seg)
        price = yen(pm.group(1)) if pm else None
        if price is None:
            continue
        lm = re.search(r'過去: ￥([\d,]+)', seg)
        listp = yen(lm.group(1)) if lm else None
        pct = round((1 - price / listp) * 100) if (listp and listp > price) else None
        items.append({"asin": asin, "title": title or "(title不明)",
                      "price": price, "list": listp, "pct": pct,
                      "coupon": "クーポン" in seg,
                      "url": f"https://www.amazon.co.jp/dp/{asin}"})
    return items

def load_state():
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def save_state(state):
    os.makedirs(STATE_DIR, exist_ok=True)
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1)
    os.replace(tmp, STATE_PATH)

def main():
    now = datetime.datetime.now(datetime.timezone.utc)
    state = load_state()
    picked = {}
    for mat, kw in KEYWORDS.items():
        page = fetch("https://www.amazon.co.jp/s?k=" + quote(kw))
        if not page:
            print(f"WARN: empty page for {mat}", file=sys.stderr)
            continue
        for it in parse(page):
            if mat not in it["title"].upper():
                continue
            if not ((it["pct"] and it["pct"] >= 5) or it["coupon"]):
                continue
            it["mat"] = mat
            prev = picked.get(it["asin"])
            if prev is None or (it["pct"] or 0) > (prev["pct"] or 0):
                picked[it["asin"]] = it
    items = sorted(picked.values(),
                   key=lambda x: (-(x["pct"] or 0), x["price"]))[:30]
    if not items:
        print("no sale items found; feed left unchanged", file=sys.stderr)
        return 1
    for it in items:
        ent = state.get(it["asin"])
        if ent is None:
            state[it["asin"]] = {"first_seen": now.isoformat()}
            it["first_seen"] = now
        else:
            it["first_seen"] = datetime.datetime.fromisoformat(ent["first_seen"])
        state[it["asin"]]["last_seen"] = now.isoformat()
    cutoff = now - datetime.timedelta(days=30)
    for asin in list(state):
        ls = state[asin].get("last_seen") or state[asin].get("first_seen")
        if ls and datetime.datetime.fromisoformat(ls) < cutoff:
            del state[asin]
    save_state(state)

    out = []
    out.append('<?xml version="1.0" encoding="UTF-8"?>')
    out.append('<rss version="2.0"><channel>')
    out.append('<title>Amazon フィラメント セール（疑似フィード）</title>')
    out.append('<link>https://www.amazon.co.jp/</link>')
    out.append('<description>Amazon.co.jp の PLA / PETG / TPU / TPE フィラメントのセール品（過去価格より5%以上安いもの、またはクーポン付き）を mcp-server 上のスクリプトが自動収集した疑似フィード。</description>')
    out.append(f'<lastBuildDate>{formatdate(now.timestamp())}</lastBuildDate>')
    for it in items:
        bits = [f'￥{it["price"]:,}']
        if it["pct"]:
            bits.append(f'-{it["pct"]}%（過去 ￥{it["list"]:,}）')
        if it["coupon"]:
            bits.append('クーポンあり')
        title = f'【{it["mat"]}】{it["title"]} — ' + ' '.join(bits)
        desc = (f'素材: {it["mat"]} / 価格: ￥{it["price"]:,}'
                + (f' / 過去価格: ￥{it["list"]:,} / 割引率: {it["pct"]}%' if it["pct"] else '')
                + (' / クーポンあり' if it["coupon"] else ''))
        out.append('<item>')
        out.append(f'<title>{escape(title)}</title>')
        out.append(f'<link>{escape(it["url"])}</link>')
        out.append(f'<guid isPermaLink="false">{it["asin"]}</guid>')
        out.append(f'<pubDate>{formatdate(it["first_seen"].timestamp())}</pubDate>')
        out.append(f'<description>{escape(desc)}</description>')
        out.append('</item>')
    out.append('</channel></rss>')
    tmp = FEED_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")
    os.replace(tmp, FEED_PATH)
    print(f"feed written: {len(items)} items")
    return 0

if __name__ == "__main__":
    sys.exit(main())
