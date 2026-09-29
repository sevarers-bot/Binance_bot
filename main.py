import os
import sys
import time
import datetime
import requests
import numpy as np
from flask import Flask
from threading import Thread

# Print çıktılarının Render log paneline anında düşmesini sağlar
sys.stdout.reconfigure(line_buffering=True)

# ==================== RENDER İÇİN WEB SUNUCUSU ====================
app = Flask('')

@app.route('/')
def home():
    return "Bot 7/24 Kesintisiz Çalışıyor!"

def run_web_server():
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run_web_server)
    t.daemon = True
    t.start()

# ==================== KULLANICI AYARLARI ====================
TELEGRAM_TOKEN = "8951230002:AAFPbwIJ1Ky-oKVg1b4rhSQ7W9LsTnrHJDs"  # Telegram Bot Token
CHAT_ID = "6593284503"          # Telegram Chat ID

# Filtreleme Kriterleri:
MIN_VOLUME_USDT = 50_000_000   # 2 Milyon $ üzeri tüm coinler taranır
THRESHOLD_HIGH = 70.0         # %60 ve üzeri baskı oranı
THRESHOLD_LOW = 30.0          # %40 ve altı baskı oranı

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
        requests.post(url, json=payload, timeout=5)
    except Exception as e:
        print(f"Telegram Gönderim Hatası: {e}", flush=True)

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
    return round(100.0 - (100.0 / (1 + rs)), 2)

def get_rsi(symbol, interval="5m"):
    try:
        url = f"{BINANCE_FUTURES_URL}/fapi/v1/klines"
        params = {"symbol": symbol, "interval": interval, "limit": 50}
        res = requests.get(url, params=params, timeout=4).json()
        if isinstance(res, list):
            close_prices = [float(k[4]) for k in res]
            return calculate_rsi(close_prices, 14)
        return 50.0
    except Exception:
        return 50.0

def get_all_usdt_pairs():
    """Şartı sağlayan TÜM hacimli coin listesini getirir."""
    try:
        url = f"{BINANCE_FUTURES_URL}/fapi/v1/ticker/24hr"
        res = requests.get(url, timeout=6).json()
        
        if not isinstance(res, list):
            print(f"Binance API Yanıt Uyarısı: {res}", flush=True)
            return []

        valid_pairs = []
        for coin in res:
            if isinstance(coin, dict) and 'symbol' in coin and 'quoteVolume' in coin:
                symbol = coin['symbol']
                volume = float(coin['quoteVolume'])
                # Hacim şartını geçen tüm USDT çiftleri
                if symbol.endswith("USDT") and not symbol.startswith("1000") and volume >= MIN_VOLUME_USDT:
                    valid_pairs.append({
                        "symbol": symbol,
                        "volume": volume,
                        "price": float(coin.get('lastPrice', 0))
                    })
        return valid_pairs
    except Exception as e:
        print(f"Hacim Çekme Hatası: {e}", flush=True)
        return []

def get_funding_rate(symbol):
    try:
        url = f"{BINANCE_FUTURES_URL}/fapi/v1/premiumIndex"
        params = {"symbol": symbol}
        res = requests.get(url, params=params, timeout=3).json()
        if isinstance(res, dict) and 'lastFundingRate' in res:
            return float(res['lastFundingRate']) * 100
        return 0.0
    except Exception:
        return 0.0

def get_long_short_ratio(symbol):
    try:
        url = f"{BINANCE_FUTURES_URL}/futures/data/topLongShortAccountRatio"
        params = {"symbol": symbol, "period": "5m", "limit": 1}
        res = requests.get(url, params=params, timeout=3).json()
        if isinstance(res, list) and len(res) > 0 and isinstance(res[0], dict):
            long_ratio = float(res[0].get('longAccount', 0.5)) * 100
            short_ratio = float(res[0].get('shortAccount', 0.5)) * 100
            return round(long_ratio, 2), round(short_ratio, 2)
        return 50.0, 50.0
    except Exception:
        return 50.0, 50.0

# ==================== TARAMA SÜRECİ ====================

def run_scanner():
    now_str = datetime.datetime.now().strftime('%H:%M:%S')
    print(f"[{now_str}] Tüm Market İçin Tarama Başlatıldı...", flush=True)
    
    pairs = get_all_usdt_pairs()
    print(f"[{now_str}] Toplam Taranacak Coin Sayısı: {len(pairs)}", flush=True)

    if not pairs:
        print(f"[{now_str}] Coin listesi alınamadı. 1 dakika sonra tekrar denenecek.", flush=True)
        return

    match_count = 0
    for i, coin in enumerate(pairs):
        symbol = coin['symbol']
        price = coin['price']
        volume_m = coin['volume'] / 1_000_000

        # IP Ban yememek için sorgular arasına 0.25 saniye gecikme koyuyoruz
        time.sleep(0.25)
        long_pct, short_pct = get_long_short_ratio(symbol)
        
        funding_rate = get_funding_rate(symbol)

        # KURALLARA UYAN SHORT KOŞULU (Funding > 0 ve Long Baskısı)
        if funding_rate > 0 and long_pct >= THRESHOLD_HIGH and short_pct <= THRESHOLD_LOW:
            match_count += 1
            rsi_5m = get_rsi(symbol, "5m")
            rsi_1h = get_rsi(symbol, "1h")

            msg = (
                f"🔴 *KURALLARA UYAN SHORT SİNYALİ*\n\n"
                f"🪙 *Sembol:* #{symbol}\n"
                f"💵 *Fiyat:* `{price}`\n"
                f"📊 *24s Hacim:* `${volume_m:.2f}M`\n\n"
                f"🟢 *Long Oranı:* `%{long_pct}`\n"
                f"🔴 *Short Oranı:* `%{short_pct}`\n"
                f"💸 *Funding Rate:* `%{funding_rate:.4f}`\n\n"
                f"📈 *RSI (5dk):* `{rsi_5m}` | *RSI (1saat):* `{rsi_1h}`\n\n"
                f"🔗 [Binance Futures Grafik](https://www.binance.com/en/futures/{symbol})"
            )
            send_telegram_msg(msg)
            print(f"-> KURALLARA UYGUN SİNYAL BULUNDU: {symbol}", flush=True)

        # KURALLARA UYAN LONG KOŞULU (Funding < 0 ve Short Baskısı)
        elif funding_rate < 0 and short_pct >= THRESHOLD_HIGH and long_pct <= THRESHOLD_LOW:
            match_count += 1
            rsi_5m = get_rsi(symbol, "5m")
            rsi_1h = get_rsi(symbol, "1h")

            msg = (
                f"🟢 *KURALLARA UYAN LONG SİNYALİ*\n\n"
                f"🪙 *Sembol:* #{symbol}\n"
                f"💵 *Fiyat:* `{price}`\n"
                f"📊 *24s Hacim:* `${volume_m:.2f}M`\n\n"
                f"🔴 *Short Oranı:* `%{short_pct}`\n"
                f"🟢 *Long Oranı:* `%{long_pct}`\n"
                f"💸 *Funding Rate:* `%{funding_rate:.4f}`\n\n"
                f"📈 *RSI (5dk):* `{rsi_5m}` | *RSI (1saat):* `{rsi_1h}`\n\n"
                f"🔗 [Binance Futures Grafik](https://www.binance.com/en/futures/{symbol})"
            )
            send_telegram_msg(msg)
            print(f"-> KURALLARA UYGUN SİNYAL BULUNDU: {symbol}", flush=True)

    print(f"[{now_str}] Tarama Bitti. Kurallara Uyan Sinyal Sayısı: {match_count}", flush=True)

# ==================== ANA DÖNGÜ ====================

if __name__ == "__main__":
    keep_alive()
    time.sleep(600)
    send_telegram_msg("🤖 *Binance Kurallara Göre Tüm Piyasayı Tarayıcı Aktif!*")
    
    while True:
        try:
            run_scanner()
        except Exception as e:
            print(f"Döngü Hatası: {e}", flush=True)
        
        # Tüm piyasayı taradıktan sonra 4 dakika bekler
        time.sleep(240)
