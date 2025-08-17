import logging
import datetime
import time

from config import PAIRS, OANDA_API, HEADERS, ALERT_COOLDOWN, STRENGTH_ALERT_COOLDOWN
from currency_strength import run_currency_strength_alert
from breakout import run_group_breakout_alert  # persistent
from forex_news_alert import fetch_forexfactory_events
from utils import send_telegram, get_current_session
from trade_signal import send_trade_signal, select_best_trade_pair, last_trade_alert_times

# ================= SETUP LOGGING =================
logger = logging.getLogger("forex_bot")
logger.setLevel(logging.WARNING)  # Only warnings and errors
logging.basicConfig(format="%(asctime)s [%(levelname)s] %(message)s")

# ================= LAST ALERT TRACKERS =================
last_strength_alert_time = 0
last_news_alert_times = {}
last_heartbeat = 0
HEARTBEAT_INTERVAL = 24 * 3600  # seconds

# ================= STARTUP =================
logger.warning("Forex alert bot started! Running in PRODUCTION MODE")
send_telegram("🚀 Forex alert bot started in PRODUCTION MODE")

# ================= HELPER FUNCTIONS =================
def cleanup_old_session_alerts(current_session):
    """Remove trade alerts from previous sessions to prevent dict growth."""
    keys_to_remove = [key for key in last_trade_alert_times if key[1] != current_session]
    for key in keys_to_remove:
        del last_trade_alert_times[key]

# ================= MAIN LOOP =================
while True:
    try:
        now_utc = datetime.datetime.now(datetime.timezone.utc)
        now_ts = time.time()
        current_session = get_current_session()

        # --- Heartbeat ---
        if now_ts - last_heartbeat >= HEARTBEAT_INTERVAL:
            heartbeat_msg = f"🫀 Alert bot heartbeat at {now_utc.strftime('%Y-%m-%d %H:%M:%S UTC')}"
            if send_telegram(heartbeat_msg):
                logger.warning("[Heartbeat] Sent heartbeat alert")
            last_heartbeat = now_ts

        # --- Currency Strength Alert ---
        alerted_currencies, last_strength_alert_time = run_currency_strength_alert(
            oanda_api=OANDA_API,
            headers=HEADERS,
            last_alert_time=last_strength_alert_time,
            cooldown=STRENGTH_ALERT_COOLDOWN,
            threshold=5
        )
        if alerted_currencies:
            logger.warning(f"[Currency Strength] Alert sent: {alerted_currencies}")

        # --- Group Breakout Alerts ---
        group_alert_results = run_group_breakout_alert(3)
        for currency, breakout_list in group_alert_results.items():
            formatted_list = []
            for p in breakout_list:
                if p not in PAIRS:
                    parts = p.split("_")
                    if len(parts) == 2:
                        flipped = f"{parts[1]}_{parts[0]}"
                        if flipped in PAIRS:
                            formatted_list.append(flipped)
                else:
                    formatted_list.append(p)

            if formatted_list and len(formatted_list) >= 3:
                msg_text = (
                    f"📢 {currency} Group Breakout Alert! ({len(formatted_list)} pairs)\n\n"
                    + "\n".join(sorted(formatted_list))
                )
                send_telegram(msg_text)
                logger.warning(f"✅ Sent {currency} breakout alert: {', '.join(sorted(formatted_list))}")

        # --- Trade Signal Alert ---
        if alerted_currencies:
            best_signal_result = select_best_trade_pair(alerted_currencies, valid_pairs=PAIRS)
            if best_signal_result:
                pair, action, base_strength, quote_strength, session = best_signal_result
                cleanup_old_session_alerts(session)

                key = (pair, session)
                if key not in last_trade_alert_times or now_ts - last_trade_alert_times[key] >= ALERT_COOLDOWN:
                    send_trade_signal(pair, base_strength, quote_strength, session_name=session)
                    last_trade_alert_times[key] = now_ts
                    logger.warning(f"[Trade Signal] Sent {action} signal for {pair} (Base {base_strength}, Quote {quote_strength})")

        # --- Forex News Alerts ---
        events = fetch_forexfactory_events()
        for ev in events:
            event_key = f"{ev['currency']}_{ev['event']}"
            if event_key not in last_news_alert_times:
                msg = (
                    f"📢 High-Impact Forex News Alert!\n"
                    f"{ev['currency']} - {ev['event']}\n"
                    f"Time: {ev['time'].strftime('%Y-%m-%d %H:%M UTC')}"
                )
                if send_telegram(msg):
                    last_news_alert_times[event_key] = now_ts
                    logger.warning(f"[News] Alert sent for {ev['currency']} - {ev['event']}")

        # Wait 5 minutes
        time.sleep(300)

    except Exception as e:
        logger.error(f"Unexpected error in main loop: {e}", exc_info=True)
        time.sleep(60)