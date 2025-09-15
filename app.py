import os
import json
import logging
import requests
from collections import Counter
from datetime import datetime, timezone
from functools import lru_cache
from flask import Flask, request, jsonify
from flask_cors import CORS
from newsapi import NewsApiClient
from transformers import pipeline
import yfinance as yf
import re

# ---------- logging CONFIGURATION ----------
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger(__name__)

# Optional libs (kept for keyword extraction if needed later)
try:
    import spacy

    spacy_nlp = spacy.load("en_core_web_sm")
except (ImportError, OSError):
    logger.warning("spaCy not found or model not downloaded. Keyword extraction will be limited.")
    spacy_nlp = None

# ---------- config ----------
NEWSAPI_KEY = os.environ.get("NEWSAPI_KEY") or "97ea8e7278d845fa92b3a912717969f8"
MODEL_A = os.environ.get("FIN_MODEL") or "ProsusAI/finbert"
MODEL_B = os.environ.get("GEN_MODEL") or "distilbert-base-uncased-finetuned-sst-2-english"
FETCH_PAGE_SIZE = int(os.environ.get("FETCH_PAGE_SIZE", "100"))
MAX_ARTICLES = int(os.environ.get("MAX_ARTICLES", "7"))
HALF_LIFE_HOURS = float(os.environ.get("HALF_LIFE_HOURS", "72"))

# ---------- flask + clients ----------
app = Flask(__name__)
CORS(app)
newsapi = NewsApiClient(api_key=NEWSAPI_KEY)

# ---------- model loading ----------
sentiment_a = pipeline("sentiment-analysis", model=MODEL_A)
sentiment_b = pipeline("sentiment-analysis", model=MODEL_B)
loaded_models = [MODEL_A, MODEL_B]


# ---------- NEW: Live Ticker Resolution via API (replaces CSV logic) ----------
@lru_cache(maxsize=512)
def resolve_input_to_ticker(query):
    """
    Resolves a company name or partial name to the best ticker symbol using Yahoo Finance's search API.
    This function replaces all previous CSV and fuzzy matching logic.
    """
    if not query:
        return None

    # Use the public Yahoo Finance search API endpoint
    url = f"https://query1.finance.yahoo.com/v1/finance/search?q={query}"
    headers = {'User-Agent': 'Mozilla/5.0'}  # Standard header to mimic a browser

    try:
        logger.info(f"Querying Yahoo Finance API for '{query}'...")
        response = requests.get(url, headers=headers, timeout=5)
        response.raise_for_status()  # Raise an exception for bad status codes (4xx or 5xx)
        data = response.json()

        results = data.get('quotes', [])

        if not results:
            logger.warning(f"Yahoo Finance search returned no results for '{query}'")
            return None

        # --- Smart Selection Logic ---
        # Prioritize equities, especially those with an exchange suffix (e.g., .NS, .BO)
        best_equity = None
        for item in results:
            # Check for 'EQUITY' type and a valid 'symbol'
            if item.get('quoteType') == 'EQUITY' and item.get('symbol'):
                # Give strong preference to symbols with a '.' indicating a specific exchange
                if '.' in item['symbol']:
                    logger.info(
                        f"Found high-confidence match for '{query}': {item['symbol']} ({item.get('shortname', '')})")
                    return item['symbol']
                # Store the first equity match as a fallback
                if best_equity is None:
                    best_equity = item

        # If no suffixed equity was found, return the first equity we found
        if best_equity:
            logger.info(
                f"Found fallback equity match for '{query}': {best_equity['symbol']} ({best_equity.get('shortname', '')})")
            return best_equity['symbol']

        logger.warning(f"No suitable equity match found for '{query}' in API results.")
        return None

    except requests.exceptions.RequestException as e:
        logger.error(f"API call to Yahoo Finance search failed: {e}")
        return None
    except (KeyError, IndexError, json.JSONDecodeError):
        logger.error(f"Failed to parse Yahoo Finance search response for '{query}'")
        return None


# ---------- caching & helper functions ----------
@lru_cache(maxsize=128)
def get_company_info(ticker):
    """Cached function to get company name and price from a valid ticker."""
    try:
        t = yf.Ticker(ticker)
        info = t.info
        name = info.get("shortName") or info.get("longName")

        hist = t.history(period="5d", interval="1d")
        price_info = None
        if not hist.empty:
            closes = hist["Close"].dropna()
            if len(closes) >= 1:
                last = float(closes.iloc[-1])
                prev = float(closes.iloc[-2]) if len(closes) >= 2 else last
                pct = ((last - prev) / prev) * 100.0 if prev != 0 else 0.0
                price_info = {"last_close": last, "pct_change": pct}
        return name, price_info
    except Exception as e:
        logger.error(f"yfinance failed for ticker '{ticker}': {e}")
        return ticker, None


@lru_cache(maxsize=256)
def fetch_articles(query):
    try:
        res = newsapi.get_everything(q=query, language="en", sort_by="relevancy", page_size=FETCH_PAGE_SIZE)
        return res.get("articles", []) or []
    except Exception as e:
        logger.warning(f"NewsAPI get_everything failed for '{query}': {e}")
        return []


def parse_published_at(ts):
    if not ts: return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None


# ---------- Main Application Endpoints ----------
@app.route("/analyze", methods=["GET"])
def analyze_ticker():
    query = request.args.get("ticker", "").strip()
    if not query:
        return jsonify({"error": "Ticker or company name required"}), 400

    # Use the new, live API resolver
    resolved_ticker = resolve_input_to_ticker(query)

    if not resolved_ticker:
        return jsonify(
            {"error": f"Could not find a valid company or ticker for '{query}'. Please try a different name."}), 404

    logger.info(f"Analysis requested for query='{query}', resolved_ticker='{resolved_ticker}'")

    company_name, price_info = get_company_info(resolved_ticker)

    search_queries = f'"{company_name}" OR "{resolved_ticker}"'
    articles = fetch_articles(search_queries)

    if not articles:
        return jsonify({
            "query": query, "ticker": resolved_ticker, "company_name": company_name,
            "overall_sentiment": "Neutral", "price": price_info, "articles": []
        })

    weighted_votes = {"positive": 0.0, "negative": 0.0, "neutral": 0.0}
    processed_articles = []

    for art in articles[:MAX_ARTICLES]:
        text_to_analyze = f"{(art.get('title') or '')}. {(art.get('description') or '')}"
        sentiment_result = sentiment_a(text_to_analyze)[0]
        sentiment = sentiment_result['label'].lower()

        pub_date = parse_published_at(art.get("publishedAt"))
        age_hours = (datetime.now(timezone.utc) - pub_date).total_seconds() / 3600 if pub_date else 24 * 7
        weight = 0.5 ** (age_hours / HALF_LIFE_HOURS)

        weighted_votes[sentiment] += weight
        processed_articles.append({
            "title": art.get("title"), "url": art.get("url"), "description": art.get("description"),
            "source": (art.get("source") or {}).get("name"), "sentiment": sentiment,
            "publishedAt": art.get("publishedAt")
        })

    overall_sentiment = "Neutral"
    if any(weighted_votes.values()):
        overall_sentiment = max(weighted_votes, key=weighted_votes.get).capitalize()

    response = {
        "query": query, "ticker": resolved_ticker, "company_name": company_name,
        "overall_sentiment": overall_sentiment, "models_used": loaded_models,
        "price": price_info, "articles": processed_articles
    }
    return jsonify(response)


@app.route("/impact", methods=["POST"])
def analyze_impact_on_company():
    payload = request.get_json()
    ticker = payload.get("ticker")
    if not ticker:
        return jsonify({"error": "Ticker is required for impact analysis."}), 400

    company_name, price_info = get_company_info(ticker)
    if not company_name:
        return jsonify({"error": f"Could not resolve company name for ticker {ticker}."}), 404

    title = payload.get("title", "")
    description = payload.get("description", "")
    full_text = f"{title}. {description}"

    sentiment_result = sentiment_a(full_text)[0]
    sentiment = sentiment_result['label'].lower()

    result = {
        "impact_on": {"name": company_name, "ticker": ticker},
        "sentiment": sentiment,
        "evidence": [description or title],
        "key_topics": [],
        "price": price_info
    }
    return jsonify(result)


@app.route("/general_news", methods=["GET"])
def general_news():
    try:
        # Fetching news for a major Indian index for relevance
        res = newsapi.get_everything(q="NIFTY 50 OR Sensex", language="en", sort_by="publishedAt")
        return jsonify(res.get("articles", []))
    except Exception as e:
        logger.exception("Error fetching general news")
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run(debug=True, host="127.0.0.1", port=5000)