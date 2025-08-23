import logging
import datetime
import requests
import time
import json
from threading import Lock
from utils import send_telegram, load_active_trades

# --- Setup logging ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger("news_alert")

# --- Config ---
WATCHED_CURRENCIES = ["EUR", "GBP", "USD", "JPY", "CHF", "AUD", "NZD", "CAD"]
WATCHED_IMPACTS = ["High", "Medium"]
NEWS_URL = "https://api.tradingeconomics.com/calendar?c=guest:guest"
PRE_ALERT_MINUTES = 60  # Pre-news alert 1 hour before event

IMPACT_EMOJI = {"High": "🔥", "Medium": "⚡"}

# --- Alert tracking ---
alerted_events = set()
alert_lock = Lock()

# ====================== News Fetching ======================
def fetch_tradingeconomics_events():
    """Fetch upcoming economic news events."""
    try:
        response = requests.get(NEWS_URL)
        response.raise_for_status()
        events = response.json()
        logger.info(f"[News] Fetched {len(events)} events from Trading Economics")
        return events
    except Exception as e:
        logger.error(f"[News] Failed to fetch events: {e}")
        return []

def filter_relevant_events(events, currencies, watched_impacts):
    """Filter events for tracked currencies and high/medium impact."""
    filtered = []
    for event in events:
        impact = event.get("impact", "").capitalize()
        country = event.get("country", "")
        title = event.get("event", "")
        timestamp = event.get("date", 0)

        if impact not in watched_impacts:
            continue
        if country not in currencies and not any(cur in title for cur in currencies):
            continue

        filtered.append({
            "time": datetime.datetime.fromtimestamp(timestamp / 1000, tz=datetime.timezone.utc),
            "currency": country,
            "impact": impact,
            "event": title,
            "actual": event.get("actual"),
            "forecast": event.get("forecast"),
            "previous": event.get("previous")
        })
    return filtered

# ====================== Alerts ======================
def trigger_pre_news_alert(event, pair, signal_type):
    now = datetime.datetime.now(datetime.timezone.utc)
    delta = event['time'] - now
    minutes_until_event = int(delta.total_seconds() / 60)

    if PRE_ALERT_MINUTES - 1 <= minutes_until_event <= PRE_ALERT_MINUTES + 1:
        event_id = f"{event['time']}_{event['currency']}_{event['event']}_{pair}_pre"
        with alert_lock:
            if event_id in alerted_events:
                return
            alerted_events.add(event_id)

        emoji = IMPACT_EMOJI.get(event['impact'], "⚡")
        msg = (
            f"{emoji} News Alert for {pair} trade! ({signal_type})\n"
            f"{event['currency']} - {event['event']} ({event['impact']})\n"
            f"⏰ Time: {event['time'].strftime('%Y-%m-%d %H:%M UTC')} "
            f"(in {minutes_until_event} min)"
        )
        send_telegram(msg)
        logger.info(f"[News] Pre-news alert sent for {event['currency']} - {event['event']}")

def trigger_post_news_alert(event):
    if event.get("actual") is None:
        return

    event_id = f"{event['time']}_{event['currency']}_{event['event']}_post"
    with alert_lock:
        if event_id in alerted_events:
            return
        alerted_events.add(event_id)

    msg = (
        f"{event['currency']} {event['event']}: "
        f"Actual {event['actual']}, Forecast {event['forecast']}, Previous {event['previous']}"
    )
    send_telegram(msg)
    logger.info(f"[News] Post-news alert sent for {event['currency']} - {event['event']}")

def send_news_alert_for_trade(trade):
    pair = trade["pair"]
    signal_type = trade["type"]
    base, quote = pair[:3], pair[3:]
    relevant_currencies = [base, quote]

    all_events = fetch_tradingeconomics_events()
    news_events = filter_relevant_events(all_events, relevant_currencies, WATCHED_IMPACTS)

    if news_events:
        for ev in news_events:
            trigger_pre_news_alert(ev, pair, signal_type)
            trigger_post_news_alert(ev)
    else:
        today = datetime.datetime.now(datetime.timezone.utc).date()
        event_id = f"no_news_{pair}_{today}"
        with alert_lock:
            if event_id not in alerted_events:
                alerted_events.add(event_id)
                msg = f"✅ No High/Medium news for {pair} in the next 24 hours."
                send_telegram(msg)
                logger.info(f"[News] No-news alert sent for {pair}")

# ====================== Continuous Loop ======================
def run_news_alert_loop():
    logger.info("📡 Forex News Alert Loop Started!")
    while True:
        try:
            active_trades = load_active_trades()  # load trades from active_trades.json
            for trade in active_trades:
                send_news_alert_for_trade(trade)
        except Exception as e:
            logger.error(f"[News] Error in news loop: {e}")
        time.sleep(300)  # every 5 minutes

# ---------------- Entry Point ----------------
if __name__ == "__main__":
    run_news_alert_loop()