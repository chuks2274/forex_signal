import asyncio
import logging
import time
from typing import Dict, List, Optional
from currency_strength import run_currency_strength_alert, strength_filter
from config import PAIRS, LOOP_INTERVAL, ALERT_COOLDOWN
from utils import (
    get_recent_candles, atr, send_alert, load_active_trades, save_active_trades,
    rsi, calculate_ema
)
from breakout import check_breakout_h4

# ---------------- Logger ----------------
logger = logging.getLogger("trade_signal")
logger.setLevel(logging.INFO)
logging.basicConfig(level=logging.INFO)

# ---------------- State ----------------
_ACTIVE_TRADES: List[Dict] = load_active_trades()
_LAST_ALERT_TIME: Dict[str, float] = {}
MIN_RRR = 2.0

# ================= SAFE D1 FETCH =================
def get_safe_d1_candles(pair: str, max_count: int = 50) -> list[dict]:
    """
    Fetch D1 candles safely. Automatically adapts if the market is closed
    and limits the number of requested candles to prevent 400 errors.
    """
    counts_to_try = [max_count, max_count // 2, 10]
    for count in counts_to_try:
        candles = get_recent_candles(pair, "D1", count)
        if candles:
            return candles
        else:
            logger.warning(f"{pair} D1 candles not available with count {count} (market may be closed)")
    logger.warning(f"No D1 candles available for {pair}. Skipping pair.")
    return []

# ---------------- Build Trade Signal ----------------
def build_trade_signal(pair: str, base_val: int, quote_val: int, rank_map: dict, debug: bool = False) -> Optional[Dict]:
    now = time.time()

    # ---------------- Cooldown Check ----------------
    if now - _LAST_ALERT_TIME.get(pair, 0) < ALERT_COOLDOWN:
        if debug:
            logger.info(f"Skipped {pair}: Alert cooldown active")
        return None

    # ---------------- H4 Candles & Indicators ----------------
    candles_4h = get_recent_candles(pair, "H4", 250)
    if not candles_4h or len(candles_4h) < 3:
        if debug:
            logger.info(f"Skipped {pair}: Missing H4 candles")
        return None

    closes = [float(c["close"]) for c in candles_4h]
    highs = [float(c["high"]) for c in candles_4h]
    lows = [float(c["low"]) for c in candles_4h]

    h4_rsi_values = rsi(closes)
    if not h4_rsi_values:
        if debug:
            logger.info(f"Skipped {pair}: Cannot calculate H4 RSI")
        return None
    h4_rsi = h4_rsi_values[-1]

    ema_20 = calculate_ema(closes, period=20)
    ema_200 = calculate_ema(closes, period=200)
    ema_slope_val = closes[-1] - closes[-2]

    h4_bullish = closes[-1] > ema_200 and ema_slope_val > 0
    h4_bearish = closes[-1] < ema_200 and ema_slope_val < 0

    # ---------------- D1 Trend Confirmation (Safe) ----------------
    candles_d1 = get_safe_d1_candles(pair, max_count=50)
    if not candles_d1 or len(candles_d1) < 2:
        if debug:
            logger.info(f"Skipped {pair}: Not enough D1 candles (market closed or unavailable)")
        ema_200_d1 = None
        d1_trend_up = d1_trend_down = True
    else:
        closes_d1 = [float(c["close"]) for c in candles_d1]
        ema_200_d1 = calculate_ema(closes_d1, period=200)
        d1_trend_up = closes_d1[-1] > ema_200_d1
        d1_trend_down = closes_d1[-1] < ema_200_d1

    # ---------------- Direction and Strength ----------------
    direction = "BUY" if base_val > quote_val else "SELL"
    strong_val, weak_val = (base_val, quote_val) if direction == "BUY" else (quote_val, base_val)

    # ---------------- H4 Candle Pattern ----------------
    h4_candle_bullish_engulfing = closes[-1] > closes[-2] and closes[-2] < closes[-3]
    h4_candle_bearish_engulfing = closes[-1] < closes[-2] and closes[-2] > closes[-3]
    candle_ok = (direction == "BUY" and h4_candle_bullish_engulfing) or (direction == "SELL" and h4_candle_bearish_engulfing)

    # ---------------- H4 Breakout Check ----------------
    h4_breakout = check_breakout_h4(pair)

    # ---------------- Strength Filter ----------------
    strength_ok = (direction == "BUY" and base_val > quote_val) or (direction == "SELL" and quote_val > base_val)
    strength_diff = strength_filter(strong_val, weak_val)

    # ---------------- All Conditions ----------------
    conditions = {
        "candle_or_breakout": candle_ok or bool(h4_breakout),
        "h4_trend": (direction == "BUY" and h4_bullish) or (direction == "SELL" and h4_bearish),
        "d1_trend": d1_trend_up if direction == "BUY" else d1_trend_down,
        "strength_ok": strength_ok,
        "strength_diff": strength_diff,
        "rsi": (h4_rsi >= 50 if direction == "BUY" else h4_rsi <= 50)
    }

    if debug:
        logger.info(f"Checking {pair}: {conditions} | Candle: {candle_ok} | Breakout: {h4_breakout}")

    if not all(conditions.values()):
        if debug:
            logger.info(f"Skipped {pair}: Conditions not met")
        return None

    # ---------------- Entry / SL / TP ----------------
    atr_val = atr(candles_4h)
    entry = closes[-1]
    stop_loss = entry - atr_val if direction == "BUY" else entry + atr_val
    tp1, tp2, tp3 = (
        entry + atr_val * 2, entry + atr_val * 4, entry + atr_val * 6
    ) if direction == "BUY" else (
        entry - atr_val * 2, entry - atr_val * 4, entry - atr_val * 6
    )

    # ---------------- Send Alert ----------------
    symbol = "🟢 BUY" if direction == "BUY" else "🔴 SELL"
    strengths_text = f"{strong_val:+d}, {weak_val:+d}" if direction == "BUY" else f"{weak_val:+d}, {strong_val:+d}"
    breakout_text = "H4 Breakout ✅" if h4_breakout else "No Breakout"
    alert_msg = (
        f"{symbol} {pair}\n"
        f"Strength Diff: {abs(strong_val - weak_val)} | Strengths: {strengths_text}\n"
        f"H4 RSI: {h4_rsi:.1f} | Candle OK: {candle_ok} | {breakout_text}\n"
        f"H4 Trend OK: {conditions['h4_trend']} | D1 Trend OK: {conditions['d1_trend']} | Strength OK: {strength_ok}\n"
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
        "h4_rsi": h4_rsi,
        "candle_ok": candle_ok,
        "h4_breakout": bool(h4_breakout),
        "h4_trend_ok": conditions["h4_trend"],
        "d1_trend_ok": conditions["d1_trend"],
        "strength_ok": strength_ok,
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

            # Trigger only top candidate per loop
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
