import time
import datetime
import logging
from config import OANDA_API, HEADERS, PAIRS, ALERT_COOLDOWN, STRENGTH_ALERT_COOLDOWN
from currency_strength import run_currency_strength_alert
from breakout import check_breakout_h1
from forex_news_alert import fetch_forexfactory_events
from utils import send_telegram

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s: %(message)s')

# Initialize last alert trackers
last_strength_alert_time = None
last_breakout_alert_times = {}  # {pair: datetime}
last_news_alert_times = {}      # {event_key: datetime}
last_heartbeat = None
HEARTBEAT_INTERVAL = 24 * 3600  # 24 hours in seconds

logging.info("Forex alert bot started! Scheduler running...")

while True:
    try:
        now_utc = datetime.datetime.now(datetime.timezone.utc)
        logging.info(f"Checking alerts at {now_utc.strftime('%Y-%m-%d %H:%M:%S UTC')}")

        # --- Heartbeat Alert ---
        if last_heartbeat is None or (now_utc - last_heartbeat).total_seconds() >= HEARTBEAT_INTERVAL:
            heartbeat_msg = f"🫀 Alert bot is running fine at {now_utc.strftime('%Y-%m-%d %H:%M:%S UTC')}"
            if send_telegram(heartbeat_msg):
                logging.info("Sent heartbeat alert")
            else:
                logging.warning("Failed to send heartbeat alert")
            last_heartbeat = now_utc

        # --- Currency Strength Alert (every 4 hours) ---
        last_strength_alert_time = run_currency_strength_alert(
            oanda_api=OANDA_API,
            headers=HEADERS,
            last_alert_time=last_strength_alert_time,
            cooldown=STRENGTH_ALERT_COOLDOWN
        )

        # --- H1 Breakout Alerts ---
        breakout_pairs = []
        for pair in PAIRS:
            try:
                if check_breakout_h1(pair):
                    last_sent = last_breakout_alert_times.get(pair)
                    if last_sent is None or (now_utc - last_sent).total_seconds() >= ALERT_COOLDOWN:
                        breakout_pairs.append(pair)
                        logging.info(f"Breakout detected for {pair} (will alert)")
                    else:
                        logging.info(f"Breakout detected for {pair} but cooldown active")
                else:
                    logging.info(f"No breakout for {pair}")
            except Exception as e:
                logging.error(f"Error checking breakout for {pair}: {e}")

        if breakout_pairs:
            alert_msg = (
                f"📢 Breakout Alert! ({len(breakout_pairs)} pairs) - {now_utc.strftime('%Y-%m-%d %H:%M UTC')}\n\n"
                + "\n".join(sorted(breakout_pairs))
            )
            if send_telegram(alert_msg):
                logging.info(f"Sent breakout alert for pairs: {', '.join(breakout_pairs)}")
                for pair in breakout_pairs:
                    last_breakout_alert_times[pair] = now_utc
            else:
                logging.warning("Failed to send breakout alert.")

        # --- Forex News Alerts ---
        events = fetch_forexfactory_events()
        for ev in events:
            event_key = f"{ev['currency']}_{ev['event']}"
            last_sent = last_news_alert_times.get(event_key)
            seconds_until_event = (ev['time'] - now_utc).total_seconds()

            if 0 <= seconds_until_event <= 3600:  # 1 hour before event
                if last_sent is None or (now_utc - last_sent).total_seconds() >= ALERT_COOLDOWN:
                    msg = (
                        f"📢 High-Impact Forex News Alert!\n"
                        f"{ev['currency']} - {ev['event']}\n"
                        f"Time: {ev['time'].strftime('%Y-%m-%d %H:%M UTC')}"
                    )
                    if send_telegram(msg):
                        logging.info(f"Sent news alert: {ev['currency']} - {ev['event']}")
                        last_news_alert_times[event_key] = now_utc
                    else:
                        logging.warning(f"Failed to send news alert for {ev['currency']} - {ev['event']}")
                else:
                    cooldown_remaining = ALERT_COOLDOWN - (now_utc - last_sent).total_seconds()
                    logging.info(f"Skipping news alert for {ev['currency']} - {ev['event']} (cooldown {cooldown_remaining:.0f}s left)")
            else:
                logging.info(f"No news alert yet for {ev['currency']} - {ev['event']} (time until event: {seconds_until_event/60:.1f} min)")

        # --- Wait before next check ---
        time.sleep(60)  # check every 1 minute

    except Exception as e:
        logging.error(f"Unexpected error in main loop: {e}", exc_info=True)
        time.sleep(60)