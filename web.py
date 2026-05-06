"""
web.py - Flask web server
RSS Feed + Cache + SEO + Admin + Widgets API
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
# Helpers
# ============================================================

def fmt_large(n):
    if not n or n < 0:
        return "—"
    if n >= 1e12:
        return f"${n / 1e12:.2f}T"
    if n >= 1e9:
        return f"${n / 1e9:.2f}B"
    if n >= 1e6:
        return f"${n / 1e6:.2f}M"
    return f"${n:,.0f}"

# ============================================================
# Page cache
# ============================================================

_page_cache = {}
PAGE_CACHE_TTL = 120
WIDGET_CACHE_TTL = 300

def page_cache_get(key):
    if key in _page_cache:
        data, ts = _page_cache[key]
        if time.time() - ts < PAGE_CACHE_TTL:
            return data
        del _page_cache[key]
    return None

def widget_cache_get(key):
    if key in _page_cache:
        data, ts = _page_cache[key]
        if time.time() - ts < WIDGET_CACHE_TTL:
            return data
        del _page_cache[key]
    return None

def page_cache_set(key, data):
    _page_cache[key] = (data, time.time())

# ============================================================
# Market Intelligence — يعيد 3 إشارات
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
    coin_aliases = {
        "BTC": ["bitcoin","btc"], "ETH": ["ethereum","eth"],
        "SOL": ["solana","sol"], "BNB": ["bnb","binance"], "XRP": ["xrp","ripple"],
    }

    coin_scores = {c: {"pos": 0, "neg": 0, "mentions": 0} for c in coin_aliases}
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
            whale_hits.append(item.get("title", ""))

        for coin, keys in coin_aliases.items():
            if any(k in text for k in keys):
                coin_scores[coin]["mentions"] += 1
                coin_scores[coin]["pos"] += pos
                coin_scores[coin]["neg"] += neg

    # ترتيب حسب الأفضل وأخذ أول 3
    sorted_coins = sorted(
        coin_aliases.keys(),
        key=lambda c: (
            coin_scores[c]["mentions"] > 0,
            coin_scores[c]["pos"] - coin_scores[c]["neg"],
            coin_scores[c]["mentions"],
        ),
        reverse=True,
    )

    ai_signals = []
    for coin in sorted_coins[:3]:
        c = coin_scores[coin]
        if c["mentions"] == 0:
            continue
        total = max(1, c["pos"] + c["neg"])
        confidence = min(95, int(50 + (abs(c["pos"] - c["neg"]) / total) * 45))
        signal = "Bullish" if c["pos"] >= c["neg"] else "Bearish"
        ai_signals.append({
            "coin": coin,
            "signal": signal,
            "confidence": confidence,
            "reason": f"Positive {c['pos']} vs negative {c['neg']} across {c['mentions']} stories"
        })

    default_signal = {"coin": "N/A", "signal": "Neutral", "confidence": 0, "reason": "Analyzing latest stories..."}

    total_sent = max(1, bull + bear)
    bullish_pct = int((bull / total_sent) * 100)
    bearish_pct = 100 - bullish_pct

    return {
        "ai_signal": ai_signals[0] if ai_signals else default_signal,
        "ai_signals": ai_signals if ai_signals else [default_signal],
        "sentiment": {"bullish": bullish_pct, "bearish": bearish_pct},
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
        news=news, stats=stats, intel=intel,
        page=page, pages=pages, total=total,
        search=search, active_tab=active_tab,
        gsc_meta=GSC_META_TAG, site_url=SITE_URL,
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
    return render_template("article.html", item=item, site_url=SITE_URL, gsc_meta=GSC_META_TAG)

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
    items = []
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
# API — News
# ============================================================

@app.route("/api/news")
def api_news():
    page     = parse_int_param("page", default=1, minimum=1)
    per_page = parse_int_param("per_page", default=20, minimum=1, maximum=100)
    search   = request.args.get("q", "").strip()
    cat      = request.args.get("cat", "").strip()

    if cat and cat != "all":
        news, total = get_filtered_news_page(cat, search, page, per_page)
    else:
        news, total = get_news(page=page, per_page=per_page, search=search or None)

    return jsonify({"news": news, "total": total, "page": page, "per_page": per_page})

# ============================================================
# API — Live Prices
# ============================================================

@app.route("/api/prices")
def api_prices():
    cached = widget_cache_get("coin_prices")
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
            result[sym] = {
                "price": round(d.get("usd", 0), 2),
                "change": round(d.get("usd_24h_change", 0), 2),
            }
        page_cache_set("coin_prices", result)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 503

# ============================================================
# API — Fear & Greed Index
# ============================================================

@app.route("/api/fear-greed")
def api_fear_greed():
    cached = widget_cache_get("fear_greed")
    if cached:
        return jsonify(cached)
    try:
        resp = requests.get("https://api.alternative.me/fng/?limit=1", timeout=5)
        data = resp.json()
        d = data.get("data", [{}])[0]
        result = {
            "value": int(d.get("value", 50)),
            "label": d.get("value_classification", "Neutral"),
        }
        page_cache_set("fear_greed", result)
        return jsonify(result)
    except Exception:
        fallback = {"value": 50, "label": "Neutral"}
        return jsonify(fallback)

# ============================================================
# API — Global Market Data
# ============================================================

@app.route("/api/global")
def api_global():
    cached = widget_cache_get("global_data")
    if cached:
        return jsonify(cached)
    try:
        resp = requests.get("https://api.coingecko.com/api/v3/global", timeout=6)
        if resp.status_code == 429:
            return jsonify({"error": "Rate limited"}), 429
        d = resp.json().get("data", {})
        mcp = d.get("market_cap_percentage", {})
        result = {
            "market_cap": fmt_large(d.get("total_market_cap", {}).get("usd", 0)),
            "volume": fmt_large(d.get("total_volume", {}).get("usd", 0)),
            "btc_dom": round(mcp.get("btc", 0), 1),
            "eth_dom": round(mcp.get("eth", 0), 1),
            "active": d.get("active_cryptocurrencies", 0),
            "market_cap_change": round(d.get("market_cap_change_percentage_24h_usd", 0), 2),
            "volume_change": round(d.get("total_volume", {}).get("usd", 0) / max(1, d.get("total_volume", {}).get("usd", 1)) * 100, 2) if d.get("total_volume") else 0,
        }
        page_cache_set("global_data", result)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 503

# ============================================================
# API — Trending / Top Gainers
# ============================================================

@app.route("/api/trending")
def api_trending():
    cached = widget_cache_get("trending_coins")
    if cached:
        return jsonify(cached)
    try:
        resp = requests.get(
            "https://api.coingecko.com/api/v3/coins/markets",
            params={
                "vs_currency": "usd",
                "order": "price_change_percentage_24h_desc",
                "per_page": "6",
                "page": "1",
                "sparkline": "false",
            },
            timeout=6,
        )
        if resp.status_code == 429:
            return jsonify({"error": "Rate limited"}), 429
        data = resp.json()
        result = []
        for c in data:
            result.append({
                "symbol": c.get("symbol", "").upper(),
                "name": c.get("name", ""),
                "price": c.get("current_price", 0),
                "change": round(c.get("price_change_percentage_24h", 0), 2),
                "rank": c.get("market_cap_rank", 0),
                "image": c.get("image", ""),
            })
        page_cache_set("trending_coins", result)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 503

@app.route("/health")
def health():
    return jsonify({"status": "ok"})

# ============================================================
# Admin
# ============================================================

@app.route("/admin/clear")
def admin_clear():
    key = request.args.get("key", "")
    if key != ADMIN_KEY:
        return jsonify({"error": "Unauthorized"}), 401
    try:
        from database import get_pg_conn, get_sqlite_conn, USE_POSTGRES, cache_clear
        if USE_POSTGRES:
            conn = get_pg_conn(); cur = conn.cursor()
            cur.execute("CREATE TABLE IF NOT EXISTS telegram_log (id TEXT PRIMARY KEY, posted_at TEXT)")
            cur.execute("DELETE FROM telegram_log")
            deleted = cur.rowcount
            conn.commit(); cur.close(); conn.close()
        else:
            with get_sqlite_conn() as conn:
                conn.execute("CREATE TABLE IF NOT EXISTS telegram_log (id TEXT PRIMARY KEY, posted_at TEXT)")
                cur = conn.execute("DELETE FROM telegram_log")
                deleted = cur.rowcount
                conn.commit()
        cache_clear()
        _page_cache.clear()
        return jsonify({"status": "ok", "deleted": deleted})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/admin/init")
def admin_init():
    key = request.args.get("key", "")
    if key != ADMIN_KEY:
        return jsonify({"error": "Unauthorized"}), 401
    try:
        init_db()
        return jsonify({"status": "ok"})
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
