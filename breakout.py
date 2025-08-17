import logging
import datetime
import requests
from utils import get_recent_candles, send_telegram
from config import PAIRS, ALERT_COOLDOWN, OANDA_API, OANDA_TOKEN, OANDA_ACCOUNT

# --- Configure logging ---
logger = logging.getLogger("breakout")
logger.setLevel(logging.WARNING)  # only warnings/errors
logging.basicConfig(format='%(asctime)s [%(levelname)s] %(message)s')

# --- Define currency groups ---
CURRENCY_GROUPS = {
    "USD": ["EUR_USD", "GBP_USD", "USD_JPY", "AUD_USD", "NZD_USD", "USD_CAD", "USD_CHF"],
    "EUR": ["EUR_USD", "EUR_GBP", "EUR_JPY", "EUR_AUD", "EUR_CAD", "EUR_NZD"],
    "GBP": ["GBP_USD", "EUR_GBP", "GBP_JPY", "GBP_AUD", "GBP_CAD", "GBP_NZD"],
    "JPY": ["USD_JPY", "EUR_JPY", "GBP_JPY", "AUD_JPY", "NZD_JPY", "CAD_JPY", "CHF_JPY"],
    "AUD": ["AUD_USD", "EUR_AUD", "GBP_AUD", "AUD_JPY", "AUD_NZD", "AUD_CAD", "AUD_CHF"],
    "NZD": ["NZD_USD", "EUR_NZD", "GBP_NZD", "AUD_NZD", "NZD_JPY", "NZD_CAD", "NZD_CHF"],
    "CAD": ["USD_CAD", "EUR_CAD", "GBP_CAD", "AUD_CAD", "NZD_CAD", "CAD_JPY", "CAD_CHF"],
    "CHF": ["USD_CHF", "EUR_CHF", "GBP_CHF", "AUD_CHF", "NZD_CHF", "CAD_CHF", "CHF_JPY"],
}

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
    """Return True if current H1 candle is outside its own H/L."""
    try:
        candles = get_recent_candles(pair, "H1", 1)
        if not candles:
            return False
        current_price = float(candles[-1]['mid']['c'])
        high = float(candles[-1]['mid']['h'])
        low = float(candles[-1]['mid']['l'])
        return current_price > high or current_price < low
    except Exception as e:
        logger.error(f"[H1] {pair} - Error: {e}")
        return False

def run_group_breakout_alert(last_group_alerts, min_pairs=3):
    """
    Checks all currencies for group breakouts.
    Returns a dict of {currency: [pairs_that_triggered_alert]}.
    Only pairs that actually meet min_pairs are included.
    """
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    today_date = now_utc.date()
    group_alerts = {}

    for group, pairs in CURRENCY_GROUPS.items():
        breakout_pairs = [pair for pair in pairs if check_breakout_yesterday(pair)]
        if len(breakout_pairs) >= min_pairs:
            last_alert_date = last_group_alerts.get(group)
            if last_alert_date != today_date:
                alert_msg = (
                    f"📢 {group} Group Breakout Alert! ({len(breakout_pairs)} pairs) - "
                    f"{now_utc.strftime('%Y-%m-%d %H:%M UTC')}\n\n"
                    + "\n".join(sorted(breakout_pairs))
                )
                if send_telegram(alert_msg):
                    logger.warning(f"✅ Sent {group} breakout alert: {', '.join(breakout_pairs)}")
                    group_alerts[group] = breakout_pairs
                    last_group_alerts[group] = today_date

    return group_alerts

# --- OANDA market check ---
def is_market_open_oanda(instrument="EUR_USD"):
    """Check if OANDA market is open for a given instrument."""
    url = f"{OANDA_API}/accounts/{OANDA_ACCOUNT}/instruments/{instrument}/candles"
    headers = {"Authorization": f"Bearer {OANDA_TOKEN}"}
    params = {"count": 1, "granularity": "H1"}

    try:
        response = requests.get(url, headers=headers, params=params, timeout=10)
        if response.status_code != 200:
            logger.warning(f"OANDA API response {response.status_code} for {instrument}")
            return False
        data = response.json()
        return bool(data.get("candles", []))
    except Exception as e:
        logger.error(f"Error checking OANDA market: {e}")
        return False

# --- Standalone test ---
if __name__ == "__main__":
    logger.warning("Standalone breakout test starting...")
    last_group_alerts = {}
    breakout_results = run_group_breakout_alert(last_group_alerts, min_pairs=3)
    if breakout_results:
        for currency, pairs in breakout_results.items():
            logger.warning(f"Standalone test alert sent: {currency} -> {', '.join(pairs)}")
    else:
        logger.warning("Standalone test: No breakout alerts detected.")