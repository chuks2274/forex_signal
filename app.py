import logging
import asyncio
import time
import os
import json
from config import STRENGTH_ALERT_COOLDOWN
from utils import send_telegram
from trade_signal import run_trade_signal_loop
from currency_strength import run_currency_strength_alert
from forex_news_alert import run_news_alert_loop, alerted_events
from breakout import run_group_breakout_alert

# ---------------- Logging ----------------
logger = logging.getLogger("forex_bot")
logger.setLevel(logging.INFO)
logging.basicConfig(format="%(asctime)s [%(levelname)s] %(message)s")

# ---------------- Debug ----------------
DEBUG_MODE = False

# ---------------- Cooldown Trackers ----------------
last_trade_alert_times = {}
last_heartbeat_time = 0
GROUP_BREAKOUT_COOLDOWN = 3600
HEARTBEAT_COOLDOWN = 24 * 3600
STATE_FILE = "bot_state.json"

# ---------------- Graceful Shutdown ----------------
shutdown_event = asyncio.Event()

# ---------------- Load State on Startup ----------------
if os.path.exists(STATE_FILE):
    try:
        with open(STATE_FILE, "r") as f:
            state = json.load(f)
            last_trade_alert_times.update(state.get("last_trade_alert_times", {}))
            alerted_events.update(state.get("alerted_events", []))
        logger.info("✅ Restored bot state from bot_state.json")
    except Exception as e:
        logger.error(f"Failed to restore bot state: {e}", exc_info=True)

# ---------------- Save State ----------------
def save_state():
    """Save important bot state before shutdown."""
    try:
        state = {
            "last_trade_alert_times": last_trade_alert_times,
            "alerted_events": list(alerted_events)
        }
        with open(STATE_FILE, "w") as f:
            json.dump(state, f, default=str)
        logger.info("💾 Bot state saved successfully")
    except Exception as e:
        logger.error(f"Failed to save bot state: {e}", exc_info=True)

# ---------------- Heartbeat Loop ----------------
async def send_heartbeat_loop():
    global last_heartbeat_time

    # Send initial heartbeat immediately
    if await asyncio.to_thread(send_telegram, "💓 Bot Heartbeat: Forex bot is running"):
        logger.info("✅ Sent initial Bot Heartbeat alert")
        last_heartbeat_time = time.time()

    while not shutdown_event.is_set():
        now = time.time()
        if now - last_heartbeat_time >= HEARTBEAT_COOLDOWN:
            if await asyncio.to_thread(send_telegram, "💓 Bot Heartbeat: Forex bot is running"):
                logger.info("✅ Sent daily Bot Heartbeat alert")
                last_heartbeat_time = now
        await asyncio.sleep(60)

# ---------------- Currency Strength Loop ----------------
async def currency_strength_loop():
    while not shutdown_event.is_set():
        try:
            await asyncio.to_thread(run_currency_strength_alert, last_trade_alert_times)
        except Exception as e:
            logger.error(f"Unexpected error in currency strength loop: {e}", exc_info=True)
        await asyncio.sleep(STRENGTH_ALERT_COOLDOWN)

# ---------------- Group Breakout Loop ----------------
async def group_breakout_loop():
    while not shutdown_event.is_set():
        try:
            await asyncio.to_thread(
                run_group_breakout_alert,
                min_pairs=4,
                send_alert_fn=send_telegram,
                group_cooldown=GROUP_BREAKOUT_COOLDOWN
            )
        except Exception as e:
            logger.error(f"Unexpected error in group breakout loop: {e}", exc_info=True)
        await asyncio.sleep(60)

# ---------------- Trade Signal Loop ----------------
async def trade_signal_loop():
    await asyncio.to_thread(run_trade_signal_loop, debug=DEBUG_MODE)

# ---------------- Main ----------------
async def main():
    logger.info("🚀 Forex bot started")

    # Start all loops concurrently
    tasks = [
        asyncio.create_task(trade_signal_loop()),
        asyncio.create_task(run_news_alert_loop(shutdown_event)),  # Pass shutdown_event for graceful exit
        asyncio.create_task(currency_strength_loop()),
        asyncio.create_task(group_breakout_loop()),
        asyncio.create_task(send_heartbeat_loop())
    ]

    try:
        await asyncio.gather(*tasks)
    except (asyncio.CancelledError, KeyboardInterrupt):
        logger.info("🛑 Shutdown signal received, stopping bot...")
        shutdown_event.set()
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        save_state()
        logger.info("🟢 Bot stopped gracefully.")

# ---------------- Entry Point ----------------
if __name__ == "__main__":
    asyncio.run(main())
