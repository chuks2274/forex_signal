import logging
import time
import threading

from utils import send_telegram
from currency_strength import run_currency_strength_alert
from trade_signal import run_trade_signal_loop
from forex_news_alert import run_news_alert_loop
from breakout import run_group_breakout_alert
from config import STRENGTH_ALERT_COOLDOWN

# ---------------- Logging ----------------
logger = logging.getLogger("forex_bot")
logger.setLevel(logging.INFO)
logging.basicConfig(format="%(asctime)s [%(levelname)s] %(message)s")

# ---------------- Cooldown Trackers ----------------
last_trade_alert_times = {}  # key = (pair, "strength_alert")
last_heartbeat_time = 0
GROUP_BREAKOUT_COOLDOWN = 3600  # 1 hour per group
HEARTBEAT_COOLDOWN = 24 * 3600  # once per day

# ---------------- Heartbeat ----------------
def send_heartbeat():
    global last_heartbeat_time
    now = time.time()
    if now - last_heartbeat_time >= HEARTBEAT_COOLDOWN:
        if send_telegram("💓 Bot Heartbeat: Forex bot is running"):
            logger.info("✅ Sent Bot Heartbeat alert")
            last_heartbeat_time = now

# ---------------- Thread Loops ----------------
def trade_signal_thread():
    """Continuously runs trade signal loop every minute (after M15 retest)."""
    while True:
        try:
            filtered_strength, _ = run_currency_strength_alert(
                last_trade_alert_times=last_trade_alert_times,
            )
            run_trade_signal_loop(filtered_strength)
        except Exception as e:
            logger.error(f"Unexpected error in trade loop: {e}")
        time.sleep(60)

def news_alert_thread():
    """Continuously runs news alerts loop every minute."""
    while True:
        try:
            run_news_alert_loop()
        except Exception as e:
            logger.error(f"Unexpected error in news alert loop: {e}")
        time.sleep(60)

def currency_strength_thread():
    """Runs full currency strength update periodically."""
    while True:
        try:
            run_currency_strength_alert(last_trade_alert_times=last_trade_alert_times)
        except Exception as e:
            logger.error(f"Unexpected error in currency strength loop: {e}")
        time.sleep(STRENGTH_ALERT_COOLDOWN)

def group_breakout_thread():
    """Checks group breakout alerts every minute with per-group cooldown."""
    while True:
        try:
            run_group_breakout_alert(
                min_pairs=4,
                send_alert_fn=send_telegram,
                group_cooldown=GROUP_BREAKOUT_COOLDOWN
            )
        except Exception as e:
            logger.error(f"Unexpected error in group breakout loop: {e}")
        time.sleep(60)

# ---------------- Main ----------------
if __name__ == "__main__":
    logger.info("🚀 Forex bot started")

    # Immediate heartbeat on startup
    send_heartbeat()

    # Start all threads
    threading.Thread(target=trade_signal_thread, daemon=True).start()
    threading.Thread(target=news_alert_thread, daemon=True).start()
    threading.Thread(target=currency_strength_thread, daemon=True).start()
    threading.Thread(target=group_breakout_thread, daemon=True).start()

    # Keep main thread alive and send daily heartbeat
    while True:
        send_heartbeat()
        time.sleep(60)
