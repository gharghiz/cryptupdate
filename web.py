"""
web.py - Flask web server
RSS Feed + Cache + SEO + Admin
"""

from flask import Flask, render_template, jsonify, request, Response
import os
import time
import requests
import re
from database import init_db, get_news, get_news_by_id, get_stats

app = Flask(__name__)
init_db()

SITE_URL     = os.environ.get("SITE_URL", "https://rare-spontaneity-production-1b51.up.railway.app")
SITE_NAME    = "CryptositNews"
GSC_META_TAG = os.environ.get("GSC_META_TAG", "")
ADMIN_KEY    = os.environ.get("ADMIN_KEY", "cryptosit2025")

def parse_int_param(name: str, default: int, minimum: int = None, maximum: int = None) -> int:
    raw = request.args.get(name, default)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = default
    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value

def get_filtered_news_page(category: str, search: str, page: int, per_page: int):
    return get_news(page=page, per_page=per_page, search=search or None, category=category)

# ============================================================
# Page cache
# ============================================================

_page_cache = {}
PAGE_CACHE_TTL = 120

def page_cache_get(key):
    if key in _page_cache:
        data, ts = _page_cache[key]
        if time.time() - ts < PAGE_CACHE_TTL:
            return data
        del _page_cache[key]
    return None

def page_cache_set(key, data):
    _page_cache[key] = (data, time.time())

# ============================================================
# Category mapping
# ============================================================

NEGATION_WORDS = {"not", "no", "despite", "survives", "resists", "avoided"}

def _count_sentiment(tokens: list, words: set) -> int:
    score = 0
    for i, token in enumerate(tokens):
        if token not in words:
            continue
        context = tokens[max(0, i-2):i]
        if not any(c in NEGATION_WORDS for c in context):
            score += 1
    return score

def _compose_text(item: dict) -> str:
    summary = item.get("summary") or ""
    if not summary and item.get("content"):
        summary = str(item.get("content"))[:150] + "..."
    raw = re.sub(r"<[^>]+>", " ", str(summary))
    return f"{item.get('title','')} {raw}".lower().strip()

def compute_market_intelligence(items: list) -> dict:
    positive_words = {"surge","rally","pumped","breakout","approved","inflow","gained","bullish","soared","jumped","surging"}
    negative_words = {"crashed","dumped","hacked","exploited","lawsuit","banned","outflow","falling","dropped","bearish","plunged","slumped"}
    coin_aliases = {"BTC":["bitcoin","btc"],"ETH":["ethereum","eth"],"SOL":["solana","sol"],"BNB":["bnb","binance"],"XRP":["xrp","ripple"]}

    coin_scores = {c:{"pos":0,"neg":0,"mentions":0} for c in coin_aliases}
    whale_hits = []
    bull = bear = 0

    for item in items[:50]:
        text = _compose_text(item)
        tokens = text.replace("-", " ").split()
        pos = _count_sentiment(tokens, positive_words)
        neg = _count_sentiment(tokens, negative_words)

        if pos > neg:
            bull += 1
        elif neg > pos:
            bear += 1

        if any(k in text for k in ["whale","million","moved","transfer","inflow","outflow"]):
            whale_hits.append(item.get("title",""))

        for coin, keys in coin_aliases.items():
            if any(k in text for k in keys):
                coin_scores[coin]["mentions"] += 1
                coin_scores[coin]["pos"] += pos
                coin_scores[coin]["neg"] += neg

    best_coin = max(
        coin_aliases.keys(),
        key=lambda c: (
            coin_scores[c]["mentions"] > 0,
            coin_scores[c]["pos"] - coin_scores[c]["neg"],
            coin_scores[c]["mentions"],
        ),
    )

    c = coin_scores[best_coin]
    total = max(1, c["pos"] + c["neg"])
    confidence = min(95, int(50 + (abs(c["pos"] - c["neg"]) / total) * 45))
    signal = "Bullish" if c["pos"] >= c["neg"] else "Bearish"

    total_sent = max(1, bull + bear)
    bullish_pct = int((bull / total_sent) * 100)
    bearish_pct = 100 - bullish_pct

    return {
        "ai_signal": {
            "coin": best_coin,
            "signal": signal,
            "confidence": confidence,
            "reason": f"Positive signals {c['pos']} vs negative {c['neg']} across {c['mentions']} related stories"
        },
        "sentiment": {
            "bullish": bullish_pct,
            "bearish": bearish_pct
        },
        "whale_activity": whale_hits[:3],
    }



def get_cached_intel() -> dict:
    cached = page_cache_get("global_intel")
    if cached:
        return cached
    latest_batch, _ = get_news(page=1, per_page=60)
    intel = compute_market_intelligence(latest_batch)
    page_cache_set("global_intel", intel)
    return intel

# ============================================================
# Main page
# ============================================================

@app.route("/")
def index():
    page       = parse_int_param("page", default=1, minimum=1)
    search     = request.args.get("q", "").strip()
    active_tab = request.args.get("tab", "").strip()

    cache_key = f"page_{page}_{search}_{active_tab}"
    cached = page_cache_get(cache_key)
    if cached and not search:
        return cached

    per_page = 20
    if active_tab and active_tab != "all":
        news, total = get_filtered_news_page(active_tab, search, page, per_page)
    else:
        news, total = get_news(page=page, per_page=per_page, search=search or None)

    stats = get_stats()
    pages = max(1, (total + 19) // 20)
    intel = get_cached_intel()

    rendered = render_template("index.html",
        news=news, stats=stats,
        intel=intel,
        page=page, pages=pages,
        total=total, search=search,
        active_tab=active_tab,
        gsc_meta=GSC_META_TAG,
        site_url=SITE_URL,
    )

    if not search:
        page_cache_set(cache_key, rendered)

    return rendered

# ============================================================
# Article page
# ============================================================

@app.route("/news/<path:news_id>")
def news_page(news_id):
    item = get_news_by_id(news_id)
    if not item:
        return "Not Found", 404
    return render_template("article.html", item=item, site_url=SITE_URL)

# ============================================================
# RSS Feed
# ============================================================

@app.route("/feed")
@app.route("/feed.xml")
@app.route("/rss")
@app.route("/rss.xml")
def rss_feed():
    cached = page_cache_get("rss_feed")
    if cached:
        return Response(cached, mimetype="application/rss+xml")

    news, _ = get_news(page=1, per_page=50, search=None)
    items   = []

    for item in news:
        title    = item["title"].replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")
        link     = f"{SITE_URL}/news/{item['id']}"
        pub_date = item["posted_at"][:19].replace("T", " ") + " UTC"
        source   = item["source"].replace("&","&amp;")
        desc     = (item.get("summary") or item["title"]).replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")

        items.append(f"""  <item>
    <title>{title}</title>
    <link>{link}</link>
    <description>{desc}</description>
    <pubDate>{pub_date}</pubDate>
    <source>{source}</source>
    <guid isPermaLink="true">{link}</guid>
  </item>""")

    rss = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">
<channel>
  <title>{SITE_NAME} — Live Crypto News</title>
  <link>{SITE_URL}</link>
  <description>Real-time crypto and trading news</description>
  <language>en-us</language>
  <atom:link href="{SITE_URL}/feed.xml" rel="self" type="application/rss+xml"/>
{"".join(items)}
</channel>
</rss>"""

    page_cache_set("rss_feed", rss)
    return Response(rss, mimetype="application/rss+xml")

# ============================================================
# API
# ============================================================

@app.route("/api/news")
def api_news():
    page   = parse_int_param("page", default=1, minimum=1)
    per_page = parse_int_param("per_page", default=20, minimum=1, maximum=100)
    search = request.args.get("q", "").strip()
    cat    = request.args.get("cat", "").strip()

    if cat and cat != "all":
        news, total = get_filtered_news_page(cat, search, page, per_page)
    else:
        news, total = get_news(page=page, per_page=per_page, search=search or None)

    return jsonify({"news": news, "total": total, "page": page, "per_page": per_page})

@app.route("/api/prices")
def api_prices():
    cached = page_cache_get("coin_prices")
    if cached:
        return jsonify(cached)
    mapping = {"BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana", "BNB": "binancecoin", "XRP": "ripple"}
    try:
        resp = requests.get(
            "https://api.coingecko.com/api/v3/simple/price",
            params={"ids": ",".join(mapping.values()), "vs_currencies": "usd", "include_24hr_change": "true"},
            timeout=6,
        )
        if resp.status_code == 429:
            return jsonify({"error": "Rate limited"}), 429
        data = resp.json()
        result = {}
        for sym, coin_id in mapping.items():
            d = data.get(coin_id, {})
            result[sym] = {"price": round(d.get("usd", 0), 2), "change": round(d.get("usd_24h_change", 0), 2)}
        page_cache_set("coin_prices", result)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 503

@app.route("/health")
def health():
    return jsonify({"status": "ok"})

# ============================================================
# Admin — حذف DB
# ============================================================

@app.route("/admin/clear")
def admin_clear():
    """حذف telegram_log فقط — الموقع ما يتأثرش"""
    key = request.args.get("key", "")
    if key != ADMIN_KEY:
        return jsonify({"error": "Unauthorized"}), 401

    try:
        from database import get_pg_conn, get_sqlite_conn, USE_POSTGRES, cache_clear

        if USE_POSTGRES:
            conn = get_pg_conn(); cur = conn.cursor()
            # إنشاء الجدول إيلا ما كانش موجود
            cur.execute("""
                CREATE TABLE IF NOT EXISTS telegram_log (
                    id TEXT PRIMARY KEY, posted_at TEXT
                )
            """)
            cur.execute("DELETE FROM telegram_log")
            deleted = cur.rowcount
            conn.commit(); cur.close(); conn.close()
        else:
            with get_sqlite_conn() as conn:
                conn.execute("""CREATE TABLE IF NOT EXISTS telegram_log (id TEXT PRIMARY KEY, posted_at TEXT)""")
                cur = conn.execute("DELETE FROM telegram_log")
                deleted = cur.rowcount
                conn.commit()

        cache_clear()
        _page_cache.clear()
        return jsonify({"status": "✅ telegram_log cleared", "deleted": deleted, "note": "posted_news (website) untouched"})

    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/admin/init")
def admin_init():
    """إنشاء جميع الجداول — شغلو مرة واحدة بعد deploy"""
    key = request.args.get("key", "")
    if key != ADMIN_KEY:
        return jsonify({"error": "Unauthorized"}), 401
    try:
        from database import init_db
        init_db()
        return jsonify({"status": "✅ DB initialized"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ============================================================
# SEO
# ============================================================

@app.route("/sitemap.xml")
def sitemap():
    cached = page_cache_get("sitemap")
    if cached:
        return Response(cached, mimetype="application/xml")

    news, _ = get_news(page=1, per_page=1000)
    urls = [f"<url><loc>{SITE_URL}/</loc><changefreq>hourly</changefreq><priority>1.0</priority></url>"]

    for cat in ["bitcoin","ethereum","defi","nft","regulation","market","altcoin","breaking"]:
        urls.append(f"<url><loc>{SITE_URL}/?tab={cat}</loc><changefreq>hourly</changefreq><priority>0.9</priority></url>")

    for item in news:
        nid = item["id"].replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")
        urls.append(f"<url><loc>{SITE_URL}/news/{nid}</loc><lastmod>{item['posted_at'][:10]}</lastmod><changefreq>never</changefreq><priority>0.8</priority></url>")

    xml = f'<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n{"".join(urls)}\n</urlset>'
    page_cache_set("sitemap", xml)
    return Response(xml, mimetype="application/xml")

@app.route("/robots.txt")
def robots():
    return Response(f"User-agent: *\nAllow: /\nSitemap: {SITE_URL}/sitemap.xml", mimetype="text/plain")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
