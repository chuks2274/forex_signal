import logging
import time
from config import PAIRS, OANDA_API, HEADERS
from utils import get_recent_candles, rsi, ema_slope, atr, send_telegram

# Configure logging for module use
logging.basicConfig(level=logging.INFO)
CURRENCIES = ["EUR", "GBP", "USD", "JPY", "CHF", "AUD", "NZD", "CAD"]

def calculate_strength(oanda_api, headers):
    """Calculate currency strength scores and rank currencies."""
    scores = {c: [] for c in CURRENCIES}

    for pair in PAIRS:
        base, quote = pair.split("_")

        # Get recent H4 candles
        candles = get_recent_candles(pair, granularity="H4", count=20)
        if not candles:
            logging.warning(f"No candles returned for {pair}")
            continue

        closes = [float(c["mid"]["c"]) for c in candles]

        # Price % change
        price_change = ((closes[-1] - closes[-2]) / closes[-2]) * 100 if len(closes) >= 2 else 0

        # RSI
        rsi_values = rsi(closes)
        rsi_val = rsi_values[-1] if rsi_values else 0

        # EMA slope
        ema_trend = ema_slope(closes)

        # ATR
        atr_val = atr(candles) or 0

        # Weighted score
        w_price, w_rsi, w_ema, w_atr = 0.4, 0.3, 0.2, 0.1
        norm_rsi = (rsi_val - 50) / 50

        score_base = w_price * price_change + w_rsi * norm_rsi * 100 + w_ema * ema_trend * 100 + w_atr * atr_val

        scores[base].append(score_base)
        scores[quote].append(-score_base)

    # Compute average scores
    avg_scores = {}
    for cur, vals in scores.items():
        if vals:
            try:
                avg_scores[cur] = sum(vals) / len(vals)
            except Exception as e:
                logging.error(f"Error calculating average for {cur}: {e}")

    # Rank from +7 strongest to -7 weakest
    sorted_scores = sorted(avg_scores.items(), key=lambda x: x[1], reverse=True)
    n = len(CURRENCIES)
    rank_map = {}
    for idx, (cur, _) in enumerate(sorted_scores):
        rank = 7 - (idx * 14) / (n - 1)
        rank_map[cur] = int(round(rank))

    return rank_map

def format_strength_alert(rank_map):
    """Return a nicely formatted currency strength alert string."""
    msg = "📊 Currency Strength Alert 📊\n"
    msg += "Currency Strength Rankings (from +7 strongest to -7 weakest):\n"
    for cur, rank in sorted(rank_map.items(), key=lambda x: x[1], reverse=True):
        sign = "+" if rank > 0 else ""
        msg += f"{cur}: {sign}{rank}\n"
    return msg

def run_currency_strength_alert(oanda_api, headers, last_alert_time=None, cooldown=14400):
    """
    Runs the currency strength alert if cooldown has passed.
    Cooldown default = 4 hours (14400 seconds)
    """
    now_ts = time.time()
    if last_alert_time and (now_ts - last_alert_time) < cooldown:
        remaining = cooldown - (now_ts - last_alert_time)
        logging.info(f"Skipping currency strength alert. Cooldown remaining: {remaining/60:.1f} minutes")
        return last_alert_time

    rank_map = calculate_strength(oanda_api, headers)
    if not rank_map:
        logging.warning("No rank map calculated for currency strength alert")
        return last_alert_time

    alert_msg = format_strength_alert(rank_map)

    if send_telegram(alert_msg):
        logging.info("Sent currency strength alert")
        return now_ts

    logging.warning("Failed to send currency strength alert")
    return last_alert_time

# ===================== Standalone test =====================
if __name__ == "__main__":
    last_strength_alert_time = None
    STRENGTH_ALERT_COOLDOWN = 14400  # 4 hours

    logging.info("Currency Strength bot standalone test started!")

    while True:
        try:
            last_strength_alert_time = run_currency_strength_alert(
                oanda_api=OANDA_API,
                headers=HEADERS,
                last_alert_time=last_strength_alert_time,
                cooldown=STRENGTH_ALERT_COOLDOWN
            )
            time.sleep(60)
        except Exception as e:
            logging.error(f"Unexpected error in standalone loop: {e}")
            time.sleep(60)