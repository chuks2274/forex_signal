import time
import datetime
import logging
import threading
from currency_strength import run_currency_strength_alert
from trade_signal import run_trade_signal_loop, load_alerts, save_alerts
from breakout import run_group_breakout_alert
from utils import send_telegram, get_current_session
from config import STRENGTH_ALERT_COOLDOWN
from forex_news_alert import run_news_alert_loop  # corrected function name

# ---------------- Logging ----------------
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO
)
logger = logging.getLogger("forex_bot")

# ---------------- Trackers ----------------
last_strength_alert_time = 0
last_heartbeat = 0
HEARTBEAT_INTERVAL = 24 * 3600  # seconds

# ---------------- Startup ----------------
send_telegram("🚀 Forex alert bot started in PRODUCTION MODE")
logger.info("🚀 Forex bot started")

# Load previous alerts to maintain cooldowns
load_alerts()

# ---------------- Trade Signal Loop ----------------
def trade_signal_main_loop():
    global last_strength_alert_time
    try:
        while True:
            now_ts = time.time()
            current_session = get_current_session()

            # --- Currency Strength Alerts ---
            alerted_currencies, last_strength_alert_time = run_currency_strength_alert(
                last_alert_time=last_strength_alert_time,
                cooldown=STRENGTH_ALERT_COOLDOWN
            )

            # --- Trade Signals ---
            if alerted_currencies:
                run_trade_signal_loop(alerted_currencies)

            # --- Group Breakout Alerts ---
            run_group_breakout_alert(4)  # silent mode, adjust parameter if needed

            # --- Save alerts ---
            save_alerts()

            time.sleep(60)

    except KeyboardInterrupt:
        logger.info("🛑 Trade signal loop stopped by user")
        save_alerts()
    except Exception as e:
        logger.error(f"Unexpected error in trade loop: {e}", exc_info=True)
        save_alerts()
        time.sleep(60)

# ---------------- Heartbeat Loop ----------------
def heartbeat_loop():
    global last_heartbeat
    try:
        while True:
            now_ts = time.time()
            if now_ts - last_heartbeat >= HEARTBEAT_INTERVAL:
                now_utc = datetime.datetime.now(datetime.timezone.utc)
                heartbeat_msg = f"🫀 Heartbeat: {now_utc.strftime('%Y-%m-%d %H:%M:%S UTC')}"
                send_telegram(heartbeat_msg)
                last_heartbeat = now_ts
            time.sleep(300)  # check every 5 minutes
    except Exception as e:
        logger.error(f"Heartbeat loop error: {e}", exc_info=True)
        time.sleep(60)

# ---------------- Entry Point ----------------
if __name__ == "__main__":
    # Run trade signal loop in a separate thread
    trade_thread = threading.Thread(target=trade_signal_main_loop, daemon=True)
    trade_thread.start()

    # Run news alert loop in a separate thread
    news_thread = threading.Thread(target=run_news_alert_loop, daemon=True)  # updated target
    news_thread.start()

    # Run heartbeat in main thread
    heartbeat_loop()
