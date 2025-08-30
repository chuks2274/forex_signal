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
MAX_EMA_SPACING = 1.0  # adjust per pair volatility
ATR_TOUCH_THRESHOLD = 0.5  # price proximity to EMA20

# ---------------- Build Trade Signal ----------------
def build_trade_signal(pair: str, base_val: int, quote_val: int, rank_map: dict, debug: bool = False) -> Optional[Dict]:
    now = time.time()

    # Cooldown check
    if now - _LAST_ALERT_TIME.get(pair, 0) < ALERT_COOLDOWN:
        if debug:
            logger.info(f"Skipped {pair}: Alert cooldown active")
        return None

    # H1 candles & RSI
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

    # H1 breakout check
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

    # Direction and strength
    direction = "BUY" if base_val > quote_val else "SELL"
    strong_val, weak_val = (base_val, quote_val) if direction == "BUY" else (quote_val, base_val)

    # ---------------- All Conditions ----------------
    atr_val = atr(candles_1h)
    ema_20 = calculate_ema(closes, period=20)
    ema_200 = calculate_ema(closes, period=200)
    ema_spacing = abs(ema_20 - ema_200)

    price_near_ema20 = abs(closes[-1] - ema_20) <= ATR_TOUCH_THRESHOLD * atr_val

    conditions = {
        "strength_diff": strength_filter(strong_val, weak_val),
        "rsi": (h1_rsi >= 50 if direction == "BUY" else h1_rsi <= 50),
        "ema_spacing": ema_spacing <= MAX_EMA_SPACING,
        "price_touch_ema20": price_near_ema20
    }

    # Debug logging
    if debug:
        logger.info(f"Checking {pair}: {conditions}")

    # Check all conditions
    if not all(conditions.values()):
        if debug:
            logger.info(f"Skipped {pair}: Conditions not met")
        return None

    # ---------------- Entry / SL / TP ----------------
    entry = closes[-1]
    stop_loss = entry - atr_val if direction == "BUY" else entry + atr_val
    tp1, tp2, tp3 = (
        entry + atr_val * 2, entry + atr_val * 4, entry + atr_val * 6
    ) if direction == "BUY" else (
        entry - atr_val * 2, entry - atr_val * 4, entry - atr_val * 6
    )

    # ---------------- Send Alert ----------------
    symbol = "🟢 BUY" if direction == "BUY" else "🔴 SELL"

    # Order strengths correctly depending on direction
    if direction == "BUY":
        strengths_text = f"{strong_val:+d}, {weak_val:+d}"
    else:  # SELL
        strengths_text = f"{weak_val:+d}, {strong_val:+d}"

    alert_msg = (
        f"{symbol} {pair}\n"
        f"Strength Diff: {abs(strong_val - weak_val)} | Strengths: {strengths_text}\n"
        f"H1 RSI: {h1_rsi:.1f} | EMA Spacing (20 vs 200): {ema_spacing:.5f}\n"
        f"Breakout: {breakout_text}\n"
        f"Entry: {entry:.5f} | SL: {stop_loss:.5f} | ATR: {atr_val:.5f}\n"
        f"TPs: TP1:{tp1:.5f}, TP2:{tp2:.5f}, TP3:{tp3:.5f} | Min RRR:1:{MIN_RRR}"
    )
    send_alert(alert_msg)
    _LAST_ALERT_TIME[pair] = now

    # Store trade info
    trade_info = {
        "pair": pair,
        "direction": direction,
        "entry": entry,
        "stop_loss": stop_loss,
        "take_profit_levels": [tp1, tp2, tp3],
        "strength_diff": abs(strong_val - weak_val),
        "h1_rsi": h1_rsi,
        "ema_spacing": ema_spacing,
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
