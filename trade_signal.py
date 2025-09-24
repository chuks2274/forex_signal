import asyncio
import logging
import time
from typing import Dict, List, Optional
from currency_strength import run_currency_strength_alert, strength_filter
from config import PAIRS, LOOP_INTERVAL, ALERT_COOLDOWN
from breakout import check_breakout_h1, check_breakout_yesterday
from utils import get_recent_candles, atr, send_alert, load_active_trades, save_active_trades, rsi, calculate_ema

# ---------------- Logger ----------------
logger = logging.getLogger("trade_signal")
logger.setLevel(logging.INFO)
logging.basicConfig(level=logging.INFO)

# ---------------- State ----------------
_ACTIVE_TRADES: List[Dict] = load_active_trades()
_LAST_ALERT_TIME: Dict[str, float] = {}
MIN_RRR = 2.0
MAX_EMA_SPACING = 1.0  # in ATR multiples
ATR_TOUCH_THRESHOLD = 0.5  # price proximity to EMA20

# ---------------- Build Trade Signal ----------------
def build_trade_signal(pair: str, base_val: int, quote_val: int, rank_map: dict, debug: bool = False) -> Optional[Dict]:
    now = time.time()

    # --- Cooldown check ---
    if now - _LAST_ALERT_TIME.get(pair, 0) < ALERT_COOLDOWN:
        if debug:
            logger.info(f"Skipped {pair}: Alert cooldown active")
        return None

    # --- Get candles & RSI ---
    candles_1h = get_recent_candles(pair, "H1", 250)
    if not candles_1h:
        if debug:
            logger.info(f"Skipped {pair}: Missing H1 candles")
        return None

    closes = [float(c["close"]) for c in candles_1h]
    h1_rsi_values = rsi(closes)
    if not h1_rsi_values:
        if debug:
            logger.info(f"Skipped {pair}: Cannot calculate H1 RSI")
        return None
    h1_rsi = h1_rsi_values[-1]

    # --- Breakout checks ---
    h1_breakout = check_breakout_h1(pair, candles_1h, rank_map)
    yest_breakout = check_breakout_yesterday(pair, candles_1h, rank_map)
    if h1_breakout:
        breakout_text = "Today Breakout ✅"
    elif yest_breakout:
        breakout_text = "Yesterday Breakout ✅"
    else:
        if debug:
            logger.info(f"Skipped {pair}: No breakout confirmation")
        return None

    # --- Direction & strength ---
    direction = "BUY" if base_val > quote_val else "SELL"
    strong_val, weak_val = (base_val, quote_val) if direction == "BUY" else (quote_val, base_val)

    # --- Indicators ---
    atr_val = atr(candles_1h)
    ema_20 = calculate_ema(closes, period=20)
    ema_200 = calculate_ema(closes, period=200)
    ema_spacing = abs(ema_20 - ema_200)
    ema_spacing_ok = (ema_spacing / atr_val) <= MAX_EMA_SPACING
    price_near_ema20 = abs(closes[-1] - ema_20) <= ATR_TOUCH_THRESHOLD * atr_val

    # --- Flexible Strength Diff rules ---
    strength_diff = abs(strong_val - weak_val)
    strength_ok = False
    strength_rule_used = None

    # BUY logic
    if direction == "BUY":
        if strength_diff in [12, 14] and strong_val > 0 and weak_val < 0:
            strength_ok = True
            strength_rule_used = f"{strong_val:+d}/{weak_val:+d}"
    # SELL logic
    else:
        if strength_diff in [12, 14] and strong_val < 0 and weak_val > 0:
            strength_ok = True
            strength_rule_used = f"{strong_val:+d}/{weak_val:+d}"

    # --- Conditions ---
    conditions = {
        "strength_extreme": strength_ok,
        "rsi": (h1_rsi >= 50 if direction == "BUY" else h1_rsi <= 50),
        "ema_spacing": ema_spacing_ok,
        "price_touch_ema20": price_near_ema20
    }

    if debug:
        logger.info(f"Checking {pair}: {conditions}")

    if not all(conditions.values()):
        if debug:
            logger.info(f"Skipped {pair}: Conditions not met")
        return None

    # --- Entry / SL / TP ---
    entry = closes[-1]
    stop_loss = entry - atr_val if direction == "BUY" else entry + atr_val
    tp1, tp2, tp3 = (
        entry + atr_val * 2, entry + atr_val * 4, entry + atr_val * 6
    ) if direction == "BUY" else (
        entry - atr_val * 2, entry - atr_val * 4, entry - atr_val * 6
    )

    # --- Alert message ---
    symbol = "🟢 BUY" if direction == "BUY" else "🔴 SELL"
    strengths_text = f"{strong_val:+d}, {weak_val:+d}" if direction == "BUY" else f"{weak_val:+d}, {strong_val:+d}"

    alert_msg = (
        f"{symbol} {pair}\n"
        f"Strength Diff: {strength_diff} ({strength_rule_used}) | Strengths: {strengths_text}\n"
        f"H1 RSI: {h1_rsi:.1f} | EMA Spacing (20 vs 200): {ema_spacing:.5f} "
        f"({ema_spacing/atr_val:.2f} ATR)\n"
        f"Breakout: {breakout_text}\n"
        f"Entry: {entry:.5f} | SL: {stop_loss:.5f} | ATR: {atr_val:.5f}\n"
        f"TPs: TP1:{tp1:.5f}, TP2:{tp2:.5f}, TP3:{tp3:.5f} | Min RRR:1:{MIN_RRR}"
    )
    send_alert(alert_msg)
    _LAST_ALERT_TIME[pair] = now

    # --- Store trade info ---
    trade_info = {
        "pair": pair,
        "direction": direction,
        "entry": entry,
        "stop_loss": stop_loss,
        "take_profit_levels": [tp1, tp2, tp3],
        "strength_diff": strength_diff,
        "strength_rule_used": strength_rule_used,
        "h1_rsi": h1_rsi,
        "ema_spacing": ema_spacing,
        "ema_spacing_atr": ema_spacing / atr_val,
        "breakout_text": breakout_text,
        "time": now
    }
    _ACTIVE_TRADES.append(trade_info)
    logger.info(alert_msg.replace("\n", " | "))

    return trade_info

# ---------------- Async Trade Loop ----------------
async def run_trade_signal_loop_async(debug: bool = False):
    logger.info("📡 Async Trade Signal Loop Started")
    last_trade_alert_times: Dict = {}

    while True:
        try:
            rank_map, _ = run_currency_strength_alert(last_trade_alert_times=last_trade_alert_times)
            if not rank_map:
                await asyncio.sleep(LOOP_INTERVAL)
                continue

            candidate_pairs = []
            for pair in PAIRS:
                if "_" not in pair:
                    continue
                base, quote = pair.split("_")
                base_val, quote_val = rank_map.get(base), rank_map.get(quote)
                if base_val is None or quote_val is None:
                    if debug:
                        logger.info(f"Skipped {pair}: Missing strength values")
                    continue
                candidate_pairs.append((abs(base_val - quote_val), pair, base_val, quote_val))

            # Trigger only top candidate
            for _, pair, base_val, quote_val in sorted(candidate_pairs, reverse=True, key=lambda x: x[0]):
                trade_info = build_trade_signal(pair, base_val, quote_val, rank_map, debug=debug)
                if trade_info:
                    break
                else:
                    if debug:
                        logger.info(f"❌ Skipped {pair}")

            save_active_trades(_ACTIVE_TRADES)
            await asyncio.sleep(LOOP_INTERVAL)

        except Exception as e:
            logger.error(f"Unexpected error in trade loop: {e}", exc_info=True)
            await asyncio.sleep(5)

# ---------------- Entry Point ----------------
if __name__ == "__main__":
    asyncio.run(run_trade_signal_loop_async(debug=True))
