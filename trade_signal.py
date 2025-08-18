import datetime
import json
import logging
import os
from config import PAIRS, ALERT_COOLDOWN
from breakout import check_breakout_h1
from utils import send_telegram, get_current_session

# --- Logging ---
logger = logging.getLogger("trade_signal")
logger.setLevel(logging.WARNING)  # Only warnings and errors appear in Render logs

# --- Persistent storage file ---
ALERTS_FILE = "trade_alerts.json"

# --- Track last alert times per pair/session ---
last_trade_alert_times = {}  # key = (pair, session), value = timestamp float

# ================= PERSISTENCE HELPERS =================
def load_alerts():
    global last_trade_alert_times
    if os.path.exists(ALERTS_FILE):
        try:
            with open(ALERTS_FILE, "r") as f:
                data = json.load(f)
            last_trade_alert_times = {tuple(k.split("|")): float(v) for k, v in data.items()}
            logger.info("[Trade Signal] Loaded previous alerts from file")
        except Exception as e:
            logger.error(f"[Trade Signal] Failed to load alerts: {e}")

def save_alerts():
    try:
        data = {"|".join(k): v for k, v in last_trade_alert_times.items()}
        with open(ALERTS_FILE, "w") as f:
            json.dump(data, f)
        logger.info("[Trade Signal] Alerts saved to file")
    except Exception as e:
        logger.error(f"[Trade Signal] Failed to save alerts: {e}")

# ================= HELPER FUNCTIONS =================
def clear_expired_session_alerts():
    """Remove alerts from previous sessions so pairs can trigger again."""
    current_session = get_current_session()
    keys_to_remove = [k for k in last_trade_alert_times if k[1] != current_session]
    for key in keys_to_remove:
        del last_trade_alert_times[key]
    save_alerts()

# ================= TRADE SIGNAL SELECTION =================
def select_best_trade_pair(alerted_currencies, valid_pairs=None, bypass_h1=False):
    """
    Strict selection:
    - BUY: base > 0, quote < 0
    - SELL: base < 0, quote > 0
    - Pair must exist in PAIRS
    - Must pass H1 breakout (unless bypass_h1=True)
    - Cooldown respected
    """
    if valid_pairs is None:
        valid_pairs = PAIRS

    if not isinstance(alerted_currencies, dict) or len(alerted_currencies) < 2:
        return None

    now_ts = datetime.datetime.now(datetime.timezone.utc).timestamp()
    session = get_current_session()
    clear_expired_session_alerts()
    if not session:
        logger.warning("[Trade Signal] No active session found")
        return None

    # Sort currencies by absolute strength
    sorted_currs = sorted(alerted_currencies.items(), key=lambda x: abs(x[1]), reverse=True)

    for base, base_strength in sorted_currs:
        for quote, quote_strength in sorted_currs[::-1]:
            if base == quote:
                continue

            # Determine action
            action = None
            if base_strength > 0 and quote_strength < 0:
                action = "BUY"
            elif base_strength < 0 and quote_strength > 0:
                action = "SELL"
            else:
                continue

            pair = f"{base}_{quote}"
            if pair not in valid_pairs:
                flipped = f"{quote}_{base}"
                if flipped in valid_pairs:
                    pair = flipped
                else:
                    continue

            key = (pair, session)
            if key in last_trade_alert_times and now_ts - last_trade_alert_times[key] < ALERT_COOLDOWN:
                continue

            if not bypass_h1 and not check_breakout_h1(pair):
                continue

            last_trade_alert_times[key] = now_ts
            save_alerts()
            logger.warning(f"[Trade Signal] Selected {action} signal for {pair} "
                           f"(Base {base_strength}, Quote {quote_strength})")
            return (pair, action, base_strength, quote_strength, session)

    return None

# ================= TRADE SIGNAL SENDING =================
def send_trade_signal(pair, base_strength, quote_strength, session_name="Unknown"):
    """Send trade signal via Telegram and log result."""
    try:
        signal_type = None
        if base_strength > 0 and quote_strength < 0:
            signal_type = "BUY"
        elif base_strength < 0 and quote_strength > 0:
            signal_type = "SELL"

        if signal_type:
            msg = f"[Trade Signal] {signal_type} {pair} [{session_name} Session] (Base {base_strength:+}, Quote {quote_strength:+})"
            logger.warning(msg)
            success = send_telegram(msg)
            if not success:
                logger.error(f"[Trade Signal] Telegram send FAILED for {pair}")
        else:
            logger.warning(f"[Trade Signal] No clear signal for {pair} (Base {base_strength:+}, Quote {quote_strength:+})")

    except Exception as e:
        logger.error(f"Error sending trade signal for {pair}: {e}", exc_info=True)

# ================= INITIALIZE =================
load_alerts()