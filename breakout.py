import logging
import datetime
import requests
from utils import get_recent_candles, send_telegram
from config import PAIRS, ALERT_COOLDOWN, OANDA_API, OANDA_TOKEN, OANDA_ACCOUNT

# --- Configure logging ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

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
            logging.info(f"{pair} - Not enough daily candles to check breakout.")
            return False

        prev_high = float(daily_candles[-2]['mid']['h'])
        prev_low = float(daily_candles[-2]['mid']['l'])

        h1_candles = get_recent_candles(pair, "H1", 1)
        if not h1_candles:
            logging.info(f"{pair} - No H1 candles to check current price.")
            return False

        current_price = float(h1_candles[-1]['mid']['c'])
        breakout = current_price > prev_high or current_price < prev_low
        logging.info(f"{pair} - Current: {current_price}, Yesterday H/L: {prev_high}/{prev_low}, Breakout: {breakout}")
        return breakout

    except Exception as e:
        logging.error(f"Error checking breakout for {pair}: {e}")
        return False

def check_breakout_h1(pair):
    """Check if H1 breakout happened."""
    try:
        candles = get_recent_candles(pair, "H1", 1)
        if not candles:
            logging.info(f"[H1] {pair} - No H1 candles available")
            return False
        current_price = float(candles[-1]['mid']['c'])
        high = float(candles[-1]['mid']['h'])
        low = float(candles[-1]['mid']['l'])
        breakout = current_price > high or current_price < low
        logging.info(f"[H1] {pair} - Current: {current_price}, H/L: {high}/{low}, Breakout: {breakout}")
        return breakout
    except Exception as e:
        logging.error(f"[H1] {pair} - Error: {e}")
        return False

def run_group_breakout_alert(last_group_alerts, min_pairs=3):
    """Send alert once per currency group per day if enough pairs break yesterday's high/low."""
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    today_date = now_utc.date()
    logging.info("Checking group breakout alerts...")

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
                    logging.info(f"✅ Sent {group} breakout alert: {', '.join(breakout_pairs)}")
                    last_group_alerts[group] = today_date
                else:
                    logging.warning(f"⚠️ Failed to send {group} breakout alert.")
        else:
            logging.info(f"{group} - Not enough breakouts ({len(breakout_pairs)}) to send alert.")

    logging.info("Group breakout check completed.")
    return last_group_alerts

# --- OANDA market check ---
def is_market_open_oanda(instrument="EUR_USD"):
    """Check if OANDA market is open for a given instrument."""
    url = f"{OANDA_API}/accounts/{OANDA_ACCOUNT}/instruments/{instrument}/candles"
    headers = {"Authorization": f"Bearer {OANDA_TOKEN}"}
    params = {"count": 1, "granularity": "H1"}

    try:
        response = requests.get(url, headers=headers, params=params, timeout=10)
        now_utc = datetime.datetime.now(datetime.timezone.utc)

        if response.status_code == 200:
            data = response.json()
            candles = data.get("candles", [])
            if candles:
                logging.info(f"[{now_utc.isoformat()}] OANDA market open for {instrument}")
                return True
            else:
                logging.info(f"[{now_utc.isoformat()}] No recent candles for {instrument}, market likely closed")
        else:
            logging.warning(f"[{now_utc.isoformat()}] OANDA API response {response.status_code} for {instrument}")
        return False

    except Exception as e:
        logging.error(f"[{datetime.datetime.now(datetime.timezone.utc).isoformat()}] Error checking OANDA market: {e}")
        return False

# --- Standalone test ---
if __name__ == "__main__":
    logging.info("Standalone breakout test starting...")
    last_breakout_alert_times = {}
    breakout_pairs = []

    for pair in PAIRS:
        try:
            yest_breakout = check_breakout_yesterday(pair)
            h1_breakout = check_breakout_h1(pair)
            logging.info(f"{pair} - Yesterday breakout: {yest_breakout}, H1 breakout: {h1_breakout}")
            if yest_breakout or h1_breakout:
                last_breakout_alert_times[pair] = datetime.datetime.now(datetime.timezone.utc)
                breakout_pairs.append(pair)
        except Exception as e:
            logging.error(f"Error checking breakout for {pair}: {e}")

    logging.info(f"Standalone breakout test completed. Pairs detected: {breakout_pairs}")