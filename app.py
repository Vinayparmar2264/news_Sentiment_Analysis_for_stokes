import os
import csv
import json
import logging
import requests
import difflib
import re
from collections import Counter
from datetime import datetime, timezone, timedelta
from functools import lru_cache
from flask import Flask, request, jsonify
from flask_cors import CORS
from newsapi import NewsApiClient
from transformers import pipeline
import yfinance as yf
import pandas as pd
import pandas_ta as ta
from concurrent.futures import ThreadPoolExecutor

# ---------- logging CONFIGURATION ----------
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger(__name__)

# Optional libs
try:
    import spacy

    spacy_nlp = spacy.load("en_core_web_sm")
except (ImportError, OSError):
    logger.warning("spaCy not found or model not downloaded. Keyword extraction will be limited.")
    spacy_nlp = None

# ---------- config ----------
NEWSAPI_KEY = os.environ.get("NEWSAPI_KEY") or "d601447f4115481581d4e546839350d5"
# MODEL_A = os.environ.get("FIN_MODEL") or "ProsusAI/finbert"
# Point to your local folder


MODEL_A = "./my_finetuned_finbert"

MODEL_B = os.environ.get("GEN_MODEL") or "distilbert-base-uncased-finetuned-sst-2-english"
FETCH_PAGE_SIZE = int(os.environ.get("FETCH_PAGE_SIZE", "100"))
MAX_ARTICLES = int(os.environ.get("MAX_ARTICLES", "20"))
TICKERS_CSV = "tickers.csv"
TRADING_SIGNAL_DISCLAIMER = "Trading signals are for informational purposes only and do not constitute financial advice. All investments carry risk. Consult a qualified financial advisor before making any decisions."

# --- START: INDIA-FOCUSED NEWS SOURCES ---
# A curated list of top Indian financial news domains for a targeted search.
INDIAN_NEWS_DOMAINS = [
    'economictimes.indiatimes.com', 'livemint.com', 'moneycontrol.com',
    'thehindubusinessline.com', 'business-standard.com', 'financialexpress.com',
    'businesstoday.in', 'zeebiz.com', 'timesofindia.indiatimes.com', 'ndtv.com/business'
]
# --- END: INDIA-FOCUSED NEWS SOURCES ---

SUGGESTION_TICKERS = [
    'RELIANCE.NS', 'TCS.NS', 'HDFCBANK.NS', 'INFY.NS', 'ICICIBANK.NS',
    'HINDUNILVR.NS', 'SBIN.NS', 'BAJAJFINANCE.NS', 'BHARTIARTL.NS', 'LT.NS',
    'KOTAKBANK.NS', 'TATAMOTORS.NS', 'ITC.NS', 'AXISBANK.NS', 'MARUTI.NS',
    'ASIANPAINT.NS', 'WIPRO.NS', 'HCLTECH.NS', 'ULTRACEMCO.NS', 'SUNPHARMA.NS'
]

# ---------- flask + clients ----------
app = Flask(__name__)
CORS(app)
newsapi = NewsApiClient(api_key=NEWSAPI_KEY)

# ---------- model loading ----------
sentiment_a = pipeline("sentiment-analysis", model=MODEL_A)
sentiment_b = pipeline("sentiment-analysis", model=MODEL_B)
loaded_models = [MODEL_A, MODEL_B]

# --- NEWS WEIGHTING & TRADING SIGNAL ENGINES (Unchanged) ---
IMPACT_KEYWORDS = {
    "clean chit": 10, "acquittal": 10, "record profit": 8, "strong earnings": 8,
    "beats expectations": 7, "beats estimates": 7, "acquisition": 7, "merger": 7,
    "sebi approval": 9, "rbi approval": 9, "blockbuster": 8, "surges": 7, "soars": 7,
    "bull run": 6, "all-time high": 8, "upgrades rating": 7, "fraud": -10, "scam": -10,
    "investigation": -8, "probe": -8, "sebi probe": -9, "raid": -9, "record loss": -8,
    "plunges": -7, "collapses": -7, "crashes": -7, "downgrades rating": -7, "lawsuit": -7,
    "allegations": -6, "misses estimates": -7, "positive outlook": 5, "optimistic": 4,
    "expansion": 4, "growth": 4, "strong demand": 5, "new contract": 5, "deal signed": 5,
    "negative outlook": -5, "concerns": -4, "warning": -4, "slumps": -5, "weak demand": -5,
    "job cuts": -5, "layoffs": -5,
}


def calculate_article_impact(text):
    score = 1
    text_lower = text.lower()
    for keyword, weight in IMPACT_KEYWORDS.items():
        if keyword in text_lower:
            score += abs(weight)
    return score


def generate_trading_signal(history_df):
    default_response = {"signal": "N/A", "summary": "Insufficient historical data to generate a signal.",
                        "indicator_details": [], "disclaimer": TRADING_SIGNAL_DISCLAIMER}
    if history_df is None or len(history_df) < 50:
        return default_response
    try:
        history_df.ta.rsi(length=14, append=True)
        history_df.ta.macd(fast=12, slow=26, signal=9, append=True)
        history_df.ta.bbands(length=20, std=2, append=True)
        history_df.dropna(inplace=True)
        if history_df.empty: return default_response
        latest, previous = history_df.iloc[-1], history_df.iloc[-2]
        buy_score, sell_score, indicator_details = 0, 0, []
        rsi_signal, rsi_explanation = "Neutral", "RSI is in the neutral zone, indicating balanced buying and selling pressure."
        if latest['RSI_14'] < 30:
            buy_score += 1; rsi_signal = "Buy"; rsi_explanation = "The stock appears to be oversold, which can sometimes precede a price increase."
        elif latest['RSI_14'] > 70:
            sell_score += 1; rsi_signal = "Sell"; rsi_explanation = "The stock appears to be overbought, which can sometimes precede a price correction."
        indicator_details.append(
            {"name": "Relative Strength Index (RSI)", "value": f"{latest['RSI_14']:.2f}", "signal": rsi_signal,
             "explanation": rsi_explanation})
        macd_signal, macd_explanation = "Neutral", "Momentum appears stable."
        if latest['MACD_12_26_9'] > latest['MACDs_12_26_9'] and previous['MACD_12_26_9'] < previous['MACDs_12_26_9']:
            buy_score += 1; macd_signal = "Buy"; macd_explanation = "A positive crossover suggests increasing bullish momentum."
        elif latest['MACD_12_26_9'] < latest['MACDs_12_26_9'] and previous['MACD_12_26_9'] > previous['MACDs_12_26_9']:
            sell_score += 1; macd_signal = "Sell"; macd_explanation = "A negative crossover suggests increasing bearish momentum."
        indicator_details.append({"name": "MACD", "value": f"{latest['MACD_12_26_9']:.2f}", "signal": macd_signal,
                                  "explanation": macd_explanation})
        bb_signal, bb_explanation = "Neutral", "The price is trading within its expected range."
        if latest['Close'] < latest['BBL_20_2.0']:
            buy_score += 1; bb_signal = "Buy"; bb_explanation = "The price is below the lower band, suggesting it may be undervalued."
        elif latest['Close'] > latest['BBU_20_2.0']:
            sell_score += 1; bb_signal = "Sell"; bb_explanation = "The price is above the upper band, suggesting it may be overvalued."
        indicator_details.append({"name": "Bollinger Bands®", "value": f"{latest['Close']:.2f}", "signal": bb_signal,
                                  "explanation": bb_explanation})
        signal, summary = "Hold", "Technical indicators are mixed or neutral."
        if buy_score >= 2:
            signal, summary = "Strong Buy", "Multiple indicators suggest strong buying pressure."
        elif sell_score >= 2:
            signal, summary = "Strong Sell", "Multiple indicators suggest strong selling pressure."
        elif buy_score > sell_score:
            signal, summary = "Buy", "At least one key indicator suggests a potential buying opportunity."
        elif sell_score > buy_score:
            signal, summary = "Sell", "At least one key indicator suggests a potential selling opportunity."
        return {"signal": signal, "summary": summary, "indicator_details": indicator_details,
                "disclaimer": TRADING_SIGNAL_DISCLAIMER}
    except Exception as e:
        logger.error(f"Error generating trading signal: {e}")
        return {"signal": "Error", "summary": "An error occurred during technical analysis.", "indicator_details": [],
                "disclaimer": TRADING_SIGNAL_DISCLAIMER}


# --- CSV & Ticker Logic (Unchanged) ---
company_to_tickers = {}
name_index = []


def load_tickers_csv(path=TICKERS_CSV):
    global company_to_tickers, name_index
    if not os.path.exists(path):
        logger.warning(f"Tickers CSV not found at '{path}'.")
        return
    try:
        with open(path, newline='', encoding='utf-8') as f:
            reader = csv.reader(f)
            next(reader, None)
            for row in reader:
                if len(row) >= 2:
                    ticker, company_name = row[0].strip(), row[1].strip()
                    if company_name and ticker:
                        company_to_tickers[company_name.lower()] = ticker
                        name_index.append(company_name)
        logger.info(f"Loaded {len(company_to_tickers)} companies from '{path}'.")
    except Exception as e:
        logger.exception(f"Failed to load tickers CSV: {e}")


load_tickers_csv()


@lru_cache(maxsize=512)
def map_name_to_ticker_from_csv(query_name):
    if not query_name: return None
    query_lower = query_name.lower()
    if query_lower in company_to_tickers: return company_to_tickers[query_lower]
    scored_matches = []
    for name in name_index:
        name_lower = name.lower()
        similarity = difflib.SequenceMatcher(None, query_lower, name_lower).ratio()
        score = similarity
        if all(token in name_lower for token in query_lower.split()): score += 0.3
        if name_lower.startswith(query_lower): score += 0.2
        scored_matches.append((name, score))
    if not scored_matches: return None
    scored_matches.sort(key=lambda x: x[1], reverse=True)
    best_match_name, best_score = scored_matches[0]
    if best_score >= 0.7:
        return company_to_tickers[best_match_name.lower()]
    return None


@lru_cache(maxsize=512)
def search_ticker_via_api(query):
    url = f"https://query1.finance.yahoo.com/v1/finance/search?q={query}"
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        response = requests.get(url, headers=headers, timeout=5)
        response.raise_for_status()
        data = response.json()
        results = data.get('quotes', [])
        if not results: return None
        for item in results:
            if item.get('quoteType') == 'EQUITY' and item.get('symbol') and (
                    '.NS' in item['symbol'] or '.BO' in item['symbol']): return item['symbol']
        for item in results:
            if item.get('quoteType') == 'EQUITY' and item.get('symbol'): return item['symbol']
        return None
    except Exception as e:
        logger.error(f"API call to Yahoo Finance search failed: {e}")
        return None


def resolve_input_to_ticker(query):
    api_result = search_ticker_via_api(query)
    if api_result: return api_result
    return map_name_to_ticker_from_csv(query)


def is_indian_ticker(ticker):
    return isinstance(ticker, str) and (ticker.upper().endswith('.NS') or ticker.upper().endswith('.BO'))


@lru_cache(maxsize=128)
def get_company_info_and_signal(ticker):
    try:
        t = yf.Ticker(ticker)
        info = t.info
        name = info.get("shortName") or info.get("longName")
        hist = t.history(period="1y", interval="1d")
        price_info = None
        if not hist.empty and len(hist["Close"]) > 1:
            closes = hist["Close"].dropna()
            last, prev = float(closes.iloc[-1]), float(closes.iloc[-2])
            pct = ((last - prev) / prev) * 100.0
            price_info = {"last_close": last, "pct_change": pct}
        trading_signal = generate_trading_signal(hist)
        return name, price_info, trading_signal
    except Exception as e:
        logger.error(f"yfinance/signal generation failed for ticker '{ticker}': {e}")
        return ticker, None, {"signal": "N/A", "summary": "Could not fetch company data.", "indicator_details": [],
                              "disclaimer": TRADING_SIGNAL_DISCLAIMER}


# --- UPGRADED NEWS FETCHING LOGIC ---
@lru_cache(maxsize=256)
def fetch_articles(company_name, ticker, from_date_str):
    try:
        if is_indian_ticker(ticker):
            # India-focused search
            ticker_base = ticker.split('.')[0]
            company_name_cleaned = re.sub(r'\s+(ltd|limited)\.?$', '', company_name, flags=re.IGNORECASE).strip()
            query = f'("{company_name_cleaned}" OR "{ticker_base}") AND ("NSE" OR "BSE" OR "Sensex" OR "stock market" OR "SEBI")'

            # 1. First, try the targeted search on Indian domains
            logger.info(f"Executing India-focused search on specific domains for: {query}")
            domains_str = ",".join(INDIAN_NEWS_DOMAINS)
            res = newsapi.get_everything(q=query, domains=domains_str, language="en", sort_by="relevancy",
                                         from_param=from_date_str, page_size=FETCH_PAGE_SIZE)
            articles = res.get("articles", []) or []
            if articles:
                logger.info(f"Found {len(articles)} articles from targeted Indian domains.")
                return articles

            # 2. Fallback: If no results, search all sources with the same query
            logger.warning(f"No results from targeted domains. Falling back to all sources for: {query}")
            res = newsapi.get_everything(q=query, language="en", sort_by="relevancy", from_param=from_date_str,
                                         page_size=FETCH_PAGE_SIZE)
            articles = res.get("articles", []) or []
            logger.info(f"Found {len(articles)} articles from fallback search.")
            return articles
        else:
            # Foreign company search (unchanged)
            query = f'"{company_name}" OR "{ticker}"'
            logger.info(f"Executing standard search for foreign company: {query}")
            res = newsapi.get_everything(q=query, language="en", sort_by="relevancy", from_param=from_date_str,
                                         page_size=FETCH_PAGE_SIZE)
            return res.get("articles", []) or []
    except Exception as e:
        logger.error(f"NewsAPI query failed: {e}")
        return []









def normalize_sentiment_label(label):
    """
    Converts model outputs (like LABEL_0) to standard 'positive', 'negative', 'neutral'.
    Assumes standard LabelEncoder order: 0=Negative, 1=Neutral, 2=Positive
    """
    label = label.lower()
    if "label_0" in label: return "negative"
    if "label_1" in label: return "neutral"
    if "label_2" in label: return "positive"
    return label  # Return as-is if it's already "positive"/"negative"













# --- Flask Routes (Main logic remains the same) ---
@app.route("/analyze", methods=["GET"])
def analyze_ticker():
    query = request.args.get("ticker", "").strip()
    if not query: return jsonify({"error": "Ticker or company name required"}), 400

    resolved_ticker = resolve_input_to_ticker(query)
    if not resolved_ticker:
        return jsonify({"error": f"Could not find a valid company or ticker for '{query}'."}), 404

    company_name, price_info, trading_signal = get_company_info_and_signal(resolved_ticker)

    two_days_ago = datetime.now(timezone.utc) - timedelta(days=2)
    from_date_str = two_days_ago.strftime('%Y-%m-%d')

    articles = fetch_articles(company_name, resolved_ticker, from_date_str)

    total_weighted_score, total_impact, processed_articles = 0, 0, []
    if articles:
        company_name_base = (company_name.split()[0] if company_name else "").lower()
        ticker_base = resolved_ticker.split('.')[0].lower()
        for art in articles:
            if len(processed_articles) >= MAX_ARTICLES: break
            title, description = art.get('title') or '', art.get('description') or ''
            text_to_analyze = f"{title}. {description}"
            if not text_to_analyze.strip(): continue
            if company_name and not re.search(
                r'\b' + re.escape(ticker_base) + r'\b|\b' + re.escape(company_name_base) + r'\b', text_to_analyze,
                re.IGNORECASE): continue

            # sentiment_result = sentiment_a(text_to_analyze)[0]
            # sentiment_label = sentiment_result['label'].lower()


            sentiment_result = sentiment_a(text_to_analyze)[0]
            raw_label = sentiment_result['label']
            sentiment_label = normalize_sentiment_label(raw_label)  # Use our helper function


            impact_score = calculate_article_impact(text_to_analyze)
            sentiment_value = 1 if sentiment_label == 'positive' else -1 if sentiment_label == 'negative' else 0
            total_weighted_score += sentiment_value * impact_score
            total_impact += impact_score
            processed_articles.append({"title": title, "url": art.get("url"), "description": description,
                                       "source": (art.get("source") or {}).get("name"), "sentiment": sentiment_label,
                                       "publishedAt": art.get("publishedAt"), "impact": impact_score})

    overall_sentiment = "Neutral"
    if total_impact > 0:
        final_score = total_weighted_score / total_impact
        if final_score > 0.15:
            overall_sentiment = "Positive"
        elif final_score < -0.15:
            overall_sentiment = "Negative"

    response = {"query": query, "ticker": resolved_ticker, "company_name": company_name,
                "overall_sentiment": overall_sentiment, "models_used": loaded_models, "price": price_info,
                "articles": processed_articles, "trading_signal": trading_signal}
    return jsonify(response)


@app.route("/suggestions", methods=["GET"])
def get_suggestions():
    buy_signals, sell_signals = [], []
    with ThreadPoolExecutor(max_workers=10) as executor:
        future_to_ticker = {executor.submit(analyze_single_suggestion, ticker): ticker for ticker in SUGGESTION_TICKERS}
        for future in future_to_ticker:
            result = future.result()
            if result:
                if result['signal'] in ["Buy", "Strong Buy"]:
                    buy_signals.append(result)
                elif result['signal'] in ["Sell", "Strong Sell"]:
                    sell_signals.append(result)
    buy_signals.sort(key=lambda x: x['signal'], reverse=True)
    sell_signals.sort(key=lambda x: x['signal'], reverse=True)
    return jsonify({"buy_signals": buy_signals, "sell_signals": sell_signals})


def analyze_single_suggestion(ticker):
    try:
        name, _, signal_data = get_company_info_and_signal(ticker)
        signal = signal_data.get("signal", "N/A")
        if signal in ["Buy", "Strong Buy", "Sell", "Strong Sell"]:
            return {"name": name, "ticker": ticker, "signal": signal}
    except Exception:
        return None
    return None


@app.route("/impact", methods=["POST"])
def analyze_impact_on_company():
    payload = request.get_json()
    ticker = payload.get("ticker")
    if not ticker: return jsonify({"error": "Ticker is required."}), 400
    company_name, price_info, _ = get_company_info_and_signal(ticker)
    if not company_name: return jsonify({"error": f"Could not resolve company name for {ticker}."}), 404
    full_text = f"{payload.get('title', '')}. {payload.get('description', '')}"
    sentiment_result = sentiment_a(full_text)[0]
    result = {"impact_on": {"name": company_name, "ticker": ticker}, "sentiment": sentiment_result['label'].lower(),
              "evidence": [payload.get("description") or payload.get("title")], "key_topics": [], "price": price_info}
    return jsonify(result)


@app.route("/general_news", methods=["GET"])
def general_news():
    try:
        query = '"NIFTY 50" OR "Sensex" OR "Indian stock market"'
        res = newsapi.get_everything(q=query, language="en", sort_by="publishedAt", page_size=50)
        return jsonify(res.get("articles", []))
    except Exception as e:
        logger.exception("Error fetching general news")
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)



