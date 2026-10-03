import os
import sys
import time
import datetime
from datetime import timezone, timedelta
import requests
import pandas as pd
from flask import Flask
from threading import Thread

sys.stdout.reconfigure(line_buffering=True)

# ==================== WEB SUNUCUSU (Railway / Render Keep-Alive) ====================
app = Flask('')

@app.route('/')
def home():
    return "Spot & Futures EMA Tarayıcı Bot Aktif!"

def run_web_server():
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run_web_server)
    t.daemon = True
    t.start()

# ==================== KULLANICI AYARLARI ====================
TELEGRAM_TOKEN = "8951230002:AAFPbwIJ1Ky-oKVg1b4rhSQ7W9LsTnrHJDs"
CHAT_ID = "-1004481336360"

# --------------------------------------------------------------------------
# MANUEL ZAMAN DİLİMİ AYARI (İstediğinizi seçip tırnak içine yazın)
# Seçenekler: "1d" (Günlük), "4h" (4 Saatlik), "1h" (1 Saatlik), "15m" (15 Dakikalık), "5m" (5 Dakikalık)
# --------------------------------------------------------------------------
SCAN_INTERVAL = "1h"  

# Filtre: En az kaç USDT 24S hacimli coinler taransın
MIN_24H_VOLUME_USDT = 10_000_000   

BINANCE_SPOT_URL = "https://api.binance.com"
BINANCE_FUTURES_URL = "https://fapi.binance.com"

# Zaman dilimi açıklamaları
INTERVAL_LABELS = {
    "1d": "1 Günlük (1D)",
    "4h": "4 Saatlik (4H)",
    "1h": "1 Saatlik (1H)",
    "15m": "15 Dakikalık (15M)",
    "5m": "5 Dakikalık (5M)"
}

def get_tr_time():
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

# ==================== SEMBOL LİSTELERİ ====================
def get_spot_usdt_pairs():
    try:
        url = f"{BINANCE_SPOT_URL}/api/v3/ticker/24hr"
        res = requests.get(url, timeout=6).json()
        if not isinstance(res, list):
            return []

        valid_pairs = []
        for coin in res:
            symbol = coin.get('symbol', '')
            vol_24h = float(coin.get('quoteVolume', 0))
            price = float(coin.get('lastPrice', 0))

            if symbol.endswith("USDT") and not any(x in symbol for x in ["UPUSDT", "DOWNUSDT", "BEARUSDT", "BULLUSDT"]):
                if vol_24h >= MIN_24H_VOLUME_USDT:
                    valid_pairs.append({
                        "symbol": symbol,
                        "price": price,
                        "volume_24h": vol_24h,
                        "market_type": "SPOT"
                    })
        return valid_pairs
    except Exception as e:
        print(f"Spot Sembol Çekme Hatası: {e}", flush=True)
        return []

def get_futures_usdt_pairs():
    try:
        url = f"{BINANCE_FUTURES_URL}/fapi/v1/ticker/24hr"
        res = requests.get(url, timeout=6).json()
        if not isinstance(res, list):
            return []

        valid_pairs = []
        for coin in res:
            symbol = coin.get('symbol', '')
            vol_24h = float(coin.get('quoteVolume', 0))
            price = float(coin.get('lastPrice', 0))

            if symbol.endswith("USDT") and vol_24h >= MIN_24H_VOLUME_USDT:
                valid_pairs.append({
                    "symbol": symbol,
                    "price": price,
                    "volume_24h": vol_24h,
                    "market_type": "FUTURES"
                })
        return valid_pairs
    except Exception as e:
        print(f"Futures Sembol Çekme Hatası: {e}", flush=True)
        return []

# ==================== METRİK ÇEKME ====================
def get_futures_market_metrics(symbol):
    """Futures İçin Funding Rate (FR), Open Interest (OI) ve Top Trader Long/Short Oranı"""
    fr_pct = 0.0
    oi_usdt = 0.0
    top_long_pct = 50.0
    top_short_pct = 50.0

    # 1. Funding Rate
    try:
        fr_res = requests.get(f"{BINANCE_FUTURES_URL}/fapi/v1/premiumIndex", params={"symbol": symbol}, timeout=3).json()
        fr_pct = float(fr_res.get("lastFundingRate", 0)) * 100
    except Exception:
        pass

    # 2. Open Interest
    try:
        oi_res = requests.get(f"{BINANCE_FUTURES_URL}/fapi/v1/openInterest", params={"symbol": symbol}, timeout=3).json()
        oi_amount = float(oi_res.get("openInterest", 0))
        price_res = requests.get(f"{BINANCE_FUTURES_URL}/fapi/v1/ticker/price", params={"symbol": symbol}, timeout=3).json()
        current_price = float(price_res.get("price", 0))
        oi_usdt = oi_amount * current_price
    except Exception:
        pass

    # 3. Top Trader Long/Short Ratio
    try:
        ls_res = requests.get(f"{BINANCE_FUTURES_URL}/futures/data/topLongShortPositionRatio", params={"symbol": symbol, "period": "5m", "limit": 1}, timeout=3).json()
        if isinstance(ls_res, list) and len(ls_res) > 0:
            top_long_pct = float(ls_res[0].get("longAccount", 0.5)) * 100
            top_short_pct = float(ls_res[0].get("shortAccount", 0.5)) * 100
    except Exception:
        pass

    return fr_pct, oi_usdt, top_long_pct, top_short_pct

# ==================== TEKNİK ANALİZ (PANDAS EMA) ====================
def check_ema_conditions(symbol, interval, market_type):
    """
    1. EMA 20 > EMA 50
    2. Fiyat > EMA 20 ve Fiyat > EMA 50
    3. Fiyat EMA 200'ü yukarı kesti mi?
    (Harici 'ta' kütüphanesi yerine dahili Pandas ewm kullanılmıştır)
    """
    base_url = BINANCE_SPOT_URL if market_type == "SPOT" else BINANCE_FUTURES_URL
    endpoint = "/api/v3/klines" if market_type == "SPOT" else "/fapi/v1/klines"

    try:
        url = f"{base_url}{endpoint}"
        params = {"symbol": symbol, "interval": interval, "limit": 220}
        res = requests.get(url, params=params, timeout=4).json()

        if not isinstance(res, list) or len(res) < 205:
            return False, 0, 0, 0, 0

        df = pd.DataFrame(res, columns=[
            'time', 'open', 'high', 'low', 'close', 'volume',
            'close_time', 'qav', 'num_trades', 'taker_base_vol', 'taker_quote_vol', 'ignore'
        ])

        df['close'] = df['close'].astype(float)

        # Pandas dahili Exponential Moving Average (EMA)
        df['ema20'] = df['close'].ewm(span=20, adjust=False).mean()
        df['ema50'] = df['close'].ewm(span=50, adjust=False).mean()
        df['ema200'] = df['close'].ewm(span=200, adjust=False).mean()

        curr_close = df['close'].iloc[-1]
        prev_close = df['close'].iloc[-2]

        curr_ema20 = df['ema20'].iloc[-1]
        curr_ema50 = df['ema50'].iloc[-1]
        curr_ema200 = df['ema200'].iloc[-1]
        prev_ema200 = df['ema200'].iloc[-2]

        c1 = curr_ema20 > curr_ema50
        c2 = (curr_close > curr_ema20) and (curr_close > curr_ema50)
        c3 = (prev_close <= prev_ema200) and (curr_close > curr_ema200)

        if c1 and c2 and c3:
            return True, curr_close, curr_ema20, curr_ema50, curr_ema200

        return False, 0, 0, 0, 0

    except Exception:
        return False, 0, 0, 0, 0

# ==================== TARAMA YÜRÜTÜCÜ ====================
def scan_market(pairs, interval):
    match_count = 0
    for coin in pairs:
        symbol = coin['symbol']
        volume_24h = coin['volume_24h']
        market_type = coin['market_type']

        time.sleep(0.04)

        is_match, price, ema20, ema50, ema200 = check_ema_conditions(symbol, interval, market_type)

        if is_match:
            match_count += 1
            interval_label = INTERVAL_LABELS.get(interval, interval)
            
            if market_type == "FUTURES":
                fr_pct, oi_usdt, long_pct, short_pct = get_futures_market_metrics(symbol)
                fr_emoji = "🟢" if fr_pct >= 0 else "🔴"
                
                market_details = (
                    f"📊 *Futures Piyasa Verileri:*\n"
                    f"• {fr_emoji} *FR (Funding Rate):* `%{fr_pct:.4f}`\n"
                    f"• 💼 *Açık Pozisyon (OI):* `${oi_usdt:,.2f}`\n"
                    f"• 📊 *24S Hacim:* `${volume_24h:,.2f}`\n\n"
                    f"👥 *Top Trader Market Yüzdeleri:*\n"
                    f"🟢 *Long:* `%{long_pct:.2f}`  |  🔴 *Short:* `%{short_pct:.2f}`\n"
                )
                trade_link = f"https://www.binance.com/en/futures/{symbol}"
            else:
                market_details = (
                    f"📊 *Spot Piyasa Verileri:*\n"
                    f"• 📊 *24S Spot Hacim:* `${volume_24h:,.2f}`\n"
                )
                trade_link = f"https://www.binance.com/en/trade/{symbol}"

            msg = (
                f"🎯 *EMA 200 YUKARI KESİŞİM SİNYALİ*\n"
                f"⏱️ *Zaman Dilimi:* `{interval_label}`\n\n"
                f"🪙 *Sembol:* #{symbol} `[{market_type}]`\n"
                f"💵 *Anlık Fiyat:* `${price}`\n\n"
                f"📈 *Teknik Göstergeler:*\n"
                f"• *EMA 20:* `${ema20:,.4f}`\n"
                f"• *EMA 50:* `${ema50:,.4f}`\n"
                f"• *EMA 200:* `${ema200:,.4f}`\n"
                f"✅ *Fiyat > EMA 20 > EMA 50*\n"
                f"🚀 *Fiyat EMA 200'ü Yukarı Kesti!*\n\n"
                f"{market_details}\n"
                f"🔗 [Binance {market_type.capitalize()} Trade]({trade_link})"
            )

            send_telegram_msg(msg)
            print(f"-> {market_type} SİNYALİ: {symbol} [{interval_label}] - Fiyat: ${price}", flush=True)

    return match_count

def run_scanner():
    now_str = get_tr_time()
    interval_label = INTERVAL_LABELS.get(SCAN_INTERVAL, SCAN_INTERVAL)
    print(f"[{now_str}] Spot ve Futures Taraması Başlatıldı | Zaman Dilimi: {interval_label}...", flush=True)

    # 1. Spot Taraması
    spot_pairs = get_spot_usdt_pairs()
    spot_matches = scan_market(spot_pairs, SCAN_INTERVAL)

    # 2. Futures Taraması
    futures_pairs = get_futures_usdt_pairs()
    futures_matches = scan_market(futures_pairs, SCAN_INTERVAL)

    total_matches = spot_matches + futures_matches
    print(f"[{now_str}] Tarama Bitti. Toplam Sinyal: {total_matches} (Spot: {spot_matches}, Futures: {futures_matches})", flush=True)

if __name__ == "__main__":
    keep_alive()
    time.sleep(2)
    label = INTERVAL_LABELS.get(SCAN_INTERVAL, SCAN_INTERVAL)
    send_telegram_msg(f"🟢 *Çift Piyasa EMA Tarayıcısı Aktif!*\n⏱️ *Taranan Zaman Dilimi:* `{label}`\nKapsam: SPOT & FUTURES")

    while True:
        try:
            run_scanner()
        except Exception as e:
            print(f"Döngü Hatası: {e}", flush=True)

        time.sleep(300) # 5 dakikada bir tekrarlar
