import logging
import datetime
import time
from utils import send_telegram  # existing Telegram function

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

ALERT_COOLDOWN = 3600  # 1 hour cooldown per event
CHECK_INTERVAL = 5      # check every 5 seconds for testing

# Track last alert time per event
last_alert_times = {}

def fetch_forexfactory_events():
    """
    Mock fetch function: logs each call and returns 1 dummy high-impact event.
    Returns a list of dicts: {'time': ..., 'currency': ..., 'event': ...}
    """
    now = datetime.datetime.now(datetime.timezone.utc)
    logging.info(f"[News] fetch_forexfactory_events called at {now.isoformat()}")
    return [{
        "time": now + datetime.timedelta(minutes=30),
        "currency": "USD",
        "event": "Non-Farm Payrolls"
    }]

def send_news_alert():
    """
    Check upcoming events and send Telegram alerts if within 60 minutes and cooldown passed.
    Always logs each step.
    """
    events = fetch_forexfactory_events()
    now = datetime.datetime.now(datetime.timezone.utc)

    for ev in events:
        event_key = f"{ev['currency']}_{ev['event']}"
        last_sent = last_alert_times.get(event_key)
        seconds_until_event = (ev['time'] - now).total_seconds()

        logging.info(f"[News] Event: {ev['currency']} - {ev['event']} in {seconds_until_event/60:.1f} minutes")

        if 0 <= seconds_until_event <= 3600:  # 1 hour before event
            if last_sent is None or (now - last_sent).total_seconds() >= ALERT_COOLDOWN:
                msg = (
                    f"📢 High-Impact Forex News Alert!\n"
                    f"{ev['currency']} - {ev['event']}\n"
                    f"Time: {ev['time'].strftime('%Y-%m-%d %H:%M UTC')}"
                )
                if send_telegram(msg):
                    last_alert_times[event_key] = now
                    logging.info(f"[News] Alert sent for {ev['currency']} - {ev['event']}")
                else:
                    logging.warning(f"[News] Failed to send alert for {ev['currency']} - {ev['event']}")
            else:
                cooldown_remaining = ALERT_COOLDOWN - (now - last_sent).total_seconds()
                logging.info(f"[News] Cooldown active for {ev['currency']} - {ev['event']} ({cooldown_remaining:.0f}s left)")

if __name__ == "__main__":
    logging.info("Forex news alert bot started (standalone test)")
    while True:
        try:
            send_news_alert()
            time.sleep(CHECK_INTERVAL)
        except Exception as e:
            logging.error(f"[News] Unexpected error in news alert loop: {e}")
            time.sleep(5)