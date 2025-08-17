import logging
import datetime
import time

from config import PAIRS, OANDA_API, HEADERS, ALERT_COOLDOWN, STRENGTH_ALERT_COOLDOWN
from currency_strength import run_currency_strength_alert
from breakout import check_breakout_h1, run_group_breakout_alert
from forex_news_alert import send_news_alert, fetch_forexfactory_events
from utils import send_telegram

# ================= SETUP LOGGING =================
logging.basicConfig(level=logging.WARNING, format='%(asctime)s [%(levelname)s] %(message)s')

# ================= LAST ALERT TRACKERS =================
last_strength_alert_time = None
last_group_alerts = {}          # per currency
last_news_alert_times = {}      # per event
last_heartbeat = None
HEARTBEAT_INTERVAL = 24 * 3600  # seconds

# Notify startup
logging.warning("Forex alert bot started! Running in PRODUCTION MODE")
send_telegram("🚀 Forex alert bot started in PRODUCTION MODE")

# ================= MAIN LOOP =================
while True:
    try:
        now_utc = datetime.datetime.now(datetime.timezone.utc)

        # --- Heartbeat ---
        if last_heartbeat is None or (now_utc - last_heartbeat).total_seconds() >= HEARTBEAT_INTERVAL:
            heartbeat_msg = f"🫀 Alert bot heartbeat at {now_utc.strftime('%Y-%m-%d %H:%M:%S UTC')}"
            if send_telegram(heartbeat_msg):
                logging.warning("[Heartbeat] Sent heartbeat alert")
            last_heartbeat = now_utc

        # --- Currency Strength Alert (H4) ---
        strength_alert_time = run_currency_strength_alert(
            oanda_api=OANDA_API,
            headers=HEADERS,
            last_alert_time=last_strength_alert_time,
            cooldown=STRENGTH_ALERT_COOLDOWN
        )
        if strength_alert_time != last_strength_alert_time:
            logging.warning("[Currency Strength] Alert sent")
            last_strength_alert_time = strength_alert_time

        # --- H1 Breakout Alerts ---
        breakout_pairs = []
        for pair in PAIRS:
            try:
                if check_breakout_h1(pair):
                    breakout_pairs.append(pair)
            except Exception as e:
                logging.error(f"[Breakout] Error checking {pair}: {e}", exc_info=True)

        if breakout_pairs:
            alert_msg = (
                f"📢 H1 Breakout Alert! ({len(breakout_pairs)} pairs) - {now_utc.strftime('%Y-%m-%d %H:%M UTC')}\n\n"
                + "\n".join(sorted(breakout_pairs))
            )
            if send_telegram(alert_msg):
                logging.warning(f"[Breakout] H1 breakout alert sent for pairs: {', '.join(breakout_pairs)}")

        # --- Group Breakout Alerts ---
        # run_group_breakout_alert() should return a dict: {currency: list_of_pairs_that_triggered_alert}
        group_alert_results = run_group_breakout_alert(last_group_alerts, min_pairs=3)

        # Log only currencies that actually triggered alerts with enough pairs
        for currency, breakout_pairs in group_alert_results.items():
            if breakout_pairs and isinstance(breakout_pairs, list) and len(breakout_pairs) >= 3:
                logging.warning(f"✅ Sent {currency} breakout alert: {', '.join(breakout_pairs)}")

        # Update last_group_alerts tracker with only the actual alerts sent
        last_group_alerts.update(group_alert_results)

        # --- Forex News Alerts ---
        events = fetch_forexfactory_events()
        for ev in events:
            event_key = f"{ev['currency']}_{ev['event']}"
            last_sent = last_news_alert_times.get(event_key)
            if last_sent:
                continue  # Skip already sent alerts

            msg = (
                f"📢 High-Impact Forex News Alert!\n"
                f"{ev['currency']} - {ev['event']}\n"
                f"Time: {ev['time'].strftime('%Y-%m-%d %H:%M UTC')}"
            )
            if send_telegram(msg):
                last_news_alert_times[event_key] = now_utc
                logging.warning(f"[News] Alert sent for {ev['currency']} - {ev['event']}")

        # Wait 5 minutes before next check
        time.sleep(300)

    except Exception as e:
        logging.error(f"Unexpected error in main loop: {e}", exc_info=True)
        time.sleep(60)