import os
import time
import threading
from datetime import datetime, timezone

import requests
import pandas as pd
from flask import Flask, jsonify

BINANCE_URL = "https://api.binance.com"
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
MODE = os.getenv("MODE", "futures").strip().lower()
SYMBOLS = [s.strip().upper() for s in os.getenv("SYMBOLS", "BTCUSDT,ETHUSDT").split(",") if s.strip()]
INTERVAL = os.getenv("INTERVAL", "15m").strip()
CHECK_SECONDS = int(os.getenv("CHECK_SECONDS", "60"))

app = Flask(__name__)
last_signal = {}

def get_klines(symbol):
    # Public market data: no Binance API key is needed.
    endpoint = "/api/v3/klines"
    if MODE == "futures":
        endpoint = "/fapi/v1/klines"
        url = "https://fapi.binance.com" + endpoint
    else:
        url = BINANCE_URL + endpoint

    r = requests.get(url, params={"symbol": symbol, "interval": INTERVAL, "limit": 150}, timeout=15)
    r.raise_for_status()
    data = r.json()
    return pd.DataFrame(data, columns=[
        "open_time","open","high","low","close","volume",
        "close_time","quote_volume","trades","taker_base","taker_quote","ignore"
    ])

def indicators(df):
    close = pd.to_numeric(df["close"])
    volume = pd.to_numeric(df["volume"])

    ema9 = close.ewm(span=9, adjust=False).mean()
    ema21 = close.ewm(span=21, adjust=False).mean()

    delta = close.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    rs = gain / loss.replace(0, pd.NA)
    rsi = 100 - (100 / (1 + rs))

    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    macd_signal = macd.ewm(span=9, adjust=False).mean()

    vol_avg = volume.rolling(20).mean()
    return ema9, ema21, rsi, macd, macd_signal, vol_avg

def make_signal(df, symbol):
    ema9, ema21, rsi, macd, macd_signal, vol_avg = indicators(df)
    i = -2  # last fully closed candle

    close = float(df["close"].iloc[i])
    high = float(df["high"].iloc[i])
    low = float(df["low"].iloc[i])
    vol = float(df["volume"].iloc[i])

    score_long = 0
    score_short = 0

    if ema9.iloc[i] > ema21.iloc[i]:
        score_long += 1
    if ema9.iloc[i] < ema21.iloc[i]:
        score_short += 1
    if macd.iloc[i] > macd_signal.iloc[i]:
        score_long += 1
    if macd.iloc[i] < macd_signal.iloc[i]:
        score_short += 1
    if 50 <= float(rsi.iloc[i]) <= 70:
        score_long += 1
    if 30 <= float(rsi.iloc[i]) <= 50:
        score_short += 1
    if vol_avg.iloc[i] and vol > vol_avg.iloc[i]:
        if score_long > score_short:
            score_long += 1
        elif score_short > score_long:
            score_short += 1

    signal = None
    if score_long >= 3 and score_long > score_short:
        signal = "LONG" if MODE == "futures" else "BUY"
        direction = "LONG"
    elif score_short >= 3 and score_short > score_long:
        signal = "SHORT" if MODE == "futures" else "SELL"
        direction = "SHORT"
    else:
        return None

    # Conservative levels based on the latest closed candle.
    if direction == "LONG":
        sl = low
        risk = close - sl
        if risk <= 0:
            return None
        tp1, tp2, tp3 = close + risk, close + 2*risk, close + 3*risk
    else:
        sl = high
        risk = sl - close
        if risk <= 0:
            return None
        tp1, tp2, tp3 = close - risk, close - 2*risk, close - 3*risk

    return {
        "symbol": symbol,
        "signal": signal,
        "price": close,
        "rsi": float(rsi.iloc[i]),
        "ema9": float(ema9.iloc[i]),
        "ema21": float(ema21.iloc[i]),
        "macd": float(macd.iloc[i]),
        "macd_signal": float(macd_signal.iloc[i]),
        "volume_ratio": float(vol / vol_avg.iloc[i]) if vol_avg.iloc[i] else 0,
        "sl": sl,
        "tp1": tp1,
        "tp2": tp2,
        "tp3": tp3,
        "candle_time": int(df["close_time"].iloc[i]),
    }

def fmt_price(x):
    if x >= 1000:
        return f"{x:,.2f}"
    if x >= 1:
        return f"{x:.4f}"
    return f"{x:.8f}"

def send_telegram(text):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram variables are not configured yet.")
        print(text)
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    r = requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "text": text}, timeout=15)
    r.raise_for_status()

def format_signal(s):
    title = "BINANCE FUTURES" if MODE == "futures" else "BINANCE SPOT"
    return (
        f"📊 {title} — SEÑAL\n\n"
        f"Par: {s['symbol']}\n"
        f"Señal: {s['signal']}\n"
        f"Precio: {fmt_price(s['price'])}\n\n"
        f"RSI: {s['rsi']:.2f}\n"
        f"EMA 9: {fmt_price(s['ema9'])}\n"
        f"EMA 21: {fmt_price(s['ema21'])}\n"
        f"MACD: {s['macd']:.6f}\n"
        f"Volumen: {s['volume_ratio']:.2f}x promedio\n\n"
        f"🛑 Stop Loss: {fmt_price(s['sl'])}\n"
        f"🎯 TP1: {fmt_price(s['tp1'])}\n"
        f"🎯 TP2: {fmt_price(s['tp2'])}\n"
        f"🎯 TP3: {fmt_price(s['tp3'])}\n\n"
        f"⏱ Marco: {INTERVAL}\n"
        f"🕒 {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\n\n"
        f"⚠️ Señal informativa. El bot NO ejecuta órdenes."
    )

def scan():
    for symbol in SYMBOLS:
        try:
            df = get_klines(symbol)
            s = make_signal(df, symbol)
            if not s:
                continue

            key = f"{symbol}:{s['candle_time']}:{s['signal']}"
            if last_signal.get(symbol) == key:
                continue

            last_signal[symbol] = key
            message = format_signal(s)
            print(message)
            send_telegram(message)
        except Exception as e:
            print(f"[{symbol}] error: {e}")

def worker():
    while True:
        scan()
        time.sleep(CHECK_SECONDS)

@app.get("/")
def home():
    return jsonify({
        "status": "running",
        "mode": MODE,
        "symbols": SYMBOLS,
        "interval": INTERVAL,
        "orders_enabled": False
    })

@app.get("/health")
def health():
    return jsonify({"ok": True})

if __name__ == "__main__":
    threading.Thread(target=worker, daemon=True).start()
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)
