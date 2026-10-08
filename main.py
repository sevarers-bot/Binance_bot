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

# ==================== WEB SUNUCUSU (Keep-Alive) ====================
app = Flask('')

@app.route('/')
def home():
    return "Hacim Patlaması & RSI Tarayıcı Aktif!"

def run_web_server():
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run_web_server)
    t.daemon = True
    t.start()

# ==================== MANUEL AYARLANABİLİR PARAMETRELER ====================
TELEGRAM_TOKEN = "8951230002:AAFPbwIJ1Ky-oKVg1b4rhSQ7W9LsTnrHJDs"
CHAT_ID = "YOUR_CHAT_ID"

# 1. Zaman Dilimi Ayarı ("5m", "15m", "1h", "4h" vb.)
SCAN_INTERVAL = "5m"  

# 2. Hacim Kıyaslaması İçin Geriye Dönük Mum Sayısı
LOOKBACK_BARS = 10  

# 3. Hacim Kat Çarpanı (Örn: Önceki 10 mumun ortalamasının 4 katı)
VOLUME_MULTIPLIER = 4.0  

# 4. Minimum RSI Eşiği
RSI_THRESHOLD = 50.0  

# 5. RSI Periyodu
RSI_PERIOD = 14  

# Genel Filtreler
MIN_24H_VOLUME_USDT = 5_000_000  # Tarama yapılacak min 24s hacim (USDT)
COOLDOWN_MINUTES = 30            # Aynı coine tekrar sinyal atması için geçmesi gereken süre (dk)

BINANCE_SPOT_URL = "https://api.binance.com"
BINANCE_FUTURES_URL = "https://fapi.binance.com"

SENT_SIGNALS = {}

INTERVAL_LABELS = {
    "1d": "1 Günlük (1D)",
    "4h": "4 Saatlik (4H)",
    "1h": "1 Saatlik (1H)",
    "15m": "15 Dakikalık (15M)",
    "5m": "5 Dakikalık (5M)",
    "1m": "1 Dakikalık (1M)"
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

# ==================== İNDİKATÖR HESAPLAMALARI ====================
def calculate_rsi(series, period=14):
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

# ==================== TEKNİK ANALİZ KONTROLÜ ====================
def check_volume_rsi_conditions(symbol, interval, market_type):
    base_url = BINANCE_SPOT_URL if market_type == "SPOT" else BINANCE_FUTURES_URL
    endpoint = "/api/v3/klines" if market_type == "SPOT" else "/fapi/v1/klines"

    try:
        url = f"{base_url}{endpoint}"
        # Yeterli RSI ve Hacim verisi için limit
        fetch_limit = LOOKBACK_BARS + RSI_PERIOD + 20
        params = {"symbol": symbol, "interval": interval, "limit": fetch_limit}
        res = requests.get(url, params=params, timeout=4).json()

        if not isinstance(res, list) or len(res) < fetch_limit - 5:
            return False, 0, 0, 0, 0

        df = pd.DataFrame(res, columns=[
            'time', 'open', 'high', 'low', 'close', 'volume',
            'close_time', 'qav', 'num_trades', 'taker_base_vol', 'taker_quote_vol', 'ignore'
        ])

        df['open'] = df['open'].astype(float)
        df['close'] = df['close'].astype(float)
        df['volume'] = df['volume'].astype(float)

        # RSI Hesapla
        df['rsi'] = calculate_rsi(df['close'], period=RSI_PERIOD)

        # iloc[-2] = Son KAPANAN mum
        last_open = df['open'].iloc[-2]
        last_close = df['close'].iloc[-2]
        last_vol = df['volume'].iloc[-2]
        last_rsi = df['rsi'].iloc[-2]

        # 1. ŞART: Mum Rengi YEŞİL olmalı
        is_green = last_close > last_open
        if not is_green:
            return False, 0, 0, 0, 0

        # 2. ŞART: Hacim, önceki LOOKBACK_BARS kadar mumun ortalamasından VOLUME_MULTIPLIER kat fazla olmalı
        prev_volumes = df['volume'].iloc[-2 - LOOKBACK_BARS : -2]
        avg_volume = prev_volumes.mean()

        if avg_volume == 0:
            return False, 0, 0, 0, 0

        vol_ratio = last_vol / avg_volume
        is_volume_spike = vol_ratio >= VOLUME_MULTIPLIER

        # 3. ŞART: RSI > RSI_THRESHOLD
        is_rsi_valid = last_rsi >= RSI_THRESHOLD

        if is_volume_spike and is_rsi_valid:
            return True, last_close, last_rsi, vol_ratio, avg_volume

        return False, 0, 0, 0, 0

    except Exception:
        return False, 0, 0, 0, 0

# ==================== TARAMA YÜRÜTÜCÜ ====================
def scan_market(pairs, interval):
    match_count = 0
    now = time.time()

    for coin in pairs:
        symbol = coin['symbol']
        volume_24h = coin['volume_24h']
        market_type = coin['market_type']

        signal_key = f"{symbol}_{market_type}"
        if signal_key in SENT_SIGNALS:
            last_sent_time = SENT_SIGNALS[signal_key]
            if (now - last_sent_time) < (COOLDOWN_MINUTES * 60):
                continue

        time.sleep(0.03)

        is_match, price, rsi, vol_ratio, avg_vol = check_volume_rsi_conditions(symbol, interval, market_type)

        if is_match:
            match_count += 1
            SENT_SIGNALS[signal_key] = now
            interval_label = INTERVAL_LABELS.get(interval, interval)
            
            trade_link = f"https://www.binance.com/en/futures/{symbol}" if market_type == "FUTURES" else f"https://www.binance.com/en/trade/{symbol}"

            msg = (
                f"🚨 *HACİM PATLAMASI & RSI SİNYALİ*\n"
                f"⏱️ *Zaman Dilimi:* `{interval_label}`\n\n"
                f"🪙 *Sembol:* #{symbol} `[{market_type}]`\n"
                f"💵 *Fiyat:* `${price}`\n"
                f"📊 *Hacim Artışı:* `{vol_ratio:.1f}x` *(Son {LOOKBACK_BARS} mum ortalamasının)*\n"
                f"📈 *RSI ({RSI_PERIOD}):* `{rsi:.2f}`\n"
                f"🕯️ *Mum Tipi:* `Yeşil (Yükseliş)`\n\n"
                f"💰 *24S Hacim:* `${volume_24h:,.2f}`\n"
                f"🔗 [Binance {market_type.capitalize()} İşlem]({trade_link})"
            )

            send_telegram_msg(msg)
            print(f"-> SİNYAL: {symbol} [{market_type}] - Hacim: {vol_ratio:.1f}x - RSI: {rsi:.1f}", flush=True)

    return match_count

def run_scanner():
    now_str = get_tr_time()
    interval_label = INTERVAL_LABELS.get(SCAN_INTERVAL, SCAN_INTERVAL)
    print(f"[{now_str}] Tarama Başlatıldı | Zaman Dilimi: {interval_label}...", flush=True)

    spot_pairs = get_spot_usdt_pairs()
    spot_matches = scan_market(spot_pairs, SCAN_INTERVAL)

    futures_pairs = get_futures_usdt_pairs()
    futures_matches = scan_market(futures_pairs, SCAN_INTERVAL)

    total_matches = spot_matches + futures_matches
    print(f"[{now_str}] Tarama Bitti. Toplam Bulunan: {total_matches}", flush=True)

if __name__ == "__main__":
    keep_alive()
    time.sleep(2)
    label = INTERVAL_LABELS.get(SCAN_INTERVAL, SCAN_INTERVAL)
    send_telegram_msg(
        f"🟢 *Hacim Patlaması & RSI Tarayıcı Aktif!*\n"
        f"⏱️ *Zaman Dilimi:* `{label}`\n"
        f"📊 *Hacim Şartı:* `Son {LOOKBACK_BARS} mum ortalamasının {VOLUME_MULTIPLIER}x katı (Yeşil Mum)`\n"
        f"📈 *RSI Şartı:* `>= {RSI_THRESHOLD}`"
    )

    while True:
        try:
            run_scanner()
        except Exception as e:
            print(f"Döngü Hatası: {e}", flush=True)

        # 5 dakikalık mumlar için 60-120 saniyede bir taramak idealdir
        time.sleep(60)
