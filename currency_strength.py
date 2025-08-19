import logging
import time
from config import PAIRS, OANDA_API, HEADERS, STRENGTH_ALERT_COOLDOWN
from utils import get_recent_candles, rsi, ema_slope, atr, send_telegram
from forex_news_alert import send_news_alert_for_trade  # news alert function

# Configure logging
logger = logging.getLogger("forex_bot")
logging.basicConfig(level=logging.INFO)

# All currencies to track
CURRENCIES = ["EUR", "GBP", "USD", "JPY", "CHF", "AUD", "NZD", "CAD"]

# ===================== Core strength calculation =====================
def calculate_strength(oanda_api, headers):
    """Calculate currency strength scores and map to +7 … -7, skipping 0."""
    scores = {c: [] for c in CURRENCIES}

    for pair in PAIRS:
        if "_" not in pair:
            logger.warning(f"Invalid pair format skipped: {pair}")
            continue

        base, quote = pair.split("_")
        if base not in CURRENCIES or quote not in CURRENCIES:
            continue

        candles = get_recent_candles(pair, granularity="H4", count=20)
        if not candles:
            logger.warning(f"No candles returned for {pair}")
            continue

        closes = [float(c["mid"]["c"]) for c in candles]
        price_change = ((closes[-1] - closes[-2]) / closes[-2]) * 100 if len(closes) >= 2 else 0
        rsi_values = rsi(closes)
        rsi_val = rsi_values[-1] if rsi_values else 0
        ema_trend = ema_slope(closes)
        atr_val = atr(candles) or 0

        w_price, w_rsi, w_ema, w_atr = 0.4, 0.3, 0.2, 0.1
        norm_rsi = (rsi_val - 50) / 50
        score_base = w_price * price_change + w_rsi * norm_rsi * 100 + w_ema * ema_trend * 100 + w_atr * atr_val

        scores[base].append(score_base)
        scores[quote].append(-score_base)

    # Calculate average scores
    avg_scores = {}
    for cur, vals in scores.items():
        if vals:
            try:
                avg_scores[cur] = sum(vals) / len(vals)
            except Exception as e:
                logger.error(f"Error calculating average for {cur}: {e}")

    # Sort descending
    sorted_scores = sorted(avg_scores.items(), key=lambda x: x[1], reverse=True)

    # Map ranks linearly from +7 to -7, skipping 0
    n = len(sorted_scores)
    max_rank = 7
    min_rank = -7
    rank_map = {}
    for idx, (cur, _) in enumerate(sorted_scores):
        # Linear mapping from max to min
        rank = max_rank - (idx * (max_rank - min_rank) / (n - 1))
        rank = int(round(rank))
        # Skip 0
        if rank == 0:
            rank = 1 if idx < n / 2 else -1
        rank_map[cur] = rank

    return rank_map

# ===================== Format alert =====================
def format_strength_alert(rank_map):
    msg = "📊 Currency Strength Alert 📊\n"
    msg += "Currency Strength Rankings (from +7 strongest to -7 weakest):\n"
    for cur, rank in sorted(rank_map.items(), key=lambda x: x[1], reverse=True):
        sign = "+" if rank > 0 else ""
        msg += f"{cur}: {sign}{rank}\n"
    return msg

# ===================== Alert function with +5/+6/+7 / -5/-6/-7 filter =====================
def run_currency_strength_alert(oanda_api, headers, last_alert_time=None,
                                cooldown=STRENGTH_ALERT_COOLDOWN):
    now_ts = time.time()
    last_ts = last_alert_time or 0

    if now_ts - last_ts < cooldown:
        remaining = cooldown - (now_ts - last_ts)
        logger.info(f"Skipping currency strength alert. Cooldown remaining: {remaining/60:.1f} minutes")
        return {}, last_ts

    try:
        rank_map = calculate_strength(oanda_api, headers)
        if not rank_map:
            logger.warning("No rank map calculated for currency strength alert")
            return {}, last_ts

        # Full alert message includes all currencies
        alert_msg = format_strength_alert(rank_map)
        if send_telegram(alert_msg):
            logger.info("✅ Sent full currency strength alert")

        # --- Filter only +5/+6/+7 and -5/-6/-7 ---
        filtered_currencies = {cur: val for cur, val in rank_map.items() if abs(val) >= 5}

        if filtered_currencies:
            pairs_to_check = [pair for pair in PAIRS
                              if pair[:3] in filtered_currencies or pair[4:] in filtered_currencies]
            for pair in pairs_to_check:
                send_news_alert_for_trade(pair)

            logger.info(f"✅ Sent trade/news alerts for pairs: {pairs_to_check}")

        return filtered_currencies, now_ts

    except Exception as e:
        logger.error(f"Error in run_currency_strength_alert: {e}", exc_info=True)
        return {}, last_ts

# ===================== Standalone test =====================
if __name__ == "__main__":
    last_strength_alert_time = None
    logger.info("Currency Strength bot standalone test started!")

    while True:
        try:
            alerted_currencies, last_strength_alert_time = run_currency_strength_alert(
                oanda_api=OANDA_API,
                headers=HEADERS,
                last_alert_time=last_strength_alert_time,
                cooldown=STRENGTH_ALERT_COOLDOWN
            )
            logger.info(f"Alerted currencies this cycle: {alerted_currencies}")
            time.sleep(60)
        except Exception as e:
            logger.error(f"Unexpected error in standalone loop: {e}")
            time.sleep(60)

# ===================== Helper to get latest strengths =====================
def get_currency_strength():
    try:
        return calculate_strength(OANDA_API, HEADERS)
    except Exception as e:
        logger.error(f"Failed to get currency strength: {e}")
        return {}