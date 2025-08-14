import logging
import datetime
from config import PAIRS, ATR_MULTIPLIER, ALERT_COOLDOWN
from utils import get_recent_candles, find_swing_points, atr, rsi, ema, send_telegram

def check_breakout_h1(pair):
    """
    Detects a breakout on H1 chart supported by ATR, EMA, and RSI.
    Returns True if breakout confirmed, else False.
    """
    try:
        candles = get_recent_candles(pair, "H1", 100)
        if not candles or len(candles) < 20:
            logging.info(f"Not enough candles to check breakout for {pair}")
            return False

        highs = [float(c['mid']['h']) for c in candles]
        lows = [float(c['mid']['l']) for c in candles]
        closes = [float(c['mid']['c']) for c in candles]

        current_atr = atr(candles)
        current_ema = ema(closes, period=50)
        current_rsi = rsi(closes, period=14)

        last_close = closes[-1]
        prev_close = closes[-2]

        # Detect swing highs and lows
        swing_highs, swing_lows = find_swing_points(candles)
        if swing_highs and isinstance(swing_highs[0], list):
            swing_highs = [item for sublist in swing_highs for item in sublist]
        if swing_lows and isinstance(swing_lows[0], list):
            swing_lows = [item for sublist in swing_lows for item in sublist]

        swing_high = max(swing_highs) if swing_highs else float('-inf')
        swing_low = min(swing_lows) if swing_lows else float('inf')

        breakout_up = last_close > swing_high and last_close > current_ema and current_rsi > 55
        breakout_down = last_close < swing_low and last_close < current_ema and current_rsi < 45

        price_move_up = last_close - prev_close >= ATR_MULTIPLIER * current_atr
        price_move_down = prev_close - last_close >= ATR_MULTIPLIER * current_atr

        if (breakout_up and price_move_up) or (breakout_down and price_move_down):
            return True

    except Exception as e:
        logging.error(f"Error in check_breakout_h1 for {pair}: {e}")

    return False


def run_breakout_alert(last_alert_times, threshold=1):
    """
    Sends a breakout alert if a pair breaks out on H1 and hasn't already been alerted.
    last_alert_times: dict storing {pair: last_alert_datetime}
    threshold: minimum number of breakout pairs to trigger alert
    """
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    breakout_pairs = []

    for pair in PAIRS:
        try:
            if check_breakout_h1(pair):
                last_sent = last_alert_times.get(pair)
                if last_sent is None or (now_utc - last_sent).total_seconds() >= ALERT_COOLDOWN:
                    breakout_pairs.append(pair)
                    logging.info(f"Breakout detected for {pair} (will alert)")
                else:
                    cooldown_remaining = ALERT_COOLDOWN - (now_utc - last_sent).total_seconds()
                    logging.info(f"Breakout detected for {pair} but cooldown active ({cooldown_remaining:.0f}s left)")
            else:
                logging.info(f"No breakout for {pair}")
        except Exception as e:
            logging.error(f"Error checking breakout for {pair}: {e}")

    if len(breakout_pairs) >= threshold:
        alert_msg = (
            f"📢 Breakout Alert! ({len(breakout_pairs)} pairs) - {now_utc.strftime('%Y-%m-%d %H:%M UTC')}\n\n"
            + "\n".join(sorted(breakout_pairs))
        )

        if send_telegram(alert_msg):
            logging.info(f"Sent breakout alert for pairs: {', '.join(breakout_pairs)}")
            # Update last alert time per pair
            for pair in breakout_pairs:
                last_alert_times[pair] = now_utc
        else:
            logging.warning("Failed to send breakout alert.")
    else:
        logging.info("No breakout alerts to send or all pairs in cooldown.")