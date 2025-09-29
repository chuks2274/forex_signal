import logging
import time
from utils import get_recent_candles
from config import PAIRS

logger = logging.getLogger("breakout")
logger.setLevel(logging.WARNING)

# --- Currency groups ---
RAW_CURRENCY_GROUPS = {
    "USD": ["EUR_USD","GBP_USD","USD_JPY","AUD_USD","NZD_USD","USD_CAD","USD_CHF"],
    "EUR": ["EUR_USD","EUR_GBP","EUR_JPY","EUR_AUD","EUR_CAD","EUR_NZD"],
    "GBP": ["GBP_USD","EUR_GBP","GBP_JPY","GBP_AUD","GBP_CAD","GBP_NZD"],
    "JPY": ["USD_JPY","EUR_JPY","GBP_JPY","AUD_JPY","NZD_JPY","CAD_JPY","CHF_JPY"],
    "AUD": ["AUD_USD","EUR_AUD","GBP_AUD","AUD_JPY","AUD_NZD","AUD_CAD","AUD_CHF"],
    "NZD": ["NZD_USD","EUR_NZD","GBP_NZD","AUD_NZD","NZD_JPY","NZD_CAD","NZD_CHF"],
    "CAD": ["USD_CAD","EUR_CAD","GBP_CAD","AUD_CAD","NZD_CAD","CAD_JPY","CAD_CHF"],
    "CHF": ["USD_CHF","EUR_CHF","GBP_CHF","AUD_CHF","NZD_CHF","CAD_CHF","CHF_JPY"],
}

CURRENCY_GROUPS = {cur: [p for p in pairs if p in PAIRS] for cur, pairs in RAW_CURRENCY_GROUPS.items()}

# Per-group cooldown tracking
_last_group_alerts = {}  # key = group, value = last alert timestamp

# ----------------- H4 Support/Resistance Detection -----------------
def detect_h4_support_resistance(pair, candles_h4=None):
    """
    Detect nearest H4 support and resistance levels using recent swing lows/highs.
    Returns (support, resistance) as floats or None.
    """
    try:
        if candles_h4 is None:
            candles_h4 = get_recent_candles(pair, "H4", 50)
        if not candles_h4:
            return None, None

        highs = [c["high"] for c in candles_h4 if "high" in c]
        lows = [c["low"] for c in candles_h4 if "low" in c]

        if not highs or not lows:
            return None, None

        resistance = max(highs[-10:])  # last 10 H4 highs
        support = min(lows[-10:])      # last 10 H4 lows
        return support, resistance
    except Exception as e:
        logger.error(f"{pair} H4 support/resistance detection error: {e}")
        return None, None

# ----------------- Individual H4 breakout check -----------------
def check_h4_breakout(pair, candles_h4=None):
    """
    Check if the latest H4 candle broke the nearest H4 support or resistance.
    Returns (level, direction) if breakout occurs, otherwise None.
    """
    try:
        if candles_h4 is None:
            candles_h4 = get_recent_candles(pair, "H4", 1)
        if not candles_h4 or "close" not in candles_h4[-1]:
            return None

        current_close = candles_h4[-1]["close"]
        support, resistance = detect_h4_support_resistance(pair)

        if resistance and current_close > resistance:
            return (resistance, "SELL")  # sell signal on breakout above resistance
        elif support and current_close < support:
            return (support, "BUY")     # buy signal on breakout below support
        return None
    except Exception as e:
        logger.error(f"{pair} H4 breakout check error: {e}")
        return None

# ----------------- Yesterday breakout check -----------------
def check_h4_yesterday_breakout(pair, candles_h4=None):
    """
    Check if any H4 candle today broke yesterday's high or low.
    Returns (level, direction) if breakout occurs, otherwise None.
    """
    try:
        if candles_h4 is None:
            candles_h4 = get_recent_candles(pair, "H4", 50)
        if not candles_h4:
            return None

        daily = get_recent_candles(pair, "D", 2)
        if not daily or len(daily) < 2 or "high" not in daily[-2] or "low" not in daily[-2]:
            return None

        prev_high, prev_low = daily[-2]["high"], daily[-2]["low"]

        for candle in candles_h4:
            close = candle.get("close")
            if close is None:
                continue
            if close > prev_high:
                return (prev_high, "SELL")
            elif close < prev_low:
                return (prev_low, "BUY")

        return None
    except Exception as e:
        logger.error(f"{pair} yesterday H4 breakout check error: {e}")
        return None

# ----------------- Group breakout alert -----------------
def run_group_h4_breakout_alert(min_pairs=4, send_alert_fn=None, group_cooldown=3600):
    """
    Check for breakout alerts per currency group on H4.
    Each group has its own cooldown (group_cooldown, in seconds).
    Returns a dict of groups with breakout pairs.
    """
    global _last_group_alerts
    now = time.time()
    alerts = {}

    for group, pairs in CURRENCY_GROUPS.items():
        breakout_pairs = []

        for pair in pairs:
            try:
                breakout_info = check_h4_breakout(pair)
                if breakout_info:
                    breakout_pairs.append(pair)
            except Exception as e:
                logger.error(f"{pair} group H4 breakout check error: {e}")

        if len(breakout_pairs) >= min_pairs:
            last_ts = _last_group_alerts.get(group, 0)
            if now - last_ts >= group_cooldown:
                alerts[group] = breakout_pairs
                _last_group_alerts[group] = now
                if send_alert_fn:
                    msg = (
                        f"📢 {group} Group H4 Breakout Alert! ({len(breakout_pairs)} pairs)\n"
                        + "\n".join(sorted(breakout_pairs))
                    )
                    send_alert_fn(msg)

    return alerts
