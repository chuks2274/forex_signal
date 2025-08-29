import logging
import time
from threading import Lock
from config import PAIRS, STRENGTH_ALERT_COOLDOWN
from utils import get_recent_candles, rsi, ema_slope, atr, send_telegram
from breakout import check_breakout_h1

logger = logging.getLogger("currency_strength")
logger.setLevel(logging.INFO)
logging.basicConfig(level=logging.INFO)

CURRENCIES = ["EUR", "GBP", "USD", "JPY", "CHF", "AUD", "NZD", "CAD"]

# ---------------- Thread-Safe Cooldown ----------------
_strength_alert_lock = Lock()
_last_strength_alert_time = 0

# ---------------- Core Strength Calculation ----------------
def calculate_strength():
    scores = {c: [] for c in CURRENCIES}

    for pair in PAIRS:
        if "_" not in pair:
            continue
        base, quote = pair.split("_")
        if base not in CURRENCIES or quote not in CURRENCIES:
            continue

        candles = get_recent_candles(pair, "H4", 20)
        if not candles:
            continue

        closes = [float(c["close"]) for c in candles]
        price_change = ((closes[-1] - closes[-2]) / closes[-2]) * 100 if len(closes) >= 2 else 0
        rsi_val = rsi(closes)[-1] if rsi(closes) else 0
        ema_trend = ema_slope(closes)
        atr_val = atr(candles) or 0

        w_price, w_rsi, w_ema, w_atr = 0.4, 0.3, 0.2, 0.1
        norm_rsi = (rsi_val - 50) / 50
        score_base = w_price * price_change + w_rsi * norm_rsi * 100 + w_ema * ema_trend * 100 + w_atr * atr_val

        scores[base].append(score_base)
        scores[quote].append(-score_base)

    avg_scores = {cur: sum(vals)/len(vals) for cur, vals in scores.items() if vals}

    sorted_scores = sorted(avg_scores.items(), key=lambda x: x[1], reverse=True)
    n = len(sorted_scores)
    max_rank, min_rank = 7, -7
    rank_map = {}
    for idx, (cur, _) in enumerate(sorted_scores):
        # Round to nearest integer and force no zero
        rank = int(round(max_rank - (idx * (max_rank - min_rank) / (n - 1))))
        if rank == 0:
            rank = 1 if idx < n / 2 else -1
        rank_map[cur] = rank

    # Ensure all values are integers
    for cur in rank_map:
        rank_map[cur] = int(rank_map[cur])

    return rank_map

# ---------------- Formatting ----------------
def format_strength_alert(rank_map):
    msg = "📊 Currency Strength Alert 📊\n"
    msg += "Currency Strength Rankings (+7 strongest → -7 weakest):\n"
    for cur, rank in sorted(rank_map.items(), key=lambda x: x[1], reverse=True):
        sign = "+" if rank > 0 else ""
        msg += f"{cur}: {sign}{rank}\n"
    return msg

# ---------------- Strength Filter ----------------
def strength_filter(strong_val, weak_val):
    return (
        (strong_val == 7 and weak_val == -7) or
        (strong_val == 7 and weak_val == -5) or
        (strong_val == 5 and weak_val == -5) or
        (weak_val == -7 and strong_val == 7) or
        (weak_val == -7 and strong_val == 5) or
        (weak_val == -5 and strong_val == 5)
    )

# ---------------- Runner ----------------
def run_currency_strength_alert(last_trade_alert_times: dict = None):
    global _last_strength_alert_time
    now_ts = time.time()

    with _strength_alert_lock:
        # Cooldown check
        if now_ts - _last_strength_alert_time < STRENGTH_ALERT_COOLDOWN:
            rank_map = calculate_strength()
            return rank_map, _last_strength_alert_time

        try:
            rank_map = calculate_strength()
            if not rank_map:
                return {}, _last_strength_alert_time

            # Send full ranking alert
            alert_msg = format_strength_alert(rank_map)
            if send_telegram(alert_msg):
                logger.info("✅ Sent full currency strength alert")
                _last_strength_alert_time = now_ts

            # Filter for strong/weak currencies
            filtered_currencies = {cur: int(val) for cur, val in rank_map.items() if abs(val) >= 5}

            # Determine top candidate pair
            if filtered_currencies and last_trade_alert_times is not None:
                candidate_pairs = []
                for pair in PAIRS:
                    if "_" not in pair:
                        continue
                    base, quote = pair.split("_")
                    base_val = filtered_currencies.get(base)
                    quote_val = filtered_currencies.get(quote)
                    if base_val is None or quote_val is None:
                        continue

                    strong_val, weak_val = (base_val, quote_val) if abs(base_val) >= abs(quote_val) else (quote_val, base_val)
                    if not strength_filter(strong_val, weak_val):
                        continue

                    # H1 breakout confirmation
                    candles_h1 = get_recent_candles(pair, "H1", 50)
                    if not candles_h1:
                        continue
                    breakout_info = check_breakout_h1(pair, candles_h1, rank_map)
                    if not breakout_info:
                        continue

                    candidate_pairs.append((abs(strong_val - weak_val), pair, base_val, quote_val))

                # Trigger only the top candidate
                candidate_pairs.sort(reverse=True, key=lambda x: x[0])
                if candidate_pairs:
                    _, pair, base_val, quote_val = candidate_pairs[0]
                    signal_type = "BUY" if base_val > 0 and quote_val < 0 else "SELL"
                    key = (pair, "strength_alert")
                    last_pair_ts = last_trade_alert_times.get(key, 0)
                    if now_ts - last_pair_ts >= STRENGTH_ALERT_COOLDOWN:
                        last_trade_alert_times[key] = now_ts
                        logger.info(f"💹 Top Candidate Trade Alert: {signal_type} {pair} | Strength Diff: {abs(base_val - quote_val)}")

            return filtered_currencies, _last_strength_alert_time

        except Exception as e:
            logger.error(f"Error in run_currency_strength_alert: {e}", exc_info=True)
            return {}, _last_strength_alert_time
