import logging
import datetime
import time

from config import PAIRS, OANDA_API, HEADERS, ALERT_COOLDOWN, STRENGTH_ALERT_COOLDOWN
from currency_strength import run_currency_strength_alert
from breakout import check_breakout_h1, run_group_breakout_alert
from forex_news_alert import fetch_forexfactory_events
from utils import send_telegram

# ================= SETUP LOGGING =================
logger = logging.getLogger("forex_bot")
logger.setLevel(logging.WARNING)  # Only warnings and errors
logging.basicConfig(format='%(asctime)s [%(levelname)s] %(message)s')

# ================= LAST ALERT TRACKERS =================
last_strength_alert_time = None
last_h1_breakout_alerts = {}     # {pair: datetime}
last_group_alerts = {}           # {currency: date}
last_news_alert_times = {}       # {event_key: datetime}
last_heartbeat = None
HEARTBEAT_INTERVAL = 24 * 3600  # seconds

# Notify startup
logger.warning("Forex alert bot started! Running in PRODUCTION MODE")
send_telegram("🚀 Forex alert bot started in PRODUCTION MODE")

# ================= MAIN LOOP =================
while True:
    try:
        now_utc = datetime.datetime.now(datetime.timezone.utc)

        # --- Heartbeat ---
        if last_heartbeat is None or (now_utc - last_heartbeat).total_seconds() >= HEARTBEAT_INTERVAL:
            heartbeat_msg = f"🫀 Alert bot heartbeat at {now_utc.strftime('%Y-%m-%d %H:%M:%S UTC')}"
            if send_telegram(heartbeat_msg):
                logger.warning("[Heartbeat] Sent heartbeat alert")
            last_heartbeat = now_utc

        # --- Currency Strength Alert (H4, 4h cooldown) ---
        if last_strength_alert_time is None or (now_utc - last_strength_alert_time).total_seconds() >= STRENGTH_ALERT_COOLDOWN:
            strength_alert_time = run_currency_strength_alert(
                oanda_api=OANDA_API,
                headers=HEADERS,
                last_alert_time=last_strength_alert_time,
                cooldown=STRENGTH_ALERT_COOLDOWN
            )
            if strength_alert_time and strength_alert_time != last_strength_alert_time:
                last_strength_alert_time = strength_alert_time
                logger.warning("[Currency Strength] Alert sent")
        else:
            elapsed = (now_utc - last_strength_alert_time).total_seconds()
            logger.info(f"[Currency Strength] Cooldown active ({elapsed/3600:.2f}h elapsed)")

        # --- H1 Breakout Alerts (pair-level cooldown) ---
        h1_pairs_to_alert = []
        for pair in PAIRS:
            last_alert_time = last_h1_breakout_alerts.get(pair)
            if last_alert_time is None or (now_utc - last_alert_time).total_seconds() >= ALERT_COOLDOWN:
                try:
                    if check_breakout_h1(pair):
                        h1_pairs_to_alert.append(pair)
                        last_h1_breakout_alerts[pair] = now_utc
                except Exception as e:
                    logger.error(f"[H1 Breakout] Error checking {pair}: {e}", exc_info=True)

        if h1_pairs_to_alert:
            alert_msg = (
                f"📢 H1 Breakout Alert! ({len(h1_pairs_to_alert)} pairs) - {now_utc.strftime('%Y-%m-%d %H:%M UTC')}\n\n"
                + "\n".join(sorted(h1_pairs_to_alert))
            )
            if send_telegram(alert_msg):
                logger.warning(f"[H1 Breakout] Alert sent for pairs: {', '.join(h1_pairs_to_alert)}")

        # --- Group Breakout Alerts ---
        group_alert_results = run_group_breakout_alert(last_group_alerts, min_pairs=3)

        for currency, breakout_list in group_alert_results.items():
            if breakout_list and len(breakout_list) >= 3:
                last_alert_date = last_group_alerts.get(currency)
                today = now_utc.date()
                if last_alert_date != today:
                    if send_telegram(
                        f"📢 {currency} Group Breakout Alert! ({len(breakout_list)} pairs)\n\n" +
                        "\n".join(sorted(breakout_list))
                    ):
                        logger.warning(f"✅ Sent {currency} breakout alert: {', '.join(breakout_list)}")
                        last_group_alerts[currency] = today

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
                    last_news_alert_times[event_key] = now_utc
                    logger.warning(f"[News] Alert sent for {ev['currency']} - {ev['event']}")

        # Wait 5 minutes before next check
        time.sleep(300)

    except Exception as e:
        logger.error(f"Unexpected error in main loop: {e}", exc_info=True)
        time.sleep(60)