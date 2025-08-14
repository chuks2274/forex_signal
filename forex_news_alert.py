import requests
import datetime
import time
import logging
from utils import send_telegram  # reuse your existing Telegram function

logging.basicConfig(level=logging.INFO)

FOREX_FACTORY_CALENDAR_URL = "https://www.forexfactory.com/calendar.php"
ALERT_COOLDOWN = 3600  # 1 hour cooldown per event
CHECK_INTERVAL = 300    # check every 5 minutes

# Track last alert time per event
last_alert_times = {}

def fetch_forexfactory_events():
    """
    Fetch high-impact Forex events from ForexFactory calendar.
    Returns a list of dicts: {'time': ..., 'currency': ..., 'event': ...}
    """
    try:
        r = requests.get(FOREX_FACTORY_CALENDAR_URL)
        r.raise_for_status()
        html = r.text

        import re, json
        matches = re.findall(r'var eventsData = (\[.*?\]);', html, re.S)
        if not matches:
            logging.warning("No events data found on ForexFactory page")
            return []

        events = json.loads(matches[0])
        high_impact_events = [
            {
                "time": datetime.datetime.strptime(ev['date'], "%Y-%m-%d %H:%M:%S"),
                "currency": ev['currency'],
                "event": ev['title']
            }
            for ev in events if ev['impact'] == "High"
        ]
        return high_impact_events

    except Exception as e:
        logging.error(f"Error fetching ForexFactory events: {e}")
        return []

def send_news_alert():
    """
    Check upcoming events and send Telegram alerts if within 60 minutes and cooldown passed.
    """
    events = fetch_forexfactory_events()
    if not events:
        return

    now = datetime.datetime.utcnow()

    for ev in events:
        event_key = f"{ev['currency']}_{ev['event']}"
        last_sent = last_alert_times.get(event_key)

        # Alert if event is within next 60 minutes and cooldown passed
        seconds_until_event = (ev['time'] - now).total_seconds()
        if 0 <= seconds_until_event <= 3600:
            if last_sent is None or (now - last_sent).total_seconds() >= ALERT_COOLDOWN:
                msg = (
                    f"📢 High-Impact Forex News Alert!\n"
                    f"{ev['currency']} - {ev['event']}\n"
                    f"Time: {ev['time'].strftime('%Y-%m-%d %H:%M UTC')}"
                )
                if send_telegram(msg):
                    logging.info(f"Sent alert: {ev['currency']} - {ev['event']}")
                    last_alert_times[event_key] = now
                else:
                    logging.warning(f"Failed to send alert for {ev['currency']} - {ev['event']}")
            else:
                cooldown_remaining = ALERT_COOLDOWN - (now - last_sent).total_seconds()
                logging.info(f"Skipping alert for {ev['currency']} - {ev['event']} (cooldown {cooldown_remaining:.0f}s left)")

if __name__ == "__main__":
    logging.info("Forex news alert bot started!")
    while True:
        try:
            send_news_alert()
            time.sleep(CHECK_INTERVAL)
        except Exception as e:
            logging.error(f"Unexpected error in news alert loop: {e}")
            time.sleep(60)