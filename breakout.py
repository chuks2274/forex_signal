import logging
import time
from utils import get_recent_candles, send_telegram
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

# ----------------- Individual H1 breakout check -----------------
def check_breakout_h1(pair, candles_h1=None, rank_map=None):
    """
    Check if the latest H1 candle broke yesterday's high or low.
    Returns (breakout_level, direction, extra) if breakout occurs, otherwise None.
    """
    try:
        if candles_h1 is None:
            candles_h1 = get_recent_candles(pair, "H1", 1)
        if not candles_h1 or "close" not in candles_h1[-1]:
            return None

        daily = get_recent_candles(pair, "D", 2)
        if not daily or len(daily) < 2 or "high" not in daily[-2] or "low" not in daily[-2]:
            return None

        prev_high, prev_low = daily[-2]["high"], daily[-2]["low"]
        current_close = candles_h1[-1]["close"]

        if current_close > prev_high:
            return (prev_high, "buy", None)
        elif current_close < prev_low:
            return (prev_low, "sell", None)
        return None
    except Exception as e:
        logger.error(f"{pair} H1 breakout check error: {e}")
        return None

# ----------------- Yesterday breakout check -----------------
def check_breakout_yesterday(pair, candles_h1=None, rank_map=None):
    """
    Check if any H1 candle today broke yesterday's high or low.
    Returns (breakout_level, direction, extra) if breakout occurs, otherwise None.
    """
    try:
        if candles_h1 is None:
            candles_h1 = get_recent_candles(pair, "H1", 50)  # last 50 H1 candles
        if not candles_h1:
            return None

        daily = get_recent_candles(pair, "D", 2)
        if not daily or len(daily) < 2 or "high" not in daily[-2] or "low" not in daily[-2]:
            return None

        prev_high, prev_low = daily[-2]["high"], daily[-2]["low"]

        for candle in candles_h1:
            close = candle.get("close")
            if close is None:
                continue
            if close > prev_high:
                return (prev_high, "buy", None)
            elif close < prev_low:
                return (prev_low, "sell", None)

        return None
    except Exception as e:
        logger.error(f"{pair} yesterday breakout check error: {e}")
        return None

# ----------------- Group breakout alert -----------------
def run_group_breakout_alert(min_pairs=4, send_alert_fn=None, group_cooldown=3600):
    """
    Check for breakout alerts per currency group.
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
                breakout_info = check_breakout_h1(pair)
                if breakout_info and breakout_info[0] is not None:
                    breakout_pairs.append(pair)
            except Exception as e:
                logger.error(f"{pair} group breakout check error: {e}")

        # Only send alert if enough pairs broke out and cooldown passed
        if len(breakout_pairs) >= min_pairs:
            last_ts = _last_group_alerts.get(group, 0)
            if now - last_ts >= group_cooldown:
                alerts[group] = breakout_pairs
                _last_group_alerts[group] = now
                if send_alert_fn:
                    msg = (
                        f"📢 {group} Group Breakout Alert! ({len(breakout_pairs)} pairs)\n"
                        + "\n".join(sorted(breakout_pairs))
                    )
                    send_alert_fn(msg)

    return alerts
