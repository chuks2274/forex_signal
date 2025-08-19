import logging
import datetime
import feedparser
from utils import send_telegram  # existing Telegram function

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

RSS_URL = "https://www.forexfactory.com/ffcal_week_this.xml"
WATCHED_IMPACTS = ["High", "Medium"]

def fetch_forexfactory_events():
    """
    Fetch and parse Forex Factory RSS feed.
    Returns list of dicts: {time, currency, impact, event, actual}
    """
    feed = feedparser.parse(RSS_URL)
    events = []

    for entry in feed.entries:
        try:
            title = entry.title               # Event name
            currency = entry.tags[0].term     # Currency affected (e.g., USD)
            impact = entry.tags[1].term       # Impact level (High/Medium/Low)
            event_time = datetime.datetime(*entry.published_parsed[:6], tzinfo=datetime.timezone.utc)
            actual = getattr(entry, "ff_actual", None)  # Actual release value (if available)

            events.append({
                "time": event_time,
                "currency": currency,
                "impact": impact,
                "event": title,
                "actual": actual
            })
        except Exception as e:
            logging.warning(f"[News] Failed to parse entry: {e}")

    return events

def check_news_for_pairs(pairs):
    """
    Given a list of currencies (e.g. ["USD","CAD"]),
    return a list of relevant High/Medium events within 1 hour.
    """
    now = datetime.datetime.now(datetime.timezone.utc)
    events = fetch_forexfactory_events()
    alerts = []

    for ev in events:
        if ev["currency"] not in pairs:
            continue
        if ev["impact"] not in WATCHED_IMPACTS:
            continue

        seconds_until_event = (ev["time"] - now).total_seconds()

        # Only look at events happening within the next hour
        if 0 <= seconds_until_event <= 3600:
            alerts.append(ev)

    return alerts

def send_news_alert_for_trade(pair):
    """
    Called when a trade signal is triggered.
    Example: pair="USDCAD"
    Sends news alerts for both currencies in the pair if High/Medium impact news is upcoming.
    """
    base, quote = pair[:3], pair[3:]
    news_events = check_news_for_pairs([base, quote])

    for ev in news_events:
        msg = (
            f"⚠️ News Alert for {pair} trade!\n"
            f"{ev['currency']} - {ev['event']} ({ev['impact']})\n"
            f"Time: {ev['time'].strftime('%Y-%m-%d %H:%M UTC')}"
        )
        if send_telegram(msg):
            logging.info(f"[News] Alert sent for {ev['currency']} - {ev['event']}")