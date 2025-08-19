import logging
import datetime
import time

from config import PAIRS, OANDA_API, HEADERS, ALERT_COOLDOWN, STRENGTH_ALERT_COOLDOWN
from currency_strength import run_currency_strength_alert
from breakout import run_group_breakout_alert, check_breakout_h1
from forex_news_alert import send_news_alert_for_trade
from utils import send_telegram, get_current_session
from trade_signal import send_trade_signal, last_trade_alert_times

# ================= SETUP LOGGING =================
logger = logging.getLogger("forex_bot")
logger.setLevel(logging.WARNING)  # Only warnings/errors appear in terminal
logging.basicConfig(format="%(asctime)s [%(levelname)s] %(message)s")

# ================= LAST ALERT TRACKERS =================
last_strength_alert_time = 0
last_news_alert_times = {}  # {pair_event: timestamp}
last_heartbeat = 0
HEARTBEAT_INTERVAL = 24 * 3600  # seconds

# ================= STARTUP =================
send_telegram("🚀 Forex alert bot started in PRODUCTION MODE")

# ================= HELPER FUNCTIONS =================
def cleanup_old_session_alerts(current_session):
    """Remove trade alerts from previous sessions to prevent dict growth."""
    keys_to_remove = [key for key in last_trade_alert_times if key[1] != current_session]
    for key in keys_to_remove:
        del last_trade_alert_times[key]

def check_trade_signal(alerted_currencies, pair, session, now_ts):
    """
    Checks trade signal, validates H1 breakout, cooldown, and triggers:
    - Trade alert Telegram
    - News alert via send_news_alert_for_trade()
    """
    if "_" not in pair:
        return False

    base, quote = pair.split("_")
    base_strength = alerted_currencies.get(base, 0)
    quote_strength = alerted_currencies.get(quote, 0)

    if abs(base_strength) < 5 or abs(quote_strength) < 5:
        return False

    # Determine BUY/SELL
    signal_type = None
    if base_strength > 0 and quote_strength < 0:
        signal_type = "BUY"
    elif base_strength < 0 and quote_strength > 0:
        signal_type = "SELL"
    else:
        return False

    if pair not in PAIRS or not check_breakout_h1(pair):
        return False

    key = (pair, session)
    if key in last_trade_alert_times and now_ts - last_trade_alert_times[key] < ALERT_COOLDOWN:
        return False

    # ✅ Valid signal: send trade alert
    last_trade_alert_times[key] = now_ts
    send_trade_signal(pair, base_strength, quote_strength, session_name=session)

    # --- Trigger news alert if applicable ---
    send_news_alert_for_trade(pair)
    return True

# ================= MAIN LOOP =================
while True:
    try:
        now_utc = datetime.datetime.now(datetime.timezone.utc)
        now_ts = time.time()
        current_session = get_current_session()

        # --- Heartbeat ---
        if now_ts - last_heartbeat >= HEARTBEAT_INTERVAL:
            heartbeat_msg = f"🫀 Alert bot heartbeat at {now_utc.strftime('%Y-%m-%d %H:%M:%S UTC')}"
            send_telegram(heartbeat_msg)
            last_heartbeat = now_ts

        # --- Currency Strength Alerts ---
        alerted_currencies, last_strength_alert_time = run_currency_strength_alert(
            oanda_api=OANDA_API,
            headers=HEADERS,
            last_alert_time=last_strength_alert_time,
            cooldown=STRENGTH_ALERT_COOLDOWN
        )

        # --- Group Breakout Alerts ---
        run_group_breakout_alert(3)  # no terminal logging

        # --- Trade Signal Alerts ---
        if alerted_currencies and current_session:
            cleanup_old_session_alerts(current_session)
            for pair in PAIRS:
                check_trade_signal(alerted_currencies, pair, current_session, now_ts)

        # --- Wait 5 minutes ---
        time.sleep(300)

    except Exception as e:
        logger.error(f"Unexpected error in main loop: {e}", exc_info=True)
        time.sleep(60)