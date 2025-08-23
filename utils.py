import requests
import logging
import pytz
from datetime import datetime
import pandas as pd
import json
import os

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

# ================= CANDLES =================
def get_recent_candles(pair: str, granularity: str = "H1", count: int = 30) -> list:
    """Fetch recent candles from OANDA using internal config."""
    try:
        url = f"{OANDA_API}/instruments/{pair}/candles"
        params = {"granularity": granularity, "count": count, "price": "M"}
        r = requests.get(url, headers=HEADERS, params=params)
        r.raise_for_status()
        return r.json().get("candles", [])
    except Exception as e:
        logger.error(f"Failed to get recent candles for {pair} at {granularity}: {e}")
        return []

# Alias for backward compatibility
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
        high = float(candles[i]["mid"]["h"])
        low = float(candles[i]["mid"]["l"])
        prev_close = float(candles[i - 1]["mid"]["c"])
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        trs.append(tr)
    return float(sum(trs[-period:]) / period)

def rsi(closes: list, period: int = 14) -> list:
    if len(closes) < period + 1:
        return []
    deltas = [closes[i+1] - closes[i] for i in range(len(closes)-1)]
    gains = [max(delta, 0) for delta in deltas]
    losses = [abs(min(delta, 0)) for delta in deltas]

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    rsis = []
    rsis.append(100 if avg_loss == 0 else 100 - (100 / (1 + (avg_gain / avg_loss))))

    for i in range(period, len(gains)):
        gain = gains[i]
        loss = losses[i]

        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period

        rsis.append(100 if avg_loss == 0 else 100 - (100 / (1 + (avg_gain / avg_loss))))

    return rsis

def ema_slope(closes: list, period: int = 10) -> float:
    if len(closes) < period + 2:
        return 0
    series = pd.Series(closes)
    ema_series = series.ewm(span=period, adjust=False).mean()
    slope = ema_series.iloc[-1] - ema_series.iloc[-2]
    return slope

# ================= SWING POINTS =================
def find_swing_points(candles: list) -> tuple:
    highs = [float(c["mid"]["h"]) for c in candles]
    lows = [float(c["mid"]["l"]) for c in candles]

    swing_highs = []
    swing_lows = []

    for i in range(1, len(candles) - 1):
        if highs[i] > highs[i - 1] and highs[i] > highs[i + 1]:
            swing_highs.append(highs[i])
        if lows[i] < lows[i - 1] and lows[i] < lows[i + 1]:
            swing_lows.append(lows[i])

    return swing_highs, swing_lows

# ================= SESSION DETECTION =================
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
    """Load active trades from JSON file."""
    if not os.path.exists(ACTIVE_TRADES_FILE):
        return []
    try:
        with open(ACTIVE_TRADES_FILE, "r") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"[Utils] Failed to load active trades: {e}")
        return []

def save_active_trades(active_trades):
    """Save active trades to JSON file."""
    try:
        with open(ACTIVE_TRADES_FILE, "w") as f:
            json.dump(active_trades, f)
    except Exception as e:
        logger.error(f"[Utils] Failed to save active trades: {e}")