import asyncio
import logging
import time
from typing import Dict, List, Optional
from currency_strength import run_currency_strength_alert, strength_filter
from config import PAIRS, LOOP_INTERVAL, ALERT_COOLDOWN
from breakout import check_breakout_h1, check_breakout_yesterday
from utils import get_recent_candles, atr, send_alert, load_active_trades, save_active_trades, rsi

# ---------------- Logger ----------------
logger = logging.getLogger("trade_signal")
logger.setLevel(logging.INFO)
logging.basicConfig(level=logging.INFO)

# ---------------- State ----------------
_ACTIVE_TRADES: List[Dict] = load_active_trades()
_LAST_ALERT_TIME: Dict[str, float] = {}
MIN_RRR = 2.0

# ---------------- Build Trade Signal ----------------
def build_trade_signal(pair: str, base_val: int, quote_val: int, rank_map: dict, debug: bool = False) -> Optional[Dict]:
    now = time.time()
    if now - _LAST_ALERT_TIME.get(pair, 0) < ALERT_COOLDOWN:
        if debug:
            logger.info(f"Skipped {pair}: Alert cooldown active")
        return None

    # ---------------- H1 Candles & RSI ----------------
    candles_1h = get_recent_candles(pair, "H1", 50)
    if not candles_1h:
        if debug:
            logger.info(f"Skipped {pair}: Missing H1 candles")
        return None
    h1_close_prices = [float(c["close"]) for c in candles_1h]
    h1_rsi_values = rsi(h1_close_prices)
    if not h1_rsi_values:
        if debug:
            logger.info(f"Skipped {pair}: Cannot calculate H1 RSI")
        return None
    h1_rsi = h1_rsi_values[-1]

    # ---------------- H1 Breakout Confirmation ----------------
    h1_breakout = check_breakout_h1(pair, candles_1h, rank_map)
    yest_breakout = check_breakout_yesterday(pair, candles_1h, rank_map)
    if not (h1_breakout or yest_breakout):
        if debug:
            logger.info(f"Skipped {pair}: No breakout confirmation")
        return None

    # ---------------- Direction ----------------
    direction = "BUY" if base_val > quote_val else "SELL"
    strong_val, weak_val = (base_val, quote_val) if direction == "BUY" else (quote_val, base_val)

    # ---------------- Strength Filter ----------------
    if not strength_filter(strong_val, weak_val):
        if debug:
            logger.info(f"Skipped {pair} ({direction}): Fails strength filter")
        return None

    # ---------------- H1 RSI Confirmation (Neutral at 50) ----------------
    if direction == "BUY" and h1_rsi < 50:
        if debug:
            logger.info(f"Skipped {pair} (BUY): H1 RSI {h1_rsi:.1f} < 50")
        return None
    if direction == "SELL" and h1_rsi > 50:
        if debug:
            logger.info(f"Skipped {pair} (SELL): H1 RSI {h1_rsi:.1f} > 50")
        return None

    # ---------------- ATR ----------------
    atr_val = atr(candles_1h)

    # ---------------- M15 RSI Confirmation ----------------
    m15_candles = get_recent_candles(pair, "M15", 50)
    if not m15_candles:
        if debug:
            logger.info(f"Skipped {pair}: Missing M15 candles")
        return None
    m15_close_prices = [float(c["close"]) for c in m15_candles]
    m15_rsi_values = rsi(m15_close_prices)
    if not m15_rsi_values:
        if debug:
            logger.info(f"Skipped {pair}: Cannot calculate M15 RSI")
        return None
    m15_rsi = m15_rsi_values[-1]

    # ---------------- M15 RSI Trigger ----------------
    if direction == "BUY" and m15_rsi > 35:
        if debug:
            logger.info(f"Skipped {pair} (BUY): M15 RSI {m15_rsi:.1f} > 35")
        return None
    if direction == "SELL" and m15_rsi < 65:
        if debug:
            logger.info(f"Skipped {pair} (SELL): M15 RSI {m15_rsi:.1f} < 65")
        return None

    # ---------------- Entry / SL / TP ----------------
    entry = m15_candles[-1]["close"]
    stop_loss = entry - atr_val if direction == "BUY" else entry + atr_val
    tp1, tp2, tp3 = (
        entry + atr_val*2, entry + atr_val*4, entry + atr_val*6
    ) if direction == "BUY" else (
        entry - atr_val*2, entry - atr_val*4, entry - atr_val*6
    )

    # ---------------- Send Alert ----------------
    symbol = "🟢 BUY" if direction == "BUY" else "🔴 SELL"
    alert_msg = f"""{symbol} {pair} [strength_alert]
Strength Diff: {abs(strong_val - weak_val)}
Strengths: {strong_val:+d}, {weak_val:+d}
H1 RSI: {h1_rsi:.1f} | M15 RSI: {m15_rsi:.1f}
Entry: {entry:.5f} | SL: {stop_loss:.5f} | ATR: {atr_val:.5f}
TPs: TP1:{tp1:.5f}, TP2:{tp2:.5f}, TP3:{tp3:.5f} | Min RRR:1:{MIN_RRR}"""
    send_alert(alert_msg)
    _LAST_ALERT_TIME[pair] = now

    trade_info = {
        "pair": pair,
        "direction": direction,
        "entry": entry,
        "stop_loss": stop_loss,
        "take_profit_levels": [tp1, tp2, tp3],
        "strength_diff": abs(strong_val - weak_val),
        "h1_rsi": h1_rsi,
        "m15_rsi": m15_rsi,
        "time": now
    }
    _ACTIVE_TRADES.append(trade_info)
    logger.info(f"Trade triggered: {pair} | Direction: {direction} | Strength Diff: {abs(strong_val - weak_val)} | H1 RSI: {h1_rsi:.1f} | M15 RSI: {m15_rsi:.1f}")
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
                    if debug:
                        logger.info(f"✅ Triggered: {pair} | Direction: {trade_info['direction']} | H1 RSI: {trade_info['h1_rsi']:.1f} | M15 RSI: {trade_info['m15_rsi']:.1f}")
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
