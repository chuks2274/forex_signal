import logging
import time
from utils import get_recent_candles, calculate_ema
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
GROUP_COOLDOWN = 4 * 3600  # 4 hours cooldown

# ----------------- Individual H4 breakout check -----------------
def check_breakout_h4(pair):
    """
    Check if the latest H4 candle breaks recent support/resistance.
    Returns True if breakout occurs, False otherwise.
    """
    try:
        candles_4h = get_recent_candles(pair, "H4", 50)
        if not candles_4h or len(candles_4h) < 2:
            return False

        closes = [float(c["close"]) for c in candles_4h]
        highs = [float(c["high"]) for c in candles_4h]
        lows = [float(c["low"]) for c in candles_4h]

        last_close = closes[-1]

        # Recent H4 high/low
        recent_high = max(highs[-50:])
        recent_low = min(lows[-50:])

        # ---------------- Safe D1 EMA Trend ----------------
        candles_d1 = get_recent_candles(pair, "D1", 250)
        if not candles_d1 or len(candles_d1) < 200:
            d1_trend_up = d1_trend_down = True  # Assume neutral if not enough D1 data
        else:
            closes_d1 = [float(c["close"]) for c in candles_d1]
            ema_200_d1 = calculate_ema(closes_d1, period=200)
            if ema_200_d1 is None:
                d1_trend_up = d1_trend_down = True
            else:
                d1_trend_up = closes_d1[-1] > ema_200_d1
                d1_trend_down = closes_d1[-1] < ema_200_d1

        # Only trigger if breakout aligns with D1 trend
        if last_close > recent_high and d1_trend_up:
            return True
        elif last_close < recent_low and d1_trend_down:
            return True

        return False

    except Exception as e:
        logger.error(f"{pair} H4 breakout check error: {e}")
        return False

# ----------------- Group breakout alert -----------------
def run_group_breakout_alert(min_pairs=4, send_alert_fn=None):
    """
    Check for H4 breakout alerts per currency group.
    Each group has a 4-hour cooldown.
    Returns a dict of groups with breakout pairs.
    """
    global _last_group_alerts
    now = time.time()
    alerts = {}

    for group, pairs in CURRENCY_GROUPS.items():
        breakout_pairs = []

        for pair in pairs:
            try:
                if check_breakout_h4(pair):
                    breakout_pairs.append(pair)
            except Exception as e:
                logger.error(f"{pair} group breakout check error: {e}")

        # Only send alert if enough pairs broke out and cooldown passed
        if len(breakout_pairs) >= min_pairs:
            last_ts = _last_group_alerts.get(group, 0)
            if now - last_ts >= GROUP_COOLDOWN:
                alerts[group] = breakout_pairs
                _last_group_alerts[group] = now
                if send_alert_fn:
                    msg = (
                        f"📢 {group} Group H4 Breakout Alert! ({len(breakout_pairs)} pairs)\n"
                        + "\n".join(sorted(breakout_pairs))
                    )
                    send_alert_fn(msg)

    return alerts
