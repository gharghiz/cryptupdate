# -*- coding: utf-8 -*-
"""
CryptositNews - Configuration
All settings in one place
"""

import os

# ============================================================
# GENERAL
# ============================================================
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
CHANNEL_ID = os.environ.get("CHANNEL_ID", "")
ADMIN_KEY = os.environ.get("ADMIN_KEY", "changeme_secure_key_2024")
APP_SECRET = os.environ.get("APP_SECRET", "cryptosit_secret_key")

# ============================================================
# DATABASE
# ============================================================
DATABASE_URL = os.environ.get("DATABASE_URL", "")
DB_POOL_SIZE = int(os.environ.get("DB_POOL_SIZE", "8"))
DB_POOL_TIMEOUT = int(os.environ.get("DB_POOL_TIMEOUT", "30"))

# ============================================================
# REDIS CACHE (optional - falls back to in-memory)
# ============================================================
REDIS_URL = os.environ.get("REDIS_URL", "")
REDIS_ENABLED = bool(REDIS_URL)
CACHE_DEFAULT_TTL = int(os.environ.get("CACHE_DEFAULT_TTL", "300"))
CACHE_NEWS_TTL = int(os.environ.get("CACHE_NEWS_TTL", "120"))
CACHE_PRICES_TTL = int(os.environ.get("CACHE_PRICES_TTL", "60"))
CACHE_FEAR_TTL = int(os.environ.get("CACHE_FEAR_TTL", "300"))
CACHE_GLOBAL_TTL = int(os.environ.get("CACHE_GLOBAL_TTL", "300"))
CACHE_STATS_TTL = int(os.environ.get("CACHE_STATS_TTL", "600"))
CACHE_TRENDING_TTL = int(os.environ.get("CACHE_TRENDING_TTL", "300"))

# ============================================================
# API RATE LIMITING
# ============================================================
RATE_LIMIT_REQUESTS = int(os.environ.get("RATE_LIMIT_REQUESTS", "60"))
RATE_LIMIT_WINDOW = int(os.environ.get("RATE_LIMIT_WINDOW", "60"))
RATE_LIMIT_BURST = int(os.environ.get("RATE_LIMIT_BURST", "10"))

# ============================================================
# OPENAI
# ============================================================
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
AI_MODEL = os.environ.get("AI_MODEL", "gpt-4o-mini")

# ============================================================
# COINGECKO
# ============================================================
COINGECKO_BASE = "https://api.coingecko.com/api/v3"
COINGECKO_RATE_LIMIT = 25
COINGECKO_RATE_WINDOW = 60
COINGECKO_DELAY = 2.5

COIN_MAP = {
    "BTC": "bitcoin", "ETH": "ethereum", "BNB": "binancecoin",
    "SOL": "solana", "XRP": "ripple", "ADA": "cardano",
    "DOGE": "dogecoin", "DOT": "polkadot", "AVAX": "avalanche-2",
    "MATIC": "matic-network", "LINK": "chainlink", "UNI": "uniswap",
    "ATOM": "cosmos", "LTC": "litecoin", "FIL": "filecoin",
    "APT": "aptos", "ARB": "arbitrum", "OP": "optimism",
    "NEAR": "near", "ALGO": "algorand", "FTM": "fantom",
    "SAND": "the-sandbox", "MANA": "decentraland",
    "AXS": "axie-infinity", "AAVE": "aave", "GRT": "the-graph",
    "INJ": "injective-protocol", "SUI": "sui", "SEI": "sei-network",
    "TIA": "celestia", "JUP": "jupiter-exchange-solana",
    "WIF": "dogwifcoin", "PEPE": "pepe", "SHIB": "shiba-inu",
}

# ============================================================
# FEAR & GREED
# ============================================================
FEAR_GREED_URL = "https://api.alternative.me/fng/"

# ============================================================
# SCRAPER SETTINGS
# ============================================================
SCRAPER_THREADS = int(os.environ.get("SCRAPER_THREADS", "8"))
NEWS_MAX_AGE_HOURS = int(os.environ.get("NEWS_MAX_AGE_HOURS", "48"))
MAX_ENTRIES_PER_FEED = int(os.environ.get("MAX_ENTRIES_PER_FEED", "15"))
SCRAPER_INTERVAL = int(os.environ.get("SCRAPER_INTERVAL", "300"))
SOURCE_COOLDOWN = int(os.environ.get("SOURCE_COOLDOWN", "1800"))
FAILED_SOURCE_MAX = int(os.environ.get("FAILED_SOURCE_MAX", "3"))

# ============================================================
# KEYWORDS
# ============================================================
IMPORTANT_KEYWORDS = [
    "crash", "hack", "ban", "SEC", "regulation", "bullish", "bearish",
    "breakout", "resistance", "support", " ATH", " all-time high",
    "adoption", "partnership", "launch", "upgrade", "ETF", "approval",
    "halving", "fork", "airdrop", "vulnerability", "exploit",
    "recall", "lawsuit", "investigation", "sanctions",
]

ACTION_KEYWORDS = [
    "BREAKING", "URGENT", "ALERT", "UPDATE", "CONFIRMED",
    "JUST IN", "FLASH", "EMERGENCY", "OFFICIAL", "EXCLUSIVE",
    "DEVELOPING", "RECALL", "BAN", "APPROVED", "REJECTED",
    "CRITICAL", "WARNING",
]

BREAKING_KEYWORDS = [
    "BREAKING", "URGENT", "FLASH", "EMERGENCY", "CRITICAL",
]

HIGH_IMPACT_KEYWORDS = [
    "SEC", "hack", "ban", "ETF", "halving", "lawsuit", "sanctions",
    "regulation", "approval", "rejection", "exploit", "vulnerability",
]

POSITIVE_KEYWORDS = [
    "bullish", "surge", "rally", "adoption", "partnership", "launch",
    "upgrade", "ATH", "all-time high", "approved", "breakout",
    "innovation", "milestone", "record", "growth",
]

NEGATIVE_KEYWORDS = [
    "crash", "hack", "ban", "bearish", "scam", "fraud", "lawsuit",
    "investigation", "sanctions", "exploit", "vulnerability", "plunge",
    "drop", "fall", "dump", "rug pull",
]

# ============================================================
# CATEGORY RULES (14 categories with weights)
# ============================================================
CATEGORY_RULES = {
    "regulation": {
        "keywords": ["SEC", "regulation", "regulate", "compliance", "legal",
                      "law", "lawsuit", "court", "judge", "ban", "sanction",
                      "legislation", "policy", "government", "congress",
                      "senate", "parliament", "EU", "MiCA", "enforcement"],
        "weight": 2.0,
    },
    "defi": {
        "keywords": ["DeFi", "yield", "liquidity", "AMM", "DEX", "Uniswap",
                      "Aave", "Compound", "MakerDAO", "lending", "borrowing",
                      "staking", "LP", "farming", "vault", "protocol"],
        "weight": 1.5,
    },
    "nft": {
        "keywords": ["NFT", "non-fungible", "mint", "minting", "collection",
                      "OpenSea", "Blur", "floor price", "rare", "metadata",
                      "ERC-721", "ERC-1155", "digital art", "PFP"],
        "weight": 1.2,
    },
    "layer1": {
        "keywords": ["Layer 1", "L1", "Ethereum", "Solana", "Cardano",
                      "Polkadot", "Avalanche", "Cosmos", "NEAR", "Sui",
                      "Aptos", "base chain", "blockchain", "consensus",
                      "sharding", "rollup"],
        "weight": 1.5,
    },
    "layer2": {
        "keywords": ["Layer 2", "L2", "rollup", "Optimism", "Arbitrum",
                      "zkSync", "StarkNet", "Polygon", "zkEVM", "scaling",
                      "Optimistic", "ZK", "zero-knowledge", "validium"],
        "weight": 1.3,
    },
    "meme": {
        "keywords": ["meme", "doge", "shib", "pepe", "wif", "bonk",
                      "floki", "troll", "wojak", "meme coin", "shitcoin",
                      "pump.fun", " meme"],
        "weight": 1.0,
    },
    "ai_crypto": {
        "keywords": ["AI", "artificial intelligence", "machine learning",
                      "GPT", "OpenAI", "deep learning", "neural",
                      "SingularityNET", "Fetch.ai", "Render", "Worldcoin",
                      "Bittensor", "TAO", "AI agent", "AI model"],
        "weight": 1.8,
    },
    "mining": {
        "keywords": ["mining", "miner", "hash rate", "hashrate", "ASIC",
                      "Bitcoin mining", "pool", "difficulty", "reward",
                      "halving", "energy", "power", "proof of work"],
        "weight": 1.0,
    },
    "stablecoin": {
        "keywords": ["stablecoin", "USDT", "USDC", "DAI", "BUSD",
                      "Tether", "Circle", "peg", "depeg", "reserve",
                      "stable", "fiat-backed", "crypto-backed"],
        "weight": 1.5,
    },
    "gaming": {
        "keywords": ["gaming", "GameFi", "play-to-earn", "P2E", "P2E",
                      "metaverse", "Web3 game", "blockchain game",
                      "Axie", "Illuvium", "Gala", "Treasure", "game"],
        "weight": 1.1,
    },
    "exchange": {
        "keywords": ["Binance", "Coinbase", "Kraken", "OKX", "Bybit",
                      "exchange", "trading", "listing", "delisting",
                      "margin", "futures", "spot", "order book", "volume"],
        "weight": 1.4,
    },
    "security": {
        "keywords": ["hack", "exploit", "vulnerability", "breach", "attack",
                      "malware", "phishing", "rug pull", "scam", "fraud",
                      "theft", "stolen", "compromised", "security",
                      "audit", "bug bounty"],
        "weight": 2.0,
    },
    "adoption": {
        "keywords": ["adoption", "institutional", "ETF", "approved",
                      "payment", "merchant", "accept", "integration",
                      "partnership", "enterprise", "bank", "traditional",
                      "mainstream", "Massachusetts", "Wall Street"],
        "weight": 1.6,
    },
    "market": {
        "keywords": ["price", "market", "bull", "bear", "rally", "crash",
                      "ATH", "all-time high", "cap", "volume", "surge",
                      "plunge", "recovery", "correction", "resistance",
                      "support", "breakout", "consolidation"],
        "weight": 1.3,
    },
}

# ============================================================
# RSS FEEDS (50+ sources)
# ============================================================
RSS_FEEDS = [
    # Major Crypto News
    {"url": "https://cointelegraph.com/rss", "category": "major", "lang": "en"},
    {"url": "https://www.coindesk.com/arc/outboundfeeds/rss/", "category": "major", "lang": "en"},
    {"url": "https://decrypt.co/feed", "category": "major", "lang": "en"},
    {"url": "https://cryptopotato.com/feed/", "category": "major", "lang": "en"},
    {"url": "https://www.theblock.co/rss.xml", "category": "major", "lang": "en"},
    {"url": "https://blockworks.co/feed", "category": "major", "lang": "en"},
    # Specialized
    {"url": "https://www.coingecko.com/en/rss", "category": "prices", "lang": "en"},
    {"url": "https://defipulse.com/blog/feed", "category": "defi", "lang": "en"},
    {"url": "https://nftnow.com/feed/", "category": "nft", "lang": "en"},
    # Traditional Finance
    {"url": "https://www.reuters.com/technology/fintech-cryptocurrency/", "category": "finance", "lang": "en"},
    {"url": "https://feeds.bloomberg.com/markets/news.rss", "category": "finance", "lang": "en"},
    {"url": "https://www.cnbc.com/id/100003114/device/rss/rss.html", "category": "finance", "lang": "en"},
    {"url": "https://feeds.finance.yahoo.com/rss/2.0/headline?s=cryptocurrency&region=US&lang=en-US", "category": "finance", "lang": "en"},
    {"url": "https://www.ft.com/rss/home", "category": "finance", "lang": "en"},
    # Tech
    {"url": "https://techcrunch.com/category/crypto/", "category": "tech", "lang": "en"},
    {"url": "https://www.wired.com/tag/blockchain/rss", "category": "tech", "lang": "en"},
    {"url": "https://arstechnica.com/tag/cryptocurrency/", "category": "tech", "lang": "en"},
    {"url": "https://www.technologyreview.com/topic/blockchain/feed/", "category": "tech", "lang": "en"},
    # Additional Crypto
    {"url": "https://beincrypto.com/feed/", "category": "crypto", "lang": "en"},
    {"url": "https://ambcrypto.com/feed/", "category": "crypto", "lang": "en"},
    {"url": "https://www.newsbtc.com/feed/", "category": "crypto", "lang": "en"},
    {"url": "https://www.livebitcoinnews.com/feed/", "category": "crypto", "lang": "en"},
    {"url": "https://zycrypto.com/feed/", "category": "crypto", "lang": "en"},
    {"url": "https://cryptoslate.com/feed", "category": "crypto", "lang": "en"},
    {"url": "https://dailyhodl.com/feed/", "category": "crypto", "lang": "en"},
    {"url": "https://ethereumworldnews.com/feed/", "category": "crypto", "lang": "en"},
    {"url": "https://smartphonebaba.com/feed/", "category": "crypto", "lang": "en"},
    {"url": "https://www.crypto-news-flash.com/feed", "category": "crypto", "lang": "en"},
    {"url": "https://www.coinpedia.org/feed", "category": "crypto", "lang": "en"},
    {"url": "https://www.cryptopolitan.com/feed", "category": "crypto", "lang": "en"},
    {"url": "https://insidebitcoins.com/feed/", "category": "crypto", "lang": "en"},
    # Arabic Sources
    {"url": "https://crypto-ar.com/feed/", "category": "arabic", "lang": "ar"},
    {"url": "https://arabic.cointelegraph.com/rss", "category": "arabic", "lang": "ar"},
    # More English
    {"url": "https://www.bitcoinist.com/feed/", "category": "crypto", "lang": "en"},
    {"url": "https://www.bitcoininsider.org/rss", "category": "crypto", "lang": "en"},
    {"url": "https://www.crypto Briefing.com/feed", "category": "crypto", "lang": "en"},
    {"url": "https://thecryptobasic.com/feed/", "category": "crypto", "lang": "en"},
    {"url": "https://www.publish0x.com/feed", "category": "crypto", "lang": "en"},
    {"url": "https://coinjournal.net/feed/", "category": "crypto", "lang": "en"},
    {"url": "https://www.crypto-discover.com/feed/", "category": "crypto", "lang": "en"},
    {"url": "https://crypto-economy.com/en/feed/", "category": "crypto", "lang": "en"},
    {"url": "https://coinmarketcap.com/rss/", "category": "prices", "lang": "en"},
    {"url": "https://www.deFiLlama.com/feed", "category": "defi", "lang": "en"},
    {"url": "https://dune.com/feed", "category": "defi", "lang": "en"},
    {"url": "https://www.theblockcrypto.com/rss.xml", "category": "major", "lang": "en"},
    {"url": "https://panceranews.com/feed/", "category": "crypto", "lang": "en"},
    {"url": "https://protos.com/feed/", "category": "crypto", "lang": "en"},
    {"url": "https://blockbeat.io/feed", "category": "crypto", "lang": "en"},
    {"url": "https://thedefiant.io/rss/", "category": "defi", "lang": "en"},
    {"url": "https://rekt.news/feed/", "category": "security", "lang": "en"},
]

# ============================================================
# I18N (Internationalization)
# ============================================================
SUPPORTED_LANGUAGES = ["en", "ar"]
DEFAULT_LANGUAGE = "en"

# ============================================================
# NEWSLETTER
# ============================================================
NEWSLETTER_ENABLED = os.environ.get("NEWSLETTER_ENABLED", "true").lower() == "true"

# ============================================================
# PWA
# ============================================================
PWA_ENABLED = os.environ.get("PWA_ENABLED", "true").lower() == "true"
PWA_APP_NAME = "CryptositNews"
PWA_SHORT_NAME = "CryptoNews"
PWA_DESCRIPTION = "Real-time Crypto News & Market Intelligence"
PWA_THEME_COLOR = "#0f172a"
PWA_BACKGROUND_COLOR = "#0f172a"

# ============================================================
# NOTIFICATIONS
# ============================================================
NOTIFICATION_SOUND = os.environ.get("NOTIFICATION_SOUND", "true").lower() == "true"

# ============================================================
# LOGGING
# ============================================================
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"

# ============================================================
# CLEANUP
# ============================================================
CLEANUP_DAYS = int(os.environ.get("CLEANUP_DAYS", "7"))
CLEANUP_INTERVAL = int(os.environ.get("CLEANUP_INTERVAL", "3600"))
TELEGRAM_LOG_DAYS = int(os.environ.get("TELEGRAM_LOG_DAYS", "3"))
