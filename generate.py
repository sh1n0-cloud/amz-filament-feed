#!/usr/bin/env python3
"""Amazon.co.jp filament sale pseudo-feed generator.

Fetches search pages for PLA/PETG/TPU/TPE filament, extracts items that are
on sale (>=5% below the displayed reference price, or with a coupon), and
writes an RSS 2.0 feed (feed.xml) next to this script.

Review fixes applied 2026-11-16 (Codex sol review, H1/H2/H3/M4/L1):
- Result elements are located by their full start tag; the ASIN is taken
  from the start tag itself when present, so attribute order in the tag
  cannot shift ASIN/title pairing (H1).
- A fetch/parse failure for any material aborts the run without touching
  the previous feed; a genuinely empty result set publishes an empty
  feed instead (H2/M4).
- The discount threshold uses integer math, no pre-rounding (L1).
"""
import html as htmlmod
import json
import re
import subprocess
import sys
import time
from datetime import datetime
from email.utils import formatdate
from pathlib import Path
from xml.sax.saxutils import escape

REPO = Path(__file__).resolve().parent
FEED = REPO / "feed.xml"
STATE_DIR = Path.home() / ".local" / "state" / "amz-filament-feed"
STATE = STATE_DIR / "state.json"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

MATERIALS = {
    "PLA": "PLA フィラメント 1.75mm 1kg",
    "PETG": "PETG フィラメント 1.75mm 1kg",
    "TPU": "TPU フィラメント 1.75mm 1kg",
    "TPE": "TPE フィラメント 1.75mm",
}

RESULT_TAG = re.compile(r'<div[^>]*data-component-type="s-search-result"[^>]*>')
ASIN_RE = re.compile(r'data-asin="([A-Z0-9]{10})"')
PRICE_RE = re.compile(r'class="a-price"[^>]*>\s*<span class="a-offscreen">￥([\d,]+)')
LIST_RE = re.compile(r'過去:?\s*￥([\d,]+)')
TITLE_ARIA_RE = re.compile(r'<h2[^>]*aria-label="([^"]+)"')
TITLE_TEXT_RE = re.compile(r'<h2[^>]*>(?:<a[^>]*>)?\s*(?:<span[^>]*>)?([^<]{8,250})')


class FetchError(Exception):
    pass


def fetch(url):
    r = subprocess.run(
        ["curl", "-s", "-f", "-L", "--max-time", "40", "-A", UA,
         "-H", "Accept-Language: ja-JP,ja;q=0.9", url],
        capture_output=True, text=True)
    if r.returncode != 0:
        raise FetchError(f"curl rc={r.returncode}")
    if 'data-component-type="s-search-result"' not in r.stdout:
        raise FetchError("no search results in page (blocked or layout changed)")
    return r.stdout


def search_url(query):
    from urllib.parse import quote_plus
    return "https://www.amazon.co.jp/s?k=" + quote_plus(query)


def parse(page, material):
    tags = list(RESULT_TAG.finditer(page))
    items = []
    for i, m in enumerate(tags):
        seg = page[m.end():(tags[i + 1].start() if i + 1 < len(tags) else len(page))]
        cut = seg.find('id="navFooter"')
        if cut > 0:
            seg = seg[:cut]
        tag_asin = ASIN_RE.search(m.group(0))
        if tag_asin:
            asin = tag_asin.group(1)
        else:
            am = ASIN_RE.search(seg)
            if not am:
                continue
            asin = am.group(1)
        tm = TITLE_ARIA_RE.search(seg)
        if tm:
            title = htmlmod.unescape(tm.group(1))
        else:
            tm = TITLE_TEXT_RE.search(seg)
            title = htmlmod.unescape(tm.group(1)).strip() if tm else ""
        title = re.sub(r'^スポンサー広告\s*-\s*', '', title).strip()
        if material.upper() not in title.upper():
            continue
        pm = PRICE_RE.search(seg)
        if not pm:
            continue
        price = int(pm.group(1).replace(",", ""))
        lm = LIST_RE.search(seg)
        listp = int(lm.group(1).replace(",", "")) if lm else None
        coupon = "クーポン" in seg
        pct = 0
        if listp and listp > price:
            pct = round((1 - price / listp) * 100)
        if listp and price * 100 <= listp * 95:
            pass  # at least 5% off: include
        elif coupon:
            pass  # coupon: include
        else:
            continue
        items.append({
            "asin": asin, "title": title, "price": price,
            "list": listp, "pct": pct, "coupon": coupon, "material": material,
        })
    return items


def load_state():
    # Normalize entries to epoch ints. The first version of this script
    # stored ISO strings; unparseable entries are dropped (sol review M5).
    try:
        raw = json.loads(STATE.read_text())
    except Exception:
        return {}
    state = {}
    if not isinstance(raw, dict):
        return {}
    for asin, ent in raw.items():
        if not isinstance(ent, dict):
            continue
        norm = {}
        for key in ("first_seen", "last_seen"):
            v = ent.get(key)
            if isinstance(v, (int, float)):
                norm[key] = int(v)
            elif isinstance(v, str):
                try:
                    norm[key] = int(datetime.fromisoformat(v).timestamp())
                except ValueError:
                    pass
        if norm:
            state[asin] = norm
    return state


def save_state(state):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1))
    tmp.replace(STATE)


def build_feed(items, state, now):
    parts = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<rss version="2.0"><channel>',
             '<title>Amazon フィラメント セール（疑似フィード）</title>',
             '<link>https://www.amazon.co.jp/</link>',
             '<description>PLA/PETG/TPU/TPE フィラメントのセール品（割引5%以上またはクーポン付き）を mcp-server から定期取得した疑似フィード</description>',
             f'<lastBuildDate>{formatdate(now)}</lastBuildDate>']
    for it in items:
        bits = [f"素材: {it['material']}", f"価格: ￥{it['price']:,}"]
        if it["list"]:
            bits.append(f"過去価格: ￥{it['list']:,}（-{it['pct']}%）")
        if it["coupon"]:
            bits.append("クーポンあり")
        label = f"【{it['material']}】{it['title']} — ￥{it['price']:,}"
        if it["pct"]:
            label += f" -{it['pct']}%"
        if it["coupon"]:
            label += " クーポンあり"
        pub = state.get(it["asin"], {}).get("first_seen", now)
        parts.append('<item>')
        parts.append(f'<title>{escape(label)}</title>')
        parts.append(f'<link>https://www.amazon.co.jp/dp/{it["asin"]}</link>')
        parts.append(f'<guid isPermaLink="false">{it["asin"]}</guid>')
        parts.append(f'<pubDate>{formatdate(pub)}</pubDate>')
        parts.append(f'<description>{escape(" / ".join(bits))}</description>')
        parts.append('</item>')
    parts.append('</channel></rss>')
    return "\n".join(parts) + "\n"


def main():
    state = load_state()
    found = {}
    failed = []
    for mat, q in MATERIALS.items():
        try:
            page = fetch(search_url(q))
        except Exception as e:
            print(f"ERROR {mat}: {e}", file=sys.stderr)
            failed.append(mat)
            continue
        for it in parse(page, mat):
            prev = found.get(it["asin"])
            if prev is None or it["pct"] > prev["pct"]:
                found[it["asin"]] = it
    if failed:
        print("failed materials: " + ", ".join(failed) +
              "; keeping previous feed", file=sys.stderr)
        return 1
    items = sorted(found.values(), key=lambda x: -x["pct"])[:30]
    now = int(time.time())
    for it in items:
        ent = state.setdefault(it["asin"], {})
        ent.setdefault("first_seen", now)
        ent["last_seen"] = now
    cutoff = now - 30 * 86400
    state = {k: v for k, v in state.items()
             if v.get("last_seen", v.get("first_seen", now)) >= cutoff}
    save_state(state)
    xml = build_feed(items, state, now)
    tmp = FEED.with_suffix(".tmp")
    tmp.write_text(xml, encoding="utf-8")
    tmp.replace(FEED)
    print(f"feed written: {len(items)} items")
    return 0


if __name__ == "__main__":
    sys.exit(main())
