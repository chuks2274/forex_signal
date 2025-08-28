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
def build_trade_signal(pair: str, base_val: float, quote_val: float, rank_map: dict) -> Optional[Dict]:
    now = time.time()
    if now - _LAST_ALERT_TIME.get(pair, 0) < ALERT_COOLDOWN:
        return None

    # ---------------- H1 Candles & RSI ----------------
    candles_1h = get_recent_candles(pair, "H1", 50)
    if not candles_1h:
        return None
    h1_close_prices = [float(c["close"]) for c in candles_1h]
    h1_rsi_values = rsi(h1_close_prices)
    if not h1_rsi_values:
        return None
    h1_rsi = h1_rsi_values[-1]

    # H1 breakout confirmation (trend)
    h1_breakout = check_breakout_h1(pair, candles_1h, rank_map)
    yest_breakout = check_breakout_yesterday(pair, candles_1h, rank_map)
    breakout_info = h1_breakout or yest_breakout
    if not breakout_info:
        return None

    # ---------------- Direction ----------------
    direction = "buy" if base_val > quote_val else "sell"
    strong_val, weak_val = (base_val, quote_val) if direction == "buy" else (quote_val, base_val)

    # ---------------- Strength Filter ----------------
    if not strength_filter(strong_val, weak_val):
        return None

    # ---------------- H1 RSI Confirmation ----------------
    if direction == "buy" and h1_rsi < 50:
        return None
    if direction == "sell" and h1_rsi > 50:
        return None

    # ---------------- ATR ----------------
    atr_val = atr(candles_1h)

    # ---------------- M15 RSI Confirmation ----------------
    m15_candles = get_recent_candles(pair, "M15", 50)
    if not m15_candles:
        return None
    m15_close_prices = [float(c["close"]) for c in m15_candles]
    m15_rsi_values = rsi(m15_close_prices)
    if not m15_rsi_values:
        return None
    m15_rsi = m15_rsi_values[-1]

    # M15 momentum check (slightly relaxed for better entry timing)
    if direction == "buy" and m15_rsi <= 45:
        return None
    if direction == "sell" and m15_rsi >= 55:
        return None

    # ---------------- Entry / SL / TP ----------------
    entry = m15_candles[-1]["close"]
    stop_loss = entry - atr_val if direction == "buy" else entry + atr_val
    tp1, tp2, tp3 = (
        entry + atr_val*2, entry + atr_val*4, entry + atr_val*6
    ) if direction == "buy" else (
        entry - atr_val*2, entry - atr_val*4, entry - atr_val*6
    )

    # ---------------- Send Alert ----------------
    symbol = "🟢 BUY" if direction == "buy" else "🔴 SELL"
    alert_msg = f"""{symbol} {pair} [strength_alert]
Strength Diff: {abs(strong_val - weak_val):.1f}
Strengths: {strong_val:+.1f}, {weak_val:+.1f}
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
    logger.info(f"Trade triggered: {pair} | Direction: {direction} | Strength Diff: {abs(strong_val - weak_val):.1f} | H1 RSI: {h1_rsi:.1f} | M15 RSI: {m15_rsi:.1f}")
    return trade_info

# ---------------- Async Trade Loop ----------------
async def run_trade_signal_loop_async(debug: bool = False):
    logger.info("📡 Async Trade Signal Loop Started")
    last_trade_alert_times: Dict = {}

    while True:
        try:
            # Fetch top candidate currency strengths
            rank_map, _ = run_currency_strength_alert(last_trade_alert_times=last_trade_alert_times)
            if not rank_map:
                await asyncio.sleep(LOOP_INTERVAL)
                continue

            # ---------------- Candidate Pairs ----------------
            candidate_pairs = []
            for pair in PAIRS:
                if "_" not in pair:
                    continue
                base, quote = pair.split("_")
                base_val, quote_val = rank_map.get(base), rank_map.get(quote)
                if base_val is None or quote_val is None:
                    continue
                candidate_pairs.append((abs(base_val - quote_val), pair, base_val, quote_val))

            # ---------------- Top Candidate ----------------
            if candidate_pairs:
                candidate_pairs.sort(reverse=True, key=lambda x: x[0])
                _, pair, base_val, quote_val = candidate_pairs[0]
                build_trade_signal(pair, base_val, quote_val, rank_map)

            save_active_trades(_ACTIVE_TRADES)
            await asyncio.sleep(LOOP_INTERVAL)

        except Exception as e:
            logger.error(f"Unexpected error in trade loop: {e}", exc_info=True)
            await asyncio.sleep(5)

# ---------------- Entry Point ----------------
if __name__ == "__main__":
    asyncio.run(run_trade_signal_loop_async())
