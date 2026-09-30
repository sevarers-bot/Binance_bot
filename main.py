import os
import sys
import time
import datetime
import requests
import numpy as np
from flask import Flask
from threading import Thread

sys.stdout.reconfigure(line_buffering=True)

# ==================== WEB SUNUCUSU ====================
app = Flask('')

@app.route('/')
def home():
    return "Bot Aktif!"

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

MIN_VOLUME_USDT = 100_000_000 # En az 100 Milyon $ 24s Hacim
THRESHOLD_HIGH = 70.0         # %70 ve üzeri baskı
THRESHOLD_LOW = 30.0          # %30 ve altı baskı

# Funding Rate Filtreleri
FUNDING_SHORT_MIN = 0.0000    # Short için Funding Rate >= 0.0000
FUNDING_LONG_MAX = 0.0000     # Long için Funding Rate <= 0.0000

# 5dk RSI Filtre Sınırları
RSI_5M_SHORT_LIMIT = 75.0     # Short için 5dk RSI >= 75
RSI_5M_LONG_LIMIT = 40.0      # Long için 5dk RSI <= 40

BINANCE_FUTURES_URL = "https://fapi.binance.com"

# Önceki tarama sonuçlarını tutacak hafıza
previous_signals = {}

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
        print(f"Telegram Hatası: {e}", flush=True)

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

def get_open_interest(symbol, price):
    try:
        url = f"{BINANCE_FUTURES_URL}/fapi/v1/openInterest"
        params = {"symbol": symbol}
        res = requests.get(url, params=params, timeout=3).json()
        if isinstance(res, dict) and 'openInterest' in res:
            oi_amount = float(res['openInterest'])
            oi_usdt = oi_amount * price
            return oi_amount, oi_usdt
        return 0.0, 0.0
    except Exception:
        return 0.0, 0.0

def get_all_usdt_pairs():
    try:
        url = f"{BINANCE_FUTURES_URL}/fapi/v1/ticker/24hr"
        res = requests.get(url, timeout=6).json()
        if not isinstance(res, list):
            return []

        valid_pairs = []
        for coin in res:
            if isinstance(coin, dict) and 'symbol' in coin and 'quoteVolume' in coin:
                symbol = coin['symbol']
                try:
                    volume = float(coin['quoteVolume'])
                except (ValueError, TypeError):
                    continue

                if symbol.endswith("USDT") and not symbol.startswith("1000"):
                    if volume >= MIN_VOLUME_USDT:
                        valid_pairs.append({
                            "symbol": symbol,
                            "volume": volume,
                            "price": float(coin.get('lastPrice', 0))
                        })
        return valid_pairs
    except Exception as e:
        print(f"Hacim Çekme Hatası: {e}", flush=True)
        return []

def get_funding_info(symbol):
    try:
        url = f"{BINANCE_FUTURES_URL}/fapi/v1/premiumIndex"
        params = {"symbol": symbol}
        res = requests.get(url, params=params, timeout=3).json()
        if isinstance(res, dict) and 'lastFundingRate' in res:
            return float(res['lastFundingRate'])
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
    global previous_signals
    now_str = datetime.datetime.now().strftime('%H:%M:%S')
    print(f"[{now_str}] Min Hacim: 100M$ | 5dk RSI (Short>=75, Long<=40) | Tarama Başlatıldı...", flush=True)
    
    pairs = get_all_usdt_pairs()
    print(f"[{now_str}] 100M$ Hacim Şartını Geçen Çift Sayısı: {len(pairs)}", flush=True)

    if not pairs:
        return

    current_signals = {}
    match_count = 0

    for coin in pairs:
        if coin['volume'] < MIN_VOLUME_USDT:
            continue

        symbol = coin['symbol']
        price = coin['price']
        volume_m = coin['volume'] / 1_000_000

        time.sleep(0.15)
        funding_rate = get_funding_info(symbol)

        time.sleep(0.1)
        long_pct, short_pct = get_long_short_ratio(symbol)

        signal_type = None

        # 1. SHORT SİNYALİ (Funding >= 0.0000 + Long >= %70 + 5dk RSI >= 75)
        if funding_rate >= FUNDING_SHORT_MIN and long_pct >= THRESHOLD_HIGH and short_pct <= THRESHOLD_LOW:
            rsi_5m = get_rsi(symbol, "5m")
            if rsi_5m >= RSI_5M_SHORT_LIMIT:
                signal_type = "SHORT"

        # 2. LONG SİNYALİ (Funding <= 0.0000 + Short >= %70 + 5dk RSI <= 40)
        elif funding_rate <= FUNDING_LONG_MAX and short_pct >= THRESHOLD_HIGH and long_pct <= THRESHOLD_LOW:
            rsi_5m = get_rsi(symbol, "5m")
            if rsi_5m <= RSI_5M_LONG_LIMIT:
                signal_type = "LONG"

        if signal_type:
            # Sinyal onaylandığında RSI ve OI bilgilerini tamamla
            if signal_type == "SHORT":
                rsi_5m = rsi_5m if 'rsi_5m' in locals() else get_rsi(symbol, "5m")
            rsi_1h = get_rsi(symbol, "1h")
            oi_amount, oi_usdt = get_open_interest(symbol, price)
            oi_m = oi_usdt / 1_000_000
            match_count += 1

            # Mevcut değerleri kaydet
            current_signals[symbol] = {
                "volume_m": volume_m,
                "oi_m": oi_m,
                "long_pct": long_pct,
                "short_pct": short_pct
            }

            # Önceki veriler var mı kontrol et
            if symbol in previous_signals:
                prev = previous_signals[symbol]
                vol_str = f"`${prev['volume_m']:.2f}M` ➡️ `${volume_m:.2f}M`"
                oi_str = f"`${prev['oi_m']:.2f}M` ➡️ `${oi_m:.2f}M`"
                long_str = f"`%{prev['long_pct']}` ➡️️ `%{long_pct}`"
                short_str = f"`%{prev['short_pct']}` ➡️ `%{short_pct}`"
                repeat_note = "🔄 *[TEKRARLAYAN SİNYAL - DEĞİŞİMLER]*\n\n"
            else:
                vol_str = f"`${volume_m:.2f}M`"
                oi_str = f"`${oi_m:.2f}M`"
                long_str = f"`%{long_pct}`"
                short_str = f"`%{short_pct}`"
                repeat_note = ""

            if signal_type == "SHORT":
                header = "🔴 🔻 *SHORT SİNYALİ* 🔻 🔴"
            else:
                header = "🟢 🔺 *LONG SİNYALİ* 🔺 🟢"

            msg = (
                f"{header}\n\n"
                f"{repeat_note}"
                f"🪙 *Sembol:* #{symbol}\n"
                f"💵 *Fiyat:* `{price}`\n"
                f"📊 *24s Hacim:* {vol_str}\n"
                f"🔓 *Open Interest:* {oi_str} ({oi_amount:,.0f} Kontrat)\n\n"
                f"🟢 *Long Oranı:* {long_str}\n"
                f"🔴 *Short Oranı:* {short_str}\n"
                f"💸 *Funding Rate:* `{funding_rate}` (%{funding_rate*100:.4f})\n\n"
                f"📈 *RSI (5dk):* `{rsi_5m}` | *RSI (1saat):* `{rsi_1h}`\n\n"
                f"🔗 [Binance Futures](https://www.binance.com/en/futures/{symbol})"
            )
            send_telegram_msg(msg)
            print(f"-> SİNYAL ({signal_type}): {symbol}", flush=True)

    # Önceki hafızayı güncelle
    previous_signals = current_signals
    print(f"[{now_str}] Tarama Bitti. Bulunan Sinyal Sayısı: {match_count}", flush=True)

# ==================== ANA DÖNGÜ ====================

if __name__ == "__main__":
    keep_alive()
    time.sleep(2)
    send_telegram_msg("🤖 *5dk RSI Destekli Tarayıcı Aktif!*")
    
    while True:
        try:
            run_scanner()
        except Exception as e:
            print(f"Döngü Hatası: {e}", flush=True)
        
        time.sleep(600)  # 10 dakikada bir çalışır (600 saniye)
