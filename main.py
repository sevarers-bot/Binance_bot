import os
import time
import datetime
import requests
import pandas as pd
import numpy as np
from flask import Flask
from threading import Thread

# ==================== RENDER İÇİN SAHTE WEB SUNUCUSU ====================
app = Flask('')

@app.route('/')
def home():
    return "Bot 7/24 Kesintisiz Çalışıyor!"

def run_web_server():
    # Render'ın atadığı dinamik PORT'u otomatik alır (Varsayılan: 10000)
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run_web_server)
    t.daemon = True
    t.start()

# ==================== KULLANICI AYARLARI ====================
TELEGRAM_TOKEN = "8951230002:AAFPbwIJ1Ky-oKVg1b4rhSQ7W9LsTnrHJDs"  # Bot Token'ınız
CHAT_ID = "6593284503"          # Chat ID'niz

# Dahasık sinyal alabilmek için esnetilmiş test eşik değerleri:
MIN_VOLUME_USDT = 5_000_000  # 5 Milyon $ üzeri hacim
THRESHOLD_HIGH = 60.0        # %60 ve üzeri Long/Short oranı
THRESHOLD_LOW = 40.0         # %40 ve altı Long/Short oranı

BINANCE_FUTURES_URL = "https://fapi.binance.com"

# ==================== YARDIMCI FONKSİYONLAR ====================

def send_telegram_msg(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": message,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True
    }
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"Telegram Gönderim Hatası: {e}")

def calculate_rsi(prices, period=14):
    if len(prices) < period + 1:
        return 50.0
    deltas = np.diff(prices)
    gains = np.where(deltas > 0, deltas, 0)
    losses = np.where(deltas < 0, -deltas, 0)
    
    avg_gain = np.mean(gains[:period])
    avg_loss = np.mean(losses[:period])
    
    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        
    if avg_loss == 0:
        return 100.0
    
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1 + rs))

def get_rsi(symbol, interval="5m"):
    try:
        url = f"{BINANCE_FUTURES_URL}/fapi/v1/klines"
        params = {"symbol": symbol, "interval": interval, "limit": 100}
        res = requests.get(url, params=params, timeout=5).json()
        close_prices = [float(k[4]) for k in res]
        return round(calculate_rsi(close_prices, 14), 2)
    except Exception:
        return 0.0

def get_top_usdt_pairs():
    try:
        url = f"{BINANCE_FUTURES_URL}/fapi/v1/ticker/24hr"
        res = requests.get(url, timeout=10).json()
        valid_pairs = []
        for coin in res:
            symbol = coin['symbol']
            volume = float(coin['quoteVolume'])
            if symbol.endswith("USDT") and volume >= MIN_VOLUME_USDT:
                valid_pairs.append({
                    "symbol": symbol,
                    "volume": volume,
                    "price": float(coin['lastPrice'])
                })
        return valid_pairs
    except Exception as e:
        print(f"Hacim çekme hatası: {e}")
        return []

def get_funding_rate(symbol):
    try:
        url = f"{BINANCE_FUTURES_URL}/fapi/v1/premiumIndex"
        params = {"symbol": symbol}
        res = requests.get(url, params=params, timeout=5).json()
        return float(res['lastFundingRate']) * 100
    except Exception:
        return 0.0

def get_long_short_ratio(symbol):
    try:
        url = f"{BINANCE_FUTURES_URL}/futures/data/topLongShortAccountRatio"
        params = {"symbol": symbol, "period": "5m", "limit": 1}
        res = requests.get(url, params=params, timeout=5).json()
        if res and isinstance(res, list) and len(res) > 0:
            long_ratio = float(res[0]['longAccount']) * 100
            short_ratio = float(res[0]['shortAccount']) * 100
            return round(long_ratio, 2), round(short_ratio, 2)
        return 50.0, 50.0
    except Exception:
        return 50.0, 50.0

# ==================== TARAMA SÜRECİ ====================

def run_scanner():
    now_str = datetime.datetime.now().strftime('%H:%M:%S')
    print(f"[{now_str}] Binance Taraması Başlatıldı...")
    
    pairs = get_top_usdt_pairs()
    print(f"Taranan Hacimli Çift Sayısı: {len(pairs)}")

    match_count = 0
    for coin in pairs:
        symbol = coin['symbol']
        price = coin['price']
        volume_m = coin['volume'] / 1_000_000

        long_pct, short_pct = get_long_short_ratio(symbol)
        funding_rate = get_funding_rate(symbol)

        # OLASI SHORT KOŞULU
        if funding_rate > 0 and long_pct >= THRESHOLD_HIGH and short_pct <= THRESHOLD_LOW:
            match_count += 1
            rsi_5m = get_rsi(symbol, "5m")
            rsi_1h = get_rsi(symbol, "1h")

            msg = (
                f"🔴 *OLASI SHORT SİNYALİ*\n\n"
                f"🪙 *Sembol:* #{symbol}\n"
                f"💵 *Fiyat:* `{price}`\n"
                f"📊 *24s Hacim:* `${volume_m:.2f}M`\n\n"
                f"🟢 *Long Oranı:* `%{long_pct}`\n"
                f"🔴 *Short Oranı:* `%{short_pct}`\n"
                f"💸 *Funding Rate:* `%{funding_rate:.4f}` (Pozitif)\n\n"
                f"📈 *RSI (5dk):* `{rsi_5m}`\n"
                f"📉 *RSI (1saat):* `{rsi_1h}`\n\n"
                f"🔗 [Binance Futures Grafik](https://www.binance.com/en/futures/{symbol})"
            )
            send_telegram_msg(msg)
            time.sleep(1)

        # OLASI LONG KOŞULU
        elif funding_rate < 0 and short_pct >= THRESHOLD_HIGH and long_pct <= THRESHOLD_LOW:
            match_count += 1
            rsi_5m = get_rsi(symbol, "5m")
            rsi_1h = get_rsi(symbol, "1h")

            msg = (
                f"🟢 *OLASI LONG SİNYALİ*\n\n"
                f"🪙 *Sembol:* #{symbol}\n"
                f"💵 *Fiyat:* `{price}`\n"
                f"📊 *24s Hacim:* `${volume_m:.2f}M`\n\n"
                f"🔴 *Short Oranı:* `%{short_pct}`\n"
                f"🟢 *Long Oranı:* `%{long_pct}`\n"
                f"💸 *Funding Rate:* `%{funding_rate:.4f}` (Negatif)\n\n"
                f"📈 *RSI (5dk):* `{rsi_5m}`\n"
                f"📉 *RSI (1saat):* `{rsi_1h}`\n\n"
                f"🔗 [Binance Futures Grafik](https://www.binance.com/en/futures/{symbol})"
            )
            send_telegram_msg(msg)
            time.sleep(1)

    print(f"[{now_str}] Tarama Bitti. Bulunan Sinyal Sayısı: {match_count}")

# ==================== ANA DÖNGÜ ====================

if __name__ == "__main__":
    keep_alive()
    time.sleep(2)
    send_telegram_msg("🤖 *Binance Long/Short & Funding Rate Tarayıcı Aktif!*")
    
    # Bot açılır açılmaz beklemeden İLK taramayı hemen yap
    try:
        run_scanner()
    except Exception as e:
        print(f"İlk Tarama Hatası: {e}")

    # Ardından her 5 dakikada bir düzenli çalışmaya devam et
    while True:
        try:
            # 300 saniye (5 dakika) bekle ve tara
            time.sleep(300)
            run_scanner()
        except Exception as e:
            print(f"Döngü Hatası: {e}")
            time.sleep(10)
