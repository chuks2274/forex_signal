import logging
import time

from config import PAIRS, STRENGTH_ALERT_COOLDOWN
from utils import get_recent_candles, rsi, ema_slope, atr, send_telegram
from breakout import check_breakout_h1

# Configure logging
logger = logging.getLogger("currency_strength")
logging.basicConfig(level=logging.INFO)

CURRENCIES = ["EUR", "GBP", "USD", "JPY", "CHF", "AUD", "NZD", "CAD"]

# ---------------- Core Strength Calculation ----------------
def calculate_strength():
    """Calculate normalized strength scores for all currencies based on H4 candles."""
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
        rsi_val = rsi(closes)[-1] if rsi(closes) else 0
        ema_trend = ema_slope(closes)
        atr_val = atr(candles) or 0

        # Weighted scoring
        w_price, w_rsi, w_ema, w_atr = 0.4, 0.3, 0.2, 0.1
        norm_rsi = (rsi_val - 50) / 50
        score_base = w_price * price_change + w_rsi * norm_rsi * 100 + w_ema * ema_trend * 100 + w_atr * atr_val

        scores[base].append(score_base)
        scores[quote].append(-score_base)

    # Average and normalize
    avg_scores = {cur: sum(vals)/len(vals) for cur, vals in scores.items() if vals}

    # Map to -7 to +7 scale
    sorted_scores = sorted(avg_scores.items(), key=lambda x: x[1], reverse=True)
    n = len(sorted_scores)
    max_rank, min_rank = 7, -7
    rank_map = {}
    for idx, (cur, _) in enumerate(sorted_scores):
        rank = int(round(max_rank - (idx * (max_rank - min_rank) / (n - 1))))
        if rank == 0:
            rank = 1 if idx < n / 2 else -1
        rank_map[cur] = rank

    return rank_map

# ---------------- Formatting ----------------
def format_strength_alert(rank_map):
    """Return a nicely formatted string for the currency strength alert."""
    msg = "📊 Currency Strength Alert 📊\n"
    msg += "Currency Strength Rankings (+7 strongest → -7 weakest):\n"
    for cur, rank in sorted(rank_map.items(), key=lambda x: x[1], reverse=True):
        sign = "+" if rank > 0 else ""
        msg += f"{cur}: {sign}{rank}\n"
    return msg

# ---------------- Runner ----------------
def run_currency_strength_alert(last_alert_time=None, cooldown=STRENGTH_ALERT_COOLDOWN, last_trade_alert_times=None):
    """Main function to calculate and optionally send currency strength alerts."""
    now_ts = time.time()
    last_ts = last_alert_time or 0

    if now_ts - last_ts < cooldown:
        remaining = cooldown - (now_ts - last_ts)
        logger.info(f"Skipping currency strength alert. Cooldown remaining: {remaining/60:.1f} minutes")
        return {}, last_ts

    try:
        rank_map = calculate_strength()
        if not rank_map:
            return {}, last_ts

        # Send full alert
        alert_msg = format_strength_alert(rank_map)
        if send_telegram(alert_msg):
            logger.info("✅ Sent full currency strength alert")

        # Filter for strong signals (+5/+6/+7 or -5/-6/-7)
        filtered_currencies = {cur: val for cur, val in rank_map.items() if abs(val) >= 5}

        # Optional: send trade alerts for valid pairs
        if filtered_currencies and last_trade_alert_times is not None:
            for pair in PAIRS:
                if "_" not in pair:
                    continue
                base, quote = pair.split("_")
                base_val = filtered_currencies.get(base)
                quote_val = filtered_currencies.get(quote)

                if base_val is None or quote_val is None:
                    continue

                signal_type = None
                if base_val > 0 and quote_val < 0:
                    signal_type = "BUY"
                elif base_val < 0 and quote_val > 0:
                    signal_type = "SELL"
                else:
                    continue

                if not check_breakout_h1(pair):
                    continue

                key = (pair, "strength_alert")
                if key in last_trade_alert_times and now_ts - last_trade_alert_times[key] < cooldown:
                    continue

                last_trade_alert_times[key] = now_ts

        return filtered_currencies, now_ts

    except Exception as e:
        logger.error(f"Error in run_currency_strength_alert: {e}", exc_info=True)
        return {}, last_ts
