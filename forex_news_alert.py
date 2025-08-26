import logging
import datetime
import requests
import asyncio
from threading import Lock
from utils import send_telegram
import json
import os

# ---------------- Logging ----------------
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger("forex_news_alert")

# ---------------- Config ----------------
WATCHED_CURRENCIES = ["EUR", "GBP", "USD", "JPY", "CHF", "AUD", "NZD", "CAD"]
WATCHED_IMPACTS = ["High", "Medium"]
NEWS_URL = "https://api.tradingeconomics.com/calendar?c=guest:guest"
PRE_ALERT_MINUTES = 60
IMPACT_EMOJI = {"High": "🔥", "Medium": "⚡"}

# ---------------- Alert Tracking ----------------
alerted_events = set()
alert_lock = Lock()
STATE_FILE = "bot_state.json"

# ---------------- Load State ----------------
if os.path.exists(STATE_FILE):
    try:
        with open(STATE_FILE, "r") as f:
            state = json.load(f)
            alerted_events.update(state.get("alerted_events", []))
        logger.info("✅ Restored alerted_events from state")
    except Exception as e:
        logger.error(f"Failed to restore state: {e}", exc_info=True)

# ---------------- Fetch News ----------------
async def fetch_tradingeconomics_events():
    try:
        response = requests.get(NEWS_URL, timeout=10)
        response.raise_for_status()
        events = response.json()
        return events
    except Exception as e:
        logger.error(f"[News] Failed to fetch events: {e}")
        return []

# ---------------- Filter Events ----------------
def filter_relevant_events(events, currencies, watched_impacts):
    filtered = []
    for event in events:
        impact = event.get("impact", "").capitalize()
        if not impact or impact not in watched_impacts:
            continue

        country = event.get("country", "")
        title = event.get("event", "")
        if country not in currencies and not any(cur in title for cur in currencies):
            continue

        try:
            event_time = datetime.datetime.fromtimestamp(int(event.get("date", 0))/1000, tz=datetime.timezone.utc)
        except Exception:
            continue

        filtered.append({
            "time": event_time,
            "currency": country,
            "impact": impact,
            "event": title,
            "actual": event.get("actual"),
            "forecast": event.get("forecast"),
            "previous": event.get("previous")
        })
    return filtered

# ---------------- Pre/Post Alerts ----------------
def trigger_pre_news_alert(event):
    now = datetime.datetime.now(datetime.timezone.utc)
    delta = event['time'] - now
    minutes_until_event = int(delta.total_seconds() / 60)

    if PRE_ALERT_MINUTES - 1 <= minutes_until_event <= PRE_ALERT_MINUTES + 1:
        event_id = f"{event['time']}_{event['currency']}_{event['event']}_pre"
        with alert_lock:
            if event_id in alerted_events:
                return
            alerted_events.add(event_id)

        emoji = IMPACT_EMOJI.get(event['impact'], "⚡")
        msg = (
            f"{emoji} Upcoming News Alert: {event['currency']} - {event['event']} ({event['impact']})\n"
            f"⏰ Time: {event['time'].strftime('%Y-%m-%d %H:%M UTC')} "
            f"(in {minutes_until_event} min)"
        )
        send_telegram(msg)
        logger.info(f"[News] Pre-news alert sent for {event['currency']} - {event['event']}")

def trigger_post_news_alert(event):
    if not event.get("actual"):
        return

    event_id = f"{event['time']}_{event['currency']}_{event['event']}_post"
    with alert_lock:
        if event_id in alerted_events:
            return
        alerted_events.add(event_id)

    msg = (
        f"{event['currency']} {event['event']}: "
        f"Actual {event.get('actual')}, Forecast {event.get('forecast')}, Previous {event.get('previous')}"
    )
    send_telegram(msg)
    logger.info(f"[News] Post-news alert sent for {event['currency']} - {event['event']}")

# ---------------- Async News Loop (Updated Logging) ----------------
async def run_news_alert_loop(shutdown_event: asyncio.Event = None):
    """Continuously fetch news and send pre/post alerts, logging only new events."""
    logger.info("📡 Forex News Alert Loop Started!")
    seen_events = set()  # Track events already logged for cleaner output

    try:
        while shutdown_event is None or not shutdown_event.is_set():
            all_events = await fetch_tradingeconomics_events()
            now = datetime.datetime.now(datetime.timezone.utc)
            relevant_events = filter_relevant_events(all_events, WATCHED_CURRENCIES, WATCHED_IMPACTS)

            for ev in relevant_events:
                event_key = f"{ev['time']}_{ev['currency']}_{ev['event']}"
                if event_key not in seen_events:
                    logger.info(f"[News] New event detected: {ev['currency']} - {ev['event']} at {ev['time']}")
                    seen_events.add(event_key)

                delta = ev["time"] - now
                minutes_until_event = delta.total_seconds() / 60

                if 59 <= minutes_until_event <= 61:
                    trigger_pre_news_alert(ev)
                if now >= ev["time"]:
                    trigger_post_news_alert(ev)

            upcoming_events = [ev["time"] for ev in relevant_events if ev["time"] > now]
            if upcoming_events:
                next_event = min(upcoming_events)
                sleep_seconds = max((next_event - datetime.timedelta(minutes=PRE_ALERT_MINUTES) - now).total_seconds(), 10)
            else:
                sleep_seconds = 300
            await asyncio.sleep(sleep_seconds)

    except asyncio.CancelledError:
        logger.info("🛑 Forex News Alert loop cancelled")
    except Exception as e:
        logger.error(f"[News] Error in news loop: {e}", exc_info=True)
    finally:
        # Save alerted_events state on shutdown
        try:
            if alerted_events:
                if os.path.exists(STATE_FILE):
                    with open(STATE_FILE, "r") as f:
                        state = json.load(f)
                else:
                    state = {}
                state["alerted_events"] = list(alerted_events)
                with open(STATE_FILE, "w") as f:
                    json.dump(state, f, default=str)
                logger.info("💾 Forex News Alert state saved on shutdown")
        except Exception as e:
            logger.error(f"Failed to save forex news state: {e}", exc_info=True)
