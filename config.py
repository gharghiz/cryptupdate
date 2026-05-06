"""
config.py - جميع الإعدادات
"""

import os

# Telegram
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID", "")

# Timing
INTERVAL_MINUTES = int(os.environ.get("INTERVAL_MINUTES", "15"))

# OpenAI
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")

# RSS Sources — 25 مصدر
RSS_FEEDS = [
    {"name": "CoinTelegraph",   "url": "https://cointelegraph.com/rss"},
    {"name": "CoinDesk",        "url": "https://www.coindesk.com/arc/outboundfeeds/rss/"},
    {"name": "Decrypt",         "url": "https://decrypt.co/feed"},
    {"name": "The Block",       "url": "https://www.theblock.co/rss.xml"},
    {"name": "Blockworks",      "url": "https://blockworks.co/feed"},
    {"name": "CryptoSlate",     "url": "https://cryptoslate.com/feed/"},
    {"name": "Bitcoin Magazine", "url": "https://bitcoinmagazine.com/feed"},
    {"name": "NewsBTC",         "url": "https://www.newsbtc.com/feed/"},
    {"name": "CryptoNews",      "url": "https://cryptonews.com/news/feed/"},
    {"name": "BeInCrypto",      "url": "https://beincrypto.com/feed/"},
    {"name": "CoinGape",        "url": "https://coingape.com/feed/"},
    {"name": "AMBCrypto",       "url": "https://ambcrypto.com/feed/"},
    {"name": "U.Today",         "url": "https://u.today/rss"},
    {"name": "Bitcoinist",      "url": "https://bitcoinist.com/feed/"},
    {"name": "Crypto Briefing", "url": "https://cryptobriefing.com/feed/"},
    {"name": "DailyCoin",       "url": "https://dailycoin.com/feed/"},
    {"name": "Cryptopolitan",   "url": "https://www.cryptopolitan.com/feed/"},
    {"name": "ZyCrypto",        "url": "https://zycrypto.com/feed/"},
    {"name": "Coin Edition",    "url": "https://coinedition.com/feed/"},
    {"name": "The Defiant",     "url": "https://thedefiant.io/feed"},
    {"name": "Unchained",       "url": "https://unchainedcrypto.com/feed/"},
    {"name": "Protos",          "url": "https://protos.com/feed/"},
    {"name": "Crypto Times",    "url": "https://www.thecryptotimes.com/feed/"},
    {"name": "Coinspeaker",     "url": "https://www.coinspeaker.com/feed/"},
    {"name": "CryptoSlate News","url": "https://cryptoslate.com/news/feed/"},
    # مصادر إضافية — وصلنا 50 مصدر
    {"name": "CryptoPotato",    "url": "https://cryptopotato.com/feed/"},
    {"name": "Crypto Academy",  "url": "https://cryptoacademy.org/feed/"},
    {"name": "99Bitcoins",      "url": "https://99bitcoins.com/feed/"},
    {"name": "CoinJournal",     "url": "https://coinjournal.net/feed/"},
    {"name": "Invezz",          "url": "https://invezz.com/feed/"},
    {"name": "CryptoMode",      "url": "https://cryptomode.com/feed/"},
    {"name": "UseTheBitcoin",   "url": "https://usethebitcoin.com/feed/"},
    {"name": "BitcoinEthNews",  "url": "https://bitcoinethereumnews.com/feed/"},
    {"name": "Crypto Insight",  "url": "https://cryptoinsight.com/feed/"},
    {"name": "BTC Echo",        "url": "https://www.btc-echo.com/feed/"},
    {"name": "Coinpedia",       "url": "https://coinpedia.org/feed/"},
    {"name": "CryptoGlobe",     "url": "https://www.cryptoglobe.com/latest/feed/"},
    {"name": "Live Bitcoin News","url": "https://www.livebitcoinnews.com/feed/"},
    {"name": "Bitcoin.com News","url": "https://news.bitcoin.com/feed/"},
    {"name": "Crypto Daily",    "url": "https://cryptodaily.co.uk/feed"},
    {"name": "CoinQuora",       "url": "https://coinquora.com/feed/"},
    {"name": "Cryptonary",      "url": "https://cryptonary.com/feed/"},
    {"name": "Milk Road",       "url": "https://milkroad.com/feed/"},
    {"name": "Bankless",        "url": "https://banklesshq.com/feed"},
    {"name": "The Defiant DeFi","url": "https://newsletter.thedefiant.io/feed"},
    {"name": "DeFi Weekly",     "url": "https://defiweekly.substack.com/feed"},
    {"name": "Week in Ethereum","url": "https://weekinethereumnews.com/feed/"},
    {"name": "Into The Block",  "url": "https://medium.com/feed/intotheblock"},
    {"name": "Delphi Digital",  "url": "https://members.delphidigital.io/feed/research"},
    {"name": "Messari",         "url": "https://messari.io/rss"},
]

# Keywords
IMPORTANT_KEYWORDS = [
    "bitcoin", "btc", "ethereum", "eth", "bnb", "solana", "sol",
    "xrp", "ripple", "cardano", "ada", "dogecoin", "doge",
    "crypto", "cryptocurrency", "blockchain", "web3", "token",
    "etf", "sec", "halving", "hack", "hacked", "exploit",
    "ban", "banned", "regulation", "legal", "lawsuit",
    "crash", "dump", "pump", "surge", "rally", "bull", "bear",
    "all-time high", "ath", "record", "billion", "million",
    "binance", "coinbase", "blackrock", "fed", "federal reserve",
    "tether", "usdt", "stablecoin", "defi", "nft",
    "trading", "price", "market", "exchange", "liquidation",
    "futures", "options", "whale", "institutional", "wallet",
    "mining", "miner", "network", "protocol", "layer",
    "launch", "partnership", "adoption", "investment",
]

BREAKING_KEYWORDS = [
    "breaking", "urgent", "just in", "alert", "emergency",
    "hack", "hacked", "exploit", "crash", "ban", "banned",
    "collapse", "sec", "arrested", "scam", "rug pull"
]

HIGH_IMPACT_KEYWORDS = [
    "etf", "sec", "regulation", "crash", "pump", "halving",
    "blackrock", "federal reserve", "ban", "hack", "exploit",
    "all-time high", "ath", "liquidation", "whale", "breaking"
]

POSITIVE_WORDS = [
    "surge", "rally", "pump", "bull", "record", "high", "gain",
    "approved", "launch", "partnership", "adoption", "growth",
    "profit", "rises", "jumps", "soars", "breakout", "ath"
]

NEGATIVE_WORDS = [
    "crash", "dump", "bear", "ban", "hack", "fall", "drop",
    "loss", "decline", "lawsuit", "warning", "risk", "fear",
    "sell", "plunge", "tumbles", "falls", "exploit", "scam"
]

# CoinGecko
COIN_MAP = {
    "bitcoin": "bitcoin",   "btc": "bitcoin",
    "ethereum": "ethereum", "eth": "ethereum",
    "bnb": "binancecoin",   "solana": "solana",
    "sol": "solana",        "xrp": "ripple",
    "ripple": "ripple",     "cardano": "cardano",
    "ada": "cardano",       "dogecoin": "dogecoin",
    "doge": "dogecoin",
}
COINGECKO_CACHE_SECONDS = 300

# Price Alerts
PRICE_ALERT_COINS = {
    "bitcoin": "BTC", "ethereum": "ETH",
    "binancecoin": "BNB", "solana": "SOL", "ripple": "XRP",
}
PRICE_ALERT_THRESHOLD = float(os.environ.get("PRICE_ALERT_THRESHOLD", "3.0"))
PRICE_CHECK_INTERVAL  = int(os.environ.get("PRICE_CHECK_INTERVAL", "15"))

# Duplicate Detection
SIMILARITY_THRESHOLD = 0.85

# Posting
MAX_POSTS_PER_CYCLE  = 10
DELAY_BETWEEN_POSTS  = 2
MAX_RETRIES_TELEGRAM = 3

# ⭐ الحل الرئيسي لمشكل النشر
# نحذف الأخبار بعد 6 ساعات فقط باش تتنشر مرة أخرى
CLEANUP_HOURS = int(os.environ.get("CLEANUP_HOURS", "6"))
