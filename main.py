import os
import sys
import time
import datetime
from datetime import timezone, timedelta
import requests
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

# MANUEL HACİM VE ZAMAN DİLİMİ AYARLARI
VOLUME_INTERVAL = "5m"        # Hacim kontrolü yapılan zaman dilimi
VOLUME_MULTIPLIER = 4.0       # Kat şartı (4.0 = 4 Katı)
LOOKBACK_PERIOD = 20          # Ortalaması alınacak geçmiş mum sayısı

# KATI HACİM FİLTRELERİ (ÖNEMSİZ PATLAMALARI ELER)
MIN_CANDLE_VOL_USDT = 500_000   # Mevcut mum hacmi EN AZ 500.000$(0.5M$) olmalı!
MIN_AVG_VOL_USDT = 100_000      # Ortalama mum hacmi EN AZ 100.000$(0.1M$) olmalı!
MIN_24H_VOLUME_USDT = 20_000_000# 24s genel hacmi en az 20M$ olmalı!

BINANCE_FUTURES_URL = "https://fapi.binance.com"

# ==================== YARDIMCI FONKSİYONLAR ====================

def get_tr_time():
    """Türkiye Saatini (GMT+3) Döndürür"""
    return (datetime.datetime.now(timezone.utc) + timedelta(hours=3)).strftime('%H:%M:%S')

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

def get_all_usdt_pairs():
    """Filtreye uygun tüm USDT çiftlerini getirir"""
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
                    vol_24h = float(coin['quoteVolume'])
                except (ValueError, TypeError):
                    continue

                if symbol.endswith("USDT"):
                    if vol_24h >= MIN_24H_VOLUME_USDT:
                        valid_pairs.append({
                            "symbol": symbol,
                            "price": float(coin.get('lastPrice', 0))
                        })
        return valid_pairs
    except Exception as e:
        print(f"Sembol Çekme Hatası: {e}", flush=True)
        return []

def check_volume_spike_and_breakdown(symbol, interval, multiplier, lookback):
    try:
        url = f"{BINANCE_FUTURES_URL}/fapi/v1/klines"
        params = {"symbol": symbol, "interval": interval, "limit": lookback + 1}
        res = requests.get(url, params=params, timeout=4).json()
        
        if isinstance(res, list) and len(res) >= lookback:
            volumes = [float(k[7]) for k in res] # USDT Hacmi
            
            current_kline = res[-1]
            open_price = float(current_kline[1])
            close_price = float(current_kline[4])
            current_volume = volumes[-1]  # Şu anki mum hacmi
            
            past_volumes = volumes[:-1]   # Geçmiş N mum hacmi
            avg_volume = sum(past_volumes) / len(past_volumes)
            
            # --- EK HACİM KONTROLÜ (ÇÖP COİNLERİ ELER) ---
            if current_volume < MIN_CANDLE_VOL_USDT or avg_volume < MIN_AVG_VOL_USDT:
                return False, 0.0, 0.0, 0.0, 0.0, 0.0
            
            if avg_volume > 0:
                ratio = current_volume / avg_volume
                if ratio >= multiplier:
                    if close_price > open_price:
                        long_vol = current_volume
                        short_vol = 0.0
                    else:
                        long_vol = 0.0
                        short_vol = current_volume

                    return True, current_volume, avg_volume, ratio, long_vol, short_vol
                    
        return False, 0.0, 0.0, 0.0, 0.0, 0.0
    except Exception:
        return False, 0.0, 0.0, 0.0, 0.0, 0.0

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

# ==================== TARAMA SÜRECİ ====================

def run_scanner():
    now_str = get_tr_time()
    print(f"[{now_str}] Hacim Patlaması Taraması ({VOLUME_INTERVAL} - {VOLUME_MULTIPLIER} Kat) Başlatıldı...", flush=True)
    
    pairs = get_all_usdt_pairs()
    print(f"[{now_str}] Taranacak Çift Sayısı: {len(pairs)}", flush=True)

    if not pairs:
        return

    match_count = 0

    for coin in pairs:
        symbol = coin['symbol']
        price = coin['price']

        time.sleep(0.1)
        
        is_spike, current_vol, avg_vol, ratio, long_vol, short_vol = check_volume_spike_and_breakdown(
            symbol, 
            VOLUME_INTERVAL, 
            VOLUME_MULTIPLIER, 
            LOOKBACK_PERIOD
        )

        if is_spike:
            funding_rate = get_funding_info(symbol)
            oi_amount, oi_usdt = get_open_interest(symbol, price)
            
            current_vol_m = current_vol / 1_000_000
            avg_vol_m = avg_vol / 1_000_000
            long_vol_m = long_vol / 1_000_000
            short_vol_m = short_vol / 1_000_000
            oi_m = oi_usdt / 1_000_000
            match_count += 1

            candle_type = "🟢 LONG (MUM YEŞİL)" if long_vol > 0 else "🔴 SHORT (MUM KIRMIZI)"

            msg = (
                f"🔥 *HACİM PATLAMASI SİNYALİ ({VOLUME_INTERVAL})*\n"
                f"Yön: *{candle_type}*\n\n"
                f"🪙 *Sembol:* #{symbol}\n"
                f"💵 *Fiyat:* `{price}`\n\n"
                f"⚡ *Hacim Artışı:* `{ratio:.2f} Kat` ({VOLUME_MULTIPLIER}x Üzeri)\n"
                f"📊 *Mevcut ({VOLUME_INTERVAL}) Toplam Hacim:* `${current_vol_m:.2f}M`\n"
                f"🟢 *Long (Alış / Yeşil) Hacim:* `${long_vol_m:.2f}M`\n"
                f"🔴 *Short (Satış / Kırmızı) Hacim:* `${short_vol_m:.2f}M`\n"
                f"📈 *Ortalama Hacim:* `${avg_vol_m:.2f}M`\n\n"
                f"🔓 *Open Interest:* `${oi_m:.2f}M` ({oi_amount:,.0f} Kontrat)\n"
                f"💸 *Funding Rate:* `{funding_rate}` (%{funding_rate*100:.4f})\n\n"
                f"🔗 [Binance Futures](https://www.binance.com/en/futures/{symbol})"
            )
            send_telegram_msg(msg)
            print(f"-> SİNYAL: {symbol} ({ratio:.1f}x Hacim) - {candle_type}", flush=True)

    print(f"[{now_str}] Tarama Bitti. Bulunan Sinyal Sayısı: {match_count}", flush=True)

# ==================== ANA DÖNGÜ ====================

if __name__ == "__main__":
    keep_alive()
    time.sleep(2)
    send_telegram_msg(f"🤖 *Hacim Detaylı Tarayıcı Aktif!*\nPeriyot: `{VOLUME_INTERVAL}` | Eşik: `{VOLUME_MULTIPLIER}x`\nMin Mum Hacmi: `500K$`")
    
    while True:
        try:
            run_scanner()
        except Exception as e:
            print(f"Döngü Hatası: {e}", flush=True)
        
        time.sleep(300)
