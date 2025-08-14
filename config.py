import os
import logging
from dotenv import load_dotenv

# Load .env from the same folder as this config.py
dotenv_path = os.path.join(os.path.dirname(__file__), '.env')
load_dotenv(dotenv_path)

# OANDA API configuration
OANDA_TOKEN = os.getenv("OANDA_TOKEN")
OANDA_ACCOUNT = os.getenv("OANDA_ACCOUNT")
OANDA_API = os.getenv("OANDA_API", "https://api-fxtrade.oanda.com/v3")

# Telegram configuration
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# ATR multiplier for breakout detection
try:
    ATR_MULTIPLIER = float(os.getenv("ATR_MULTIPLIER", 0.5))
except Exception as e:
    logging.error(f"Error loading ATR_MULTIPLIER: {e}")
    ATR_MULTIPLIER = 0.5

# Timeframes for analysis
try:
    TIMEFRAMES = os.getenv("TIMEFRAMES", "H1,H4,D").split(",")
except Exception as e:
    logging.error(f"Error loading TIMEFRAMES: {e}")
    TIMEFRAMES = ["H1", "H4", "D"]

# HTTP headers for OANDA API requests
HEADERS = {"Authorization": f"Bearer {OANDA_TOKEN}"}

# Currency pairs to monitor
PAIRS = [
    "EUR_USD", "GBP_USD", "USD_JPY", "USD_CHF",
    "AUD_USD", "NZD_USD", "USD_CAD",
    "EUR_GBP", "EUR_JPY", "GBP_JPY"
]

# Alert cooldowns in seconds
ALERT_COOLDOWN = 3600  # general breakout alerts
STRENGTH_ALERT_COOLDOWN = 3600  # currency strength alerts