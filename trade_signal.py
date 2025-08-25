import time
import logging
from typing import Dict, List, Optional
from config import PAIRS, ALERT_COOLDOWN
from utils import get_recent_candles, save_active_trades, load_active_trades, send_telegram, atr, rsi
from breakout import check_breakout_h1, check_breakout_yesterday

logger = logging.getLogger("trade_signal")
logger.setLevel(logging.INFO)

_ACTIVE_TRADES: List[Dict] = load_active_trades()
_LAST_ALERT_TIME: Dict[str, float] = {}

MIN_RRR = 2.0  # Minimum 1:2 risk-to-reward
MIN_STRONG = 5
MAX_WEAK = -5

# ---------------- Retest Check ----------------
def check_retest_confirmation(pair: str, breakout_level: float, direction: str) -> bool:
    m15 = get_recent_candles(pair, "M15", 50)
    if not m15:
        return False
    closes = [c["close"] for c in m15]
    if len(closes) < 5:
        return False
    atr_val = atr(m15)
    tolerance = atr_val * 0.5
    rsi_series = rsi(closes)
    if len(rsi_series) < 5:
        return False
    for i in range(-5, 0):
        candle = m15[i]
        close = candle["close"]
        low = candle["low"]
        high = candle["high"]
        rsi_val = rsi_series[i]
        if direction == "buy" and abs(low - breakout_level) <= tolerance and close > candle["open"] and rsi_val < 70:
            return True
        elif direction == "sell" and abs(high - breakout_level) <= tolerance and close < candle["open"] and rsi_val > 30:
            return True
    return False

# ---------------- RSI Filter for Pullback Entries ----------------
def rsi_filter(pair: str, direction: str) -> bool:
    """Trend filter: H1 RSI + M15 pullback cross logic"""
    h1 = get_recent_candles(pair, "H1", 100)
    m15 = get_recent_candles(pair, "M15", 100)
    if not h1 or not m15:
        return False

    h1_closes = [c["close"] for c in h1]
    m15_closes = [c["close"] for c in m15]

    h1_rsi = rsi(h1_closes)[-1]
    m15_rsi_series = rsi(m15_closes)
    if not m15_rsi_series or len(m15_rsi_series) < 2:
        return False

    last_rsi = m15_rsi_series[-1]
    prev_rsi = m15_rsi_series[-2]

    if direction == "buy" and h1_rsi >= 55:
        if last_rsi >= 50:
            return True
        if prev_rsi < 50 and last_rsi >= 50:
            return True

    if direction == "sell" and h1_rsi <= 45:
        if last_rsi <= 50:
            return True
        if prev_rsi > 50 and last_rsi <= 50:
            return True

    return False

# ---------------- Risk-to-Reward ----------------
def calculate_rrr(entry, sl, tp):
    risk = abs(entry - sl)
    reward = abs(tp - entry)
    return reward / risk if risk != 0 else 0

# ---------------- Build Trade Signal ----------------
def build_trade_signal(pair: str, candles_1h: List[Dict], strength_data: Dict[str, int], debug: bool = False) -> Optional[Dict]:
    h1_breakout = check_breakout_h1(pair, candles_1h, strength_data)
    yest_breakout = check_breakout_yesterday(pair, candles_1h, strength_data)
    breakout_info = h1_breakout or yest_breakout
    scenario = "Breakout Today" if h1_breakout else "Breakout Yesterday + Retest"
    if not breakout_info:
        return None
    breakout_level, _original_direction, _ = breakout_info

    base, quote = pair.split("_")
    base_strength = strength_data.get(base, 0)
    quote_strength = strength_data.get(quote, 0)
    strength_diff = abs(base_strength - quote_strength)
    if strength_diff not in [10, 12, 14]:
        return None

    if base_strength >= quote_strength:
        alert_direction = "buy"
        strong_curr, strong_val = base, base_strength
        weak_curr, weak_val = quote, quote_strength
    else:
        alert_direction = "sell"
        strong_curr, strong_val = quote, quote_strength
        weak_curr, weak_val = base, base_strength

    # ---------------- FILTER ±5/-5 STRENGTH ----------------
    if strong_val < MIN_STRONG or weak_val > MAX_WEAK:
        if debug:
            logger.info(f"Skipping {pair}: strength values below +5/-5 threshold")
        return None

    atr_val = atr(candles_1h)

    # Safe entry fetch
    m15_candles = get_recent_candles(pair, "M15", 1)
    if not m15_candles:
        if debug:
            logger.info(f"No M15 candles for {pair}, skipping trade signal")
        return None
    entry = m15_candles[-1]["close"]

    if alert_direction == "buy":
        stop_loss = entry - atr_val
        tp1, tp2, tp3 = entry + atr_val * 2, entry + atr_val * 4, entry + atr_val * 6
    else:
        stop_loss = entry + atr_val
        tp1, tp2, tp3 = entry - atr_val * 2, entry - atr_val * 4, entry - atr_val * 6

    # Check retest
    if not check_retest_confirmation(pair, breakout_level, alert_direction):
        if debug:
            logger.info(f"Skipping {pair}: M15 retest not confirmed")
        return None

    # Check H1 + M15 RSI filter
    if not rsi_filter(pair, alert_direction):
        if debug:
            logger.info(f"Skipping {pair}: RSI filter not confirmed")
        return None

    rrr = calculate_rrr(entry, stop_loss, tp1)
    if rrr < MIN_RRR:
        return None

    # ---------------- FIXED STRENGTH FORMATTING ----------------
    strong_str = f"{strong_curr}:{strong_val:+d}"  
    weak_str = f"{weak_curr}:{weak_val:+d}"      

    symbol = "🟢 BUY" if alert_direction == "buy" else "🔴 SELL"
    alert_msg = f"""{symbol} {pair} [strength_alert]
Scenario: {scenario}
Strength Diff: {strength_diff}
Strengths: {strong_str}, {weak_str}
Entry: {entry:.5f} | SL: {stop_loss:.5f} | ATR: {atr_val:.5f}
TPs: TP1:{tp1:.5f}, TP2:{tp2:.5f}, TP3:{tp3:.5f} | Min RRR:1:{MIN_RRR}
Timeframes: {{'breakout':'H1','retest':'M15'}}"""
    send_telegram(alert_msg)

    _ACTIVE_TRADES.append({
        "pair": pair,
        "direction": alert_direction,
        "entry": entry,
        "stop_loss": stop_loss,
        "take_profit_levels": [tp1, tp2, tp3],
        "strength_diff": strength_diff,
        "time": time.time()
    })

    logger.info(f"Trade triggered: {pair} | Direction: {alert_direction} | Strength Diff: {strength_diff}")
    return _ACTIVE_TRADES[-1]

# ---------------- Main Loop ----------------
def run_trade_signal_loop(strength_data: Dict[str, int], debug: bool = False):
    """
    Loop through all pairs and trigger trade alerts for all valid setups.
    Respects ALERT_COOLDOWN per pair.
    """
    global _ACTIVE_TRADES, _LAST_ALERT_TIME
    now = time.time()
    trades_triggered = 0

    for pair in PAIRS:
        last_ts = _LAST_ALERT_TIME.get(pair, 0)
        if now - last_ts < ALERT_COOLDOWN:
            continue

        candles_1h = get_recent_candles(pair, "H1", 50)
        if not candles_1h:
            if debug:
                logger.info(f"No H1 candles for {pair}, skipping")
            continue

        try:
            result = build_trade_signal(pair, candles_1h, strength_data, debug=debug)
            if result:
                _LAST_ALERT_TIME[pair] = now
                trades_triggered += 1
                if debug:
                    logger.info(f"Trade triggered for {pair}")
        except Exception as e:
            logger.error(f"Error building trade signal for {pair}: {e}")

    if trades_triggered > 0:
        save_active_trades(_ACTIVE_TRADES)
        logger.info(f"{trades_triggered} trade(s) alerts saved")
