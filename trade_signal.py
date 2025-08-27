import time
import logging
from typing import Dict, List, Optional
from config import PAIRS, RSI_PERIOD, LOOP_INTERVAL, ALERT_COOLDOWN
from breakout import check_breakout_h1, check_breakout_yesterday
from utils import (
    get_recent_candles, load_active_trades,
    get_rsi, send_alert, atr
)

# ---------------- Logger ----------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger("trade_signal")

# ---------------- State ----------------
_ACTIVE_TRADES: List[Dict] = load_active_trades()
prev_m15_rsi: Dict[str, Optional[float]] = {pair: None for pair in PAIRS}
armed_flags: Dict[str, Dict[str, bool]] = {pair: {"buy": False, "sell": False} for pair in PAIRS}
_LAST_ALERT_TIME: Dict[str, float] = {}

# ---------------- Config ----------------
MIN_RRR = 2.0
MIN_STRONG = 5
MAX_WEAK = -5
RANGE_LOOKBACK = 10   # M15 candles for range confirmation
ATTEMPTS_NEEDED = 1   # only 1 rejection needed
MIN_SR_BUFFER = 0.3
MAX_SR_BUFFER = 1.0

# ---------------- Retest Check ----------------
def check_retest_confirmation(pair: str, breakout_level: float, direction: str) -> bool:
    m15 = get_recent_candles(pair, "M15", 50)
    if not m15:
        return False
    closes = [c["close"] for c in m15]
    if len(closes) < 5:
        return False
    atr_val = atr(m15)
    tolerance = atr_val * 1.0   # loosened tolerance
    rsi_series = get_rsi_series(closes)
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

def get_rsi_series(closes: List[float]) -> List[float]:
    """Safe RSI fetch for a series of close prices"""
    return get_rsi(closes, period=RSI_PERIOD) or []

# ---------------- Range Confirmation ----------------
def confirm_m15_range(pair: str, direction: str, lookback: int = RANGE_LOOKBACK, attempts_needed: int = ATTEMPTS_NEEDED) -> bool:
    m15 = get_recent_candles(pair, "M15", lookback)
    if not m15 or len(m15) < lookback:
        return False

    highs = [c["high"] for c in m15]
    lows = [c["low"] for c in m15]
    closes = [c["close"] for c in m15]

    swing_high = max(highs[:-2])
    swing_low = min(lows[:-2])

    attempts = 0
    if direction == "sell":
        for i in range(-2, 0):
            if m15[i]["high"] >= swing_high and closes[i] < swing_high:
                attempts += 1
        return attempts >= attempts_needed

    elif direction == "buy":
        for i in range(-2, 0):
            if m15[i]["low"] <= swing_low and closes[i] > swing_low:
                attempts += 1
        return attempts >= attempts_needed

    return False

# ---------------- H1 SR Filter ----------------
def get_dynamic_sr_buffer(strength_diff: int, atr_val: float, min_buffer: float = MIN_SR_BUFFER, max_buffer: float = MAX_SR_BUFFER):
    if strength_diff >= 14:
        buffer_multiplier = min_buffer
    elif strength_diff <= 10:
        buffer_multiplier = max_buffer
    else:
        buffer_multiplier = max_buffer - (strength_diff - 10) * (max_buffer - min_buffer) / 4
    return buffer_multiplier * atr_val

def confirm_h1_sr_filter(pair: str, direction: str, h1_pivots: List[float], strength_diff: int) -> bool:
    if not h1_pivots:  # safety: no pivots
        return True
    candles_1h = get_recent_candles(pair, "H1", 20)
    if not candles_1h:
        return False
    last_close = candles_1h[-1]["close"]
    atr_val = atr(candles_1h)
    buffer = get_dynamic_sr_buffer(strength_diff, atr_val)
    for pivot in h1_pivots:
        if abs(last_close - pivot) <= buffer:
            return False
    return True

# ---------------- Build Trade Signal ----------------
def build_trade_signal(pair: str, strength_data: Dict[str, int], debug: bool = False) -> Optional[Dict]:
    now = time.time()
    if now - _LAST_ALERT_TIME.get(pair, 0) < ALERT_COOLDOWN:
        return None

    candles_1h = get_recent_candles(pair, "H1", 50)
    if not candles_1h:
        return None

    h1_breakout = check_breakout_h1(pair, candles_1h, strength_data)
    yest_breakout = check_breakout_yesterday(pair, candles_1h, strength_data)
    breakout_info = h1_breakout or yest_breakout
    scenario = "Breakout Today" if h1_breakout else "Breakout Yesterday + Retest"
    if not breakout_info:
        return None

    breakout_level, _, h1_pivots = breakout_info
    h1_pivots = h1_pivots or []  # ✅ pivot-safe

    base, quote = pair.split("_")
    base_strength = strength_data.get(base, 0)
    quote_strength = strength_data.get(quote, 0)
    strength_diff = abs(base_strength - quote_strength)

    if base_strength >= quote_strength:
        direction = "buy"
        strong_curr, strong_val = base, base_strength
        weak_curr, weak_val = quote, quote_strength
    else:
        direction = "sell"
        strong_curr, strong_val = quote, quote_strength
        weak_curr, weak_val = base, base_strength

    # Strength filter
    if strong_val < MIN_STRONG or weak_val > -MAX_WEAK or strength_diff < 10:
        return None

    # H1 SR filter
    if not confirm_h1_sr_filter(pair, direction, h1_pivots, strength_diff):
        return None

    atr_val = atr(candles_1h)
    m15_candles = get_recent_candles(pair, "M15", 1)
    if not m15_candles:
        return None
    entry = m15_candles[-1]["close"]

    if direction == "buy":
        stop_loss = entry - atr_val
        tp1, tp2, tp3 = entry + atr_val*2, entry + atr_val*4, entry + atr_val*6
    else:
        stop_loss = entry + atr_val
        tp1, tp2, tp3 = entry - atr_val*2, entry - atr_val*4, entry - atr_val*6

    if not check_retest_confirmation(pair, breakout_level, direction):
        return None

    # M15 RSI Pullback + Crossback (looser)
    m15_candles_full = get_recent_candles(pair, "M15", 100)
    last_rsi = get_rsi_series([c["close"] for c in m15_candles_full])[-1] if m15_candles_full else None
    prev_rsi = prev_m15_rsi.get(pair)
    flags = armed_flags[pair]
    signal = None

    if last_rsi is not None:
        if direction == "buy":
            if last_rsi < 50:
                flags["buy"] = True
            elif flags["buy"] and prev_rsi is not None and prev_rsi < 50 and last_rsi >= 50:
                signal = "BUY"
                flags["buy"] = False
        elif direction == "sell":
            if last_rsi > 50:
                flags["sell"] = True
            elif flags["sell"] and prev_rsi is not None and prev_rsi > 50 and last_rsi <= 50:
                signal = "SELL"
                flags["sell"] = False
        prev_m15_rsi[pair] = last_rsi

    if signal != direction.upper():
        return None

    # M15 range confirmation (only 1 rejection needed)
    if not confirm_m15_range(pair, direction):
        return None

    rrr = abs(tp1 - entry) / abs(entry - stop_loss) if stop_loss != entry else 0
    if rrr < MIN_RRR:
        return None

    # Build alert
    strong_str = f"{strong_curr}:{strong_val:+d}"
    weak_str = f"{weak_curr}:{weak_val:+d}"
    symbol = "🟢 BUY" if direction == "buy" else "🔴 SELL"
    alert_msg = f"""{symbol} {pair} [strength_alert]
Scenario: {scenario}
Strength Diff: {strength_diff}
Strengths: {strong_str}, {weak_str}
Entry: {entry:.5f} | SL: {stop_loss:.5f} | ATR: {atr_val:.5f}
TPs: TP1:{tp1:.5f}, TP2:{tp2:.5f}, TP3:{tp3:.5f} | Min RRR:1:{MIN_RRR}
Timeframes: {{'breakout':'H1','retest':'M15'}} 📊 Currency Strength Alert 📊"""
    send_alert(alert_msg)
    _LAST_ALERT_TIME[pair] = now

    _ACTIVE_TRADES.append({
        "pair": pair,
        "direction": direction,
        "entry": entry,
        "stop_loss": stop_loss,
        "take_profit_levels": [tp1, tp2, tp3],
        "strength_diff": strength_diff,
        "time": now
    })
    logger.info(f"Trade triggered: {pair} | Direction: {direction} | Strength Diff: {strength_diff}")
    return _ACTIVE_TRADES[-1]

# ---------------- Main Loop ----------------
def run_trade_signal_loop(debug: bool = False):
    from utils import get_currency_strength, save_active_trades
    logger.info("📡 Trade Signal Loop Started")
    while True:
        try:
            strength_data = get_currency_strength(PAIRS)
            for pair in PAIRS:
                build_trade_signal(pair, strength_data, debug=debug)
            save_active_trades(_ACTIVE_TRADES)
            time.sleep(LOOP_INTERVAL)
        except Exception as e:
            logger.error(f"Unexpected error in trade loop: {e}", exc_info=True)
            time.sleep(5)

if __name__ == "__main__":
    run_trade_signal_loop()
