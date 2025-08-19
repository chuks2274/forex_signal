import logging
import datetime
import time
from utils import get_recent_candles, send_telegram
from config import PAIRS, ALERT_COOLDOWN

# --- Configure logging ---
logger = logging.getLogger("breakout")
logger.setLevel(logging.WARNING)
logging.basicConfig(format='%(asctime)s [%(levelname)s] %(message)s')

# --- Define currency groups, filtered to PAIRS ---
RAW_CURRENCY_GROUPS = {
    "USD": ["EUR_USD", "GBP_USD", "USD_JPY", "AUD_USD", "NZD_USD", "USD_CAD", "USD_CHF"],
    "EUR": ["EUR_USD", "EUR_GBP", "EUR_JPY", "EUR_AUD", "EUR_CAD", "EUR_NZD"],
    "GBP": ["GBP_USD", "EUR_GBP", "GBP_JPY", "GBP_AUD", "GBP_CAD", "GBP_NZD"],
    "JPY": ["USD_JPY", "EUR_JPY", "GBP_JPY", "AUD_JPY", "NZD_JPY", "CAD_JPY", "CHF_JPY"],
    "AUD": ["AUD_USD", "EUR_AUD", "GBP_AUD", "AUD_JPY", "AUD_NZD", "AUD_CAD", "AUD_CHF"],
    "NZD": ["NZD_USD", "EUR_NZD", "GBP_NZD", "AUD_NZD", "NZD_JPY", "NZD_CAD", "NZD_CHF"],
    "CAD": ["USD_CAD", "EUR_CAD", "GBP_CAD", "AUD_CAD", "NZD_CAD", "CAD_JPY", "CAD_CHF"],
    "CHF": ["USD_CHF", "EUR_CHF", "GBP_CHF", "AUD_CHF", "NZD_CHF", "CAD_CHF", "CHF_JPY"],
}

CURRENCY_GROUPS = {cur: [p for p in pairs if p in PAIRS] for cur, pairs in RAW_CURRENCY_GROUPS.items()}

# --- Persistent alert storage ---
last_group_alerts = {}  # {currency: float timestamp} persists across restarts

# --- Breakout functions ---
def check_breakout_yesterday(pair):
    """Return True if current price breaks yesterday's high/low."""
    try:
        daily_candles = get_recent_candles(pair, "D", 2)
        if not daily_candles or len(daily_candles) < 2:
            return False

        prev_high = float(daily_candles[-2]['mid']['h'])
        prev_low = float(daily_candles[-2]['mid']['l'])

        h1_candles = get_recent_candles(pair, "H1", 1)
        if not h1_candles:
            return False

        current_price = float(h1_candles[-1]['mid']['c'])
        return current_price > prev_high or current_price < prev_low

    except Exception as e:
        logger.error(f"Error checking breakout for {pair}: {e}")
        return False

def check_breakout_h1(pair):
    """Return True if the latest H1 close breaks above the previous candle's high or below its low."""
    try:
        candles = get_recent_candles(pair, "H1", 2)
        if not candles or len(candles) < 2:
            return False

        prev_high = float(candles[-2]['mid']['h'])
        prev_low = float(candles[-2]['mid']['l'])
        last_close = float(candles[-1]['mid']['c'])

        return last_close > prev_high or last_close < prev_low

    except Exception as e:
        logger.error(f"[H1] {pair} - Error: {e}")
        return False

def run_group_breakout_alert(min_pairs=3):
    """
    Checks all currencies for group breakouts with ALERT_COOLDOWN.
    Returns a dict of {currency: [pairs_that_triggered_alert]}.
    No news alerts are triggered here; news is only sent via trade signals.
    """
    global last_group_alerts  # always refer to module-level persistent dict

    now_ts = time.time()
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    group_alerts = {}

    for group, pairs in CURRENCY_GROUPS.items():
        breakout_pairs = [pair for pair in pairs if check_breakout_yesterday(pair)]
        if len(breakout_pairs) >= min_pairs:
            last_alert_ts = last_group_alerts.get(group, 0)

            if now_ts - last_alert_ts >= ALERT_COOLDOWN:
                alert_msg = (
                    f"📢 {group} Group Breakout Alert! ({len(breakout_pairs)} pairs) - "
                    f"{now_utc.strftime('%Y-%m-%d %H:%M UTC')}\n\n"
                    + "\n".join(sorted(breakout_pairs))
                )
                if send_telegram(alert_msg):
                    logger.warning(f"✅ Sent {group} breakout alert: {', '.join(breakout_pairs)}")
                    group_alerts[group] = breakout_pairs
                    last_group_alerts[group] = now_ts  # persist timestamp

    return group_alerts

# --- Standalone test ---
if __name__ == "__main__":
    logger.warning("Standalone breakout test starting...")
    while True:
        breakout_results = run_group_breakout_alert(min_pairs=3)
        if breakout_results:
            for currency, pairs in breakout_results.items():
                logger.warning(f"Standalone test alert sent: {currency} -> {', '.join(pairs)}")
        else:
            logger.warning("Standalone test: No breakout alerts detected.")
        time.sleep(60)