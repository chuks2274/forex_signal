import requests
import logging
import pytz
from datetime import datetime
import pandas as pd
import json
import os
import time
from requests.exceptions import RequestException

from config import TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, OANDA_API, HEADERS

logger = logging.getLogger("utils")
logging.basicConfig(level=logging.INFO)

# ================= TELEGRAM =================
def send_telegram(message: str) -> bool:
    """Send a message via Telegram bot."""
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        resp = requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "text": message})
        resp.raise_for_status()
        return True
    except Exception as e:
        logger.error(f"Failed to send telegram message: {e}")
        return False

# Alias for backward compatibility
send_alert = send_telegram

# ================= OANDA CANDLES =================
def fetch_oanda_candles(pair: str, granularity: str = "H1", count: int = 30, max_retries: int = 3, backoff: float = 1.5) -> list:
    url = f"{OANDA_API}/instruments/{pair}/candles"
    params = {"granularity": granularity, "count": count, "price": "M"}
    for attempt in range(1, max_retries + 1):
        try:
            r = requests.get(url, headers=HEADERS, params=params, timeout=10)
            r.raise_for_status()
            return r.json().get("candles", [])
        except RequestException as e:
            wait_time = backoff ** attempt
            logger.warning(f"[Attempt {attempt}/{max_retries}] Failed to fetch {pair} candles ({granularity}): {e}. Retrying in {wait_time:.1f}s...")
            time.sleep(wait_time)
        except Exception as e:
            logger.error(f"Unexpected error fetching {pair} candles ({granularity}): {e}")
            break
    logger.error(f"Failed to fetch OANDA candles for {pair} at {granularity} after {max_retries} attempts.")
    return []

def get_recent_candles(pair: str, timeframe: str = "H1", count: int = 30) -> list[dict]:
    raw_candles = fetch_oanda_candles(pair, timeframe, count)
    normalized = []
    for c in raw_candles:
        if isinstance(c, dict):
            mid = c.get("mid", c)
            normalized.append({
                "time": c.get("time"),
                "open": float(mid.get("o", mid.get("open", 0))),
                "high": float(mid.get("h", mid.get("high", 0))),
                "low": float(mid.get("l", mid.get("low", 0))),
                "close": float(mid.get("c", mid.get("close", 0)))
            })
        elif isinstance(c, (tuple, list)) and len(c) >= 4:
            normalized.append({
                "open": float(c[0]),
                "high": float(c[1]),
                "low": float(c[2]),
                "close": float(c[3])
            })
    return normalized

# Alias
get_candles = get_recent_candles

# ================= TECHNICAL INDICATORS =================
def ema(values: list, period: int = 14) -> list:
    if len(values) < period:
        return []
    emas = []
    k = 2 / (period + 1)
    sma = sum(values[:period]) / period
    emas.append(sma)
    for price in values[period:]:
        ema_val = price * k + emas[-1] * (1 - k)
        emas.append(ema_val)
    return emas

def atr(candles: list, period: int = 14) -> float:
    if len(candles) < period:
        return 0.0
    trs = []
    for i in range(1, len(candles)):
        high = candles[i]["high"]
        low = candles[i]["low"]
        prev_close = candles[i - 1]["close"]
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        trs.append(tr)
    return float(sum(trs[-period:]) / period)

def rsi(closes: list, period: int = 14) -> list:
    if len(closes) < period + 1:
        return []
    deltas = [closes[i + 1] - closes[i] for i in range(len(closes) - 1)]
    gains = [max(delta, 0) for delta in deltas]
    losses = [abs(min(delta, 0)) for delta in deltas]
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    rsis = [100 if avg_loss == 0 else 100 - (100 / (1 + (avg_gain / avg_loss)))]
    for i in range(period, len(gains)):
        gain = gains[i]
        loss = losses[i]
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
        rs = avg_gain / avg_loss if avg_loss != 0 else float('inf')
        rsis.append(100 - (100 / (1 + rs)))
    return rsis

def get_rsi(closes: list, period: int = 14) -> list:
    return rsi(closes, period)

def ema_slope(closes: list, period: int = 10) -> float:
    if len(closes) < period + 2:
        return 0
    series = pd.Series(closes)
    ema_series = series.ewm(span=period, adjust=False).mean()
    slope = ema_series.iloc[-1] - ema_series.iloc[-2]
    return slope

# ================= CURRENT PRICE =================
def get_current_price(pair: str) -> float:
    candles = get_recent_candles(pair, "M1", count=1)
    if candles:
        return candles[-1]["close"]
    return 0.0

# ================= CURRENCY STRENGTH =================
def get_currency_strength(pairs: list) -> dict:
    strengths = {}
    for p in pairs:
        base, quote = p.split("_")
        price = get_current_price(p)
        strengths[base] = strengths.get(base, 0) + price
        strengths[quote] = strengths.get(quote, 0) - price
    return strengths

# ================= SESSION =================
def get_current_session(now_utc=None) -> str:
    if now_utc is None:
        now_utc = datetime.utcnow()
    ny_tz = pytz.timezone("America/New_York")
    now_ny = now_utc.replace(tzinfo=pytz.utc).astimezone(ny_tz)
    hour = now_ny.hour
    if 0 <= hour < 8:
        return "Asian"
    elif 8 <= hour < 16:
        return "London"
    else:
        return "NewYork"

# ================= ACTIVE TRADES JSON =================
ACTIVE_TRADES_FILE = "active_trades.json"

def load_active_trades():
    if not os.path.exists(ACTIVE_TRADES_FILE):
        return []
    try:
        with open(ACTIVE_TRADES_FILE, "r") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Failed to load active trades: {e}")
        return []

def save_active_trades(trades):
    try:
        with open(ACTIVE_TRADES_FILE, "w") as f:
            json.dump(trades, f)
    except Exception as e:
        logger.error(f"Failed to save active trades: {e}")
        
# ================= EMA SPACING / CALCULATION =================
def calculate_ema(prices: list, period: int = 20) -> float:
    """
    Calculate the Exponential Moving Average (EMA) for a list of prices.
    Returns the last EMA value.
    """
    if len(prices) < period:
        return None
    ema = sum(prices[:period]) / period  # start with SMA
    multiplier = 2 / (period + 1)
    for price in prices[period:]:
        ema = (price - ema) * multiplier + ema
    return ema


def get_ema_spacing(pair: str, fast: int = 20, slow: int = 200) -> float:
    """
    Returns the absolute difference between fast EMA and slow EMA for a given pair.
    """
    candles = get_recent_candles(pair, "H1", max(fast, slow) + 10)
    closes = [float(c["close"]) for c in candles]
    ema_fast = calculate_ema(closes, fast)
    ema_slow = calculate_ema(closes, slow)
    if ema_fast is None or ema_slow is None:
        return 0.0
    return abs(ema_fast - ema_slow)

