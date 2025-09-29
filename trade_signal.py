import asyncio
import logging
import time
from typing import Dict, List, Optional
from currency_strength import run_currency_strength_alert, strength_filter
from config import PAIRS, LOOP_INTERVAL, ALERT_COOLDOWN
from breakout import detect_h4_support_resistance, check_h4_breakout
from utils import get_recent_candles, atr, send_alert, load_active_trades, save_active_trades, rsi, calculate_ema

# ---------------- Logger ----------------
logger = logging.getLogger("trade_signal")
logger.setLevel(logging.INFO)
logging.basicConfig(level=logging.INFO)

# ---------------- State ----------------
_ACTIVE_TRADES: List[Dict] = load_active_trades()
_LAST_ALERT_TIME: Dict[str, float] = {}
MIN_RRR = 2.0
ATR_TOUCH_THRESHOLD = 0.5  # Price proximity to ATR for entry confirmation

# ---------------- Build Trade Signal ----------------
def build_trade_signal(pair: str, base_val: int, quote_val: int, rank_map: dict, debug: bool = False) -> Optional[Dict]:
    now = time.time()

    # Cooldown check
    if now - _LAST_ALERT_TIME.get(pair, 0) < ALERT_COOLDOWN:
        if debug:
            logger.info(f"Skipped {pair}: Alert cooldown active")
        return None

    # H4 candles
    candles_h4 = get_recent_candles(pair, "H4", 50)
    if not candles_h4:
        if debug:
            logger.info(f"Skipped {pair}: Missing H4 candles")
        return None

    closes_h4 = [float(c["close"]) for c in candles_h4]
    h4_rsi_values = rsi(closes_h4)
    h4_rsi = h4_rsi_values[-1] if h4_rsi_values else None

    # Detect H4 support/resistance
    support, resistance = detect_h4_support_resistance(pair, candles_h4)
    current_price = closes_h4[-1]

    # Check breakout signal
    breakout_info = check_h4_breakout(pair, candles_h4)
    if not breakout_info:
        if debug:
            logger.info(f"Skipped {pair}: No H4 breakout")
        return None
    level, signal = breakout_info  # signal = "BUY" or "SELL"

    # Daily trend confirmation
    daily_candles = get_recent_candles(pair, "D", 50)
    if not daily_candles:
        if debug:
            logger.info(f"Skipped {pair}: Missing daily candles")
        return None
    daily_closes = [c["close"] for c in daily_candles]
    ema_200_daily = calculate_ema(daily_closes, 200)
    ema_200_slope = ema_200_daily - calculate_ema(daily_closes[:-1], 200)

    # Trend filter
    if signal == "BUY" and current_price < ema_200_daily:
        if debug:
            logger.info(f"Skipped {pair}: Daily trend not bullish")
        return None
    if signal == "SELL" and current_price > ema_200_daily:
        if debug:
            logger.info(f"Skipped {pair}: Daily trend not bearish")
        return None

    # Currency strength confirmation
    base, quote = pair.split("_")
    base_strength = rank_map.get(base, 0)
    quote_strength = rank_map.get(quote, 0)

    if signal == "BUY" and not (base_strength > quote_strength):
        if debug:
            logger.info(f"Skipped {pair}: Currency strength not aligned for BUY")
        return None
    if signal == "SELL" and not (base_strength < quote_strength):
        if debug:
            logger.info(f"Skipped {pair}: Currency strength not aligned for SELL")
        return None

    # ---------------- Entry / SL / TP ----------------
    atr_val = atr(candles_h4)
    entry = current_price
    stop_loss = entry - atr_val if signal == "BUY" else entry + atr_val
    tp1, tp2, tp3 = (
        entry + atr_val * 2, entry + atr_val * 4, entry + atr_val * 6
    ) if signal == "BUY" else (
        entry - atr_val * 2, entry - atr_val * 4, entry - atr_val * 6
    )

    # Send Alert
    symbol = "🟢 BUY" if signal == "BUY" else "🔴 SELL"
    strengths_text = f"{base_strength:+d}, {quote_strength:+d}" if signal == "BUY" else f"{quote_strength:+d}, {base_strength:+d}"
    alert_msg = (
        f"{symbol} {pair}\n"
        f"Strength Diff: {abs(base_strength - quote_strength)} | Strengths: {strengths_text}\n"
        f"H4 RSI: {h4_rsi:.1f} | Breakout Signal: {signal}\n"
        f"Entry: {entry:.5f} | SL: {stop_loss:.5f} | ATR: {atr_val:.5f}\n"
        f"TPs: TP1:{tp1:.5f}, TP2:{tp2:.5f}, TP3:{tp3:.5f} | Min RRR:1:{MIN_RRR}"
    )
    send_alert(alert_msg)
    _LAST_ALERT_TIME[pair] = now

    trade_info = {
        "pair": pair,
        "direction": signal,
        "entry": entry,
        "stop_loss": stop_loss,
        "take_profit_levels": [tp1, tp2, tp3],
        "strength_diff": abs(base_strength - quote_strength),
        "h4_rsi": h4_rsi,
        "breakout_signal": signal,
        "time": now
    }
    _ACTIVE_TRADES.append(trade_info)
    logger.info(alert_msg.replace("\n", " | "))
    return trade_info

# ---------------- Async Trade Loop ----------------
async def run_trade_signal_loop_async(debug: bool = False):
    logger.info("📡 Async H4 Trade Signal Loop Started")
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
