import os
import time
import json
import logging
from typing import Dict, List, Optional, Tuple
import numpy as np

from config import PAIRS, ALERT_COOLDOWN
from utils import send_telegram, get_recent_candles
from breakout import check_breakout_h1, check_breakout_yesterday
from currency_strength import run_currency_strength_alert

# ---------------- Logging ----------------
logger = logging.getLogger("trade_signal")
logger.setLevel(logging.INFO)

# ---------------- Persistent storage ----------------
ALERTS_FILE = "trade_alerts.json"
ACTIVE_TRADES_FILE = "active_trades.json"
last_trade_alert_times: Dict[Tuple[str, str], float] = {}
ACTIVE_TRADES: List[Dict[str, str]] = []

# ================= Persistence ======================
def load_alerts():
    global last_trade_alert_times
    if os.path.exists(ALERTS_FILE):
        try:
            with open(ALERTS_FILE, "r") as f:
                data = json.load(f)
            last_trade_alert_times = {tuple(k.split("|")): float(v) for k, v in data.items()}
            logger.info("[Trade Signal] Loaded previous alerts")
        except Exception as e:
            logger.error(f"[Trade Signal] Failed to load alerts: {e}")

def save_alerts():
    try:
        data = {"|".join(k): v for k, v in last_trade_alert_times.items()}
        with open(ALERTS_FILE, "w") as f:
            json.dump(data, f)
        logger.info("[Trade Signal] Alerts saved")
    except Exception as e:
        logger.error(f"[Trade Signal] Failed to save alerts: {e}")

def save_active_trades():
    """Save active trades to JSON file for news_alert.py."""
    try:
        with open(ACTIVE_TRADES_FILE, "w") as f:
            json.dump(ACTIVE_TRADES, f)
        logger.info("[Trade Signal] Active trades saved")
    except Exception as e:
        logger.error(f"[Trade Signal] Failed to save active trades: {e}")

# ================= Helpers =========================
def _parse_pair(pair: str) -> Tuple[str, str, str]:
    """Normalize pair names like EURUSD → EUR_USD"""
    p = pair.replace("-", "").replace("/", "").upper()
    if "_" in p:
        base, quote = p.split("_")[:2]
    else:
        base, quote = p[:3], p[3:6]
    return f"{base}_{quote}", base, quote

def add_active_trade(pair: str, action: str):
    """Add trade to active list and save to file."""
    global ACTIVE_TRADES
    ACTIVE_TRADES.append({"pair": pair, "type": action})
    save_active_trades()  # Save immediately
    msg = f"🚨 Active trade added: {pair} ({action})"
    logger.info(f"[Trade] {msg}")
    send_telegram(msg)

def remove_active_trade(pair: str, action: str):
    """Remove trade from active list and save to file."""
    global ACTIVE_TRADES
    ACTIVE_TRADES[:] = [t for t in ACTIVE_TRADES if not (t['pair'] == pair and t['type'] == action)]
    save_active_trades()  # Save immediately
    msg = f"🗑️ Active trade removed: {pair} ({action})"
    logger.info(f"[Trade] {msg}")
    send_telegram(msg)

# ================= Build Trade Signal =================
def build_trade_signal(pair: str, candles_1h: List[dict], candles_15m: List[dict],
                       strength_data: Dict[str,float], min_rrr: int = 2) -> Optional[dict]:
    norm_pair, base, quote = _parse_pair(pair)
    base_strength = strength_data.get(base, 0)
    quote_strength = strength_data.get(quote, 0)
    diff = abs(base_strength - quote_strength)

    # Threshold logic
    if (
        (base_strength >= 7 and quote_strength <= -7) or
        (base_strength >= 5 and quote_strength <= -7) or
        (base_strength >= 5 and quote_strength <= -5)
    ):
        direction = "BUY"
    elif (
        (base_strength <= -7 and quote_strength >= 7) or
        (base_strength <= -5 and quote_strength >= 7) or
        (base_strength <= -5 and quote_strength >= 5)
    ):
        direction = "SELL"
    else:
        return None

    # Breakout scenario
    h1_breakout = check_breakout_h1(norm_pair)
    yesterday_breakout = check_breakout_yesterday(norm_pair)
    scenario = None
    if h1_breakout:
        scenario = "Breakout Today"
    elif yesterday_breakout:
        scenario = "Yesterday Breakout + Retest"
    else:
        return None

    last_candle_15m = candles_15m[-1]
    entry = float(last_candle_15m["mid"]["c"])
    recent_candles = candles_15m[-14:]
    atr_val = np.mean([float(c["mid"]["h"]) - float(c["mid"]["l"]) for c in recent_candles])
    stop_loss = entry - atr_val if direction == "BUY" else entry + atr_val

    tps = {}
    for i in range(3):
        if direction == "BUY":
            tps[f"TP{i+1}"] = float(round(entry + atr_val * (i + 2), 5))
        else:
            tps[f"TP{i+1}"] = float(round(entry - atr_val * (i + 2), 5))

    return {
        "pair": norm_pair,
        "pair_compact": norm_pair.replace("_",""),
        "action": direction,
        "entry": round(entry,5),
        "stop_loss": round(stop_loss,5),
        "take_profit_levels": tps,
        "ATR": round(atr_val,5),
        "scenario": scenario,
        "timeframes": {"breakout":"1H","retest":"15M"},
        "strength_snapshot": {base: base_strength, quote: quote_strength},
        "strength_diff": diff,
        "min_RRR": min_rrr,
    }

# ================= Send Alerts ======================
def find_and_send_best_signal(signal: dict, session_name: str) -> bool:
    if not signal:
        return False

    action_upper = signal["action"].upper()
    emoji = "🟢" if action_upper == "BUY" else "🔴"
    strength_text = ", ".join([f"{k}:{v}" for k,v in signal["strength_snapshot"].items()])
    tp_text = ", ".join([f"{k}:{v}" for k,v in signal["take_profit_levels"].items()])

    msg = (
        f"{emoji} {action_upper} {signal['pair']} [{session_name}]\n"
        f"Scenario: {signal.get('scenario','N/A')}\n"
        f"Strength Diff: {signal.get('strength_diff','N/A')}\n"
        f"Strengths: {strength_text}\n"
        f"Entry: {signal['entry']} | SL: {signal['stop_loss']} | ATR: {signal['ATR']}\n"
        f"TPs: {tp_text} | Min RRR: 1:{signal.get('min_RRR','N/A')}\n"
        f"Timeframes: {signal.get('timeframes','N/A')}"
    )

    logger.info(f"[Trade Signal] {msg.replace(os.linesep,' | ')}")
    send_telegram(msg)

    add_active_trade(signal["pair"], action_upper)

    return True

# ================= Main Trade Loop =========================
def run_trade_signal_loop(alerted_currencies=None):
    now_ts = time.time()
    if not alerted_currencies:
        return

    session = "strength_alert"
    for pair in PAIRS:
        if "_" not in pair:
            continue

        key = (pair, session)
        if key in last_trade_alert_times and now_ts - last_trade_alert_times[key] < ALERT_COOLDOWN:
            continue

        candles_1h = get_recent_candles(pair, granularity="H1", count=30)
        candles_15m = get_recent_candles(pair, granularity="M15", count=30)
        if not candles_1h or not candles_15m:
            continue

        signal = build_trade_signal(pair, candles_1h, candles_15m, alerted_currencies)
        if not signal:
            continue

        sent = find_and_send_best_signal(signal, session)
        if sent:
            last_trade_alert_times[key] = now_ts
            logger.info(f"✅ Sent trade alert for {pair}: {signal['action']}")

    save_alerts()