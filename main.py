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
CHAT_ID = "-1004481336360"

SCAN_INTERVAL = "5m"        # Zaman Dilimi
LOOKBACK_BARS = 20         # Ortalama hacim için bakılacak mum sayısı
VOLUME_MULTIPLIER = 6.0    # Hacim kat çarpanı
RSI_THRESHOLD = 50.0      # Minimum RSI eşiği
RSI_PERIOD = 14            # RSI periyodu
MIN_24H_VOLUME_USDT = 200_000_000  # Minimum 24s Hacim (USDT)

BINANCE_SPOT_URL = "https://api.binance.com"
BINANCE_FUTURES_URL = "https://fapi.binance.com"

LAST_SIGNAL_CANDLE_TIME = {}

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

# ==================== LONG/SHORT & FUNDING RATE BİLGİSİ ÇEKME ====================
def get_futures_metrics(symbol):
    """
    Binance Futures API'den ilgili coin için:
    - Long/Short hesap oranını (%)
    - Anlık Funding Rate (FR %) değerini çeker.
    """
    funding_rate_str = "N/A"
    long_short_str = "N/A"

    try:
        # 1. Funding Rate (FR) Çekme
        fr_url = f"{BINANCE_FUTURES_URL}/fapi/v1/premiumIndex"
        fr_res = requests.get(fr_url, params={"symbol": symbol}, timeout=4).json()
        if isinstance(fr_res, dict) and "lastFundingRate" in fr_res:
            fr_val = float(fr_res["lastFundingRate"]) * 100
            funding_rate_str = f"%{fr_val:+.4f}"
    except Exception as e:
        print(f"FR Çekme Hatası ({symbol}): {e}", flush=True)

    try:
        # 2. Long/Short Global Account Ratio Çekme
        ls_url = f"{BINANCE_FUTURES_URL}/futures/data/globalLongShortAccountRatio"
        ls_params = {"symbol": symbol, "period": "5m", "limit": 1}
        ls_res = requests.get(ls_url, params=ls_params, timeout=4).json()
        if isinstance(ls_res, list) and len(ls_res) > 0:
            long_account = float(ls_res[0].get("longAccount", 0)) * 100
            short_account = float(ls_res[0].get("shortAccount", 0)) * 100
            long_short_str = f"%{long_account:.1f} L / %{short_account:.1f} S"
    except Exception as e:
        print(f"L/S Çekme Hatası ({symbol}): {e}", flush=True)

    return long_short_str, funding_rate_str

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
        fetch_limit = LOOKBACK_BARS + RSI_PERIOD + 20
        params = {"symbol": symbol, "interval": interval, "limit": fetch_limit}
        res = requests.get(url, params=params, timeout=4).json()

        if not isinstance(res, list) or len(res) < fetch_limit - 5:
            return False, 0, 0, 0, 0, 0

        df = pd.DataFrame(res, columns=[
            'time', 'open', 'high', 'low', 'close', 'volume',
            'close_time', 'qav', 'num_trades', 'taker_base_vol', 'taker_quote_vol', 'ignore'
        ])

        df['open'] = df['open'].astype(float)
        df['close'] = df['close'].astype(float)
        df['volume'] = df['volume'].astype(float)

        df['rsi'] = calculate_rsi(df['close'], period=RSI_PERIOD)

        last_candle_time = df['time'].iloc[-2]
        last_open = df['open'].iloc[-2]
        last_close = df['close'].iloc[-2]
        last_vol = df['volume'].iloc[-2]
        last_rsi = df['rsi'].iloc[-2]

        is_green = last_close > last_open
        if not is_green:
            return False, 0, 0, 0, 0, last_candle_time

        prev_volumes = df['volume'].iloc[-2 - LOOKBACK_BARS : -2]
        avg_volume = prev_volumes.mean()

        if avg_volume == 0:
            return False, 0, 0, 0, 0, last_candle_time

        vol_ratio = last_vol / avg_volume
        is_volume_spike = vol_ratio >= VOLUME_MULTIPLIER
        is_rsi_valid = last_rsi >= RSI_THRESHOLD

        if is_volume_spike and is_rsi_valid:
            return True, last_close, last_rsi, vol_ratio, avg_volume, last_candle_time

        return False, 0, 0, 0, 0, last_candle_time

    except Exception:
        return False, 0, 0, 0, 0, 0

# ==================== TARAMA YÜRÜTÜCÜ ====================
def scan_market(pairs, interval):
    match_count = 0

    for coin in pairs:
        symbol = coin['symbol']
        volume_24h = coin['volume_24h']
        market_type = coin['market_type']

        time.sleep(0.03)

        is_match, price, rsi, vol_ratio, avg_vol, candle_time = check_volume_rsi_conditions(symbol, interval, market_type)

        if is_match:
            signal_key = f"{symbol}_{market_type}"
            
            if signal_key in LAST_SIGNAL_CANDLE_TIME:
                if LAST_SIGNAL_CANDLE_TIME[signal_key] == candle_time:
                    continue

            LAST_SIGNAL_CANDLE_TIME[signal_key] = candle_time
            match_count += 1
            interval_label = INTERVAL_LABELS.get(interval, interval)
            
            # Long/Short Oranı ve Funding Rate Bilgilerini Çek
            ls_ratio, funding_rate = get_futures_metrics(symbol)

            trade_link = f"https://www.binance.com/en/futures/{symbol}" if market_type == "FUTURES" else f"https://www.binance.com/en/trade/{symbol}"

            msg = (
                f"🚨 *HACİM PATLAMASI & RSI SİNYALİ*\n"
                f"⏱️ *Zaman Dilimi:* `{interval_label}`\n\n"
                f"🪙 *Sembol:* #{symbol} `[{market_type}]`\n"
                f"💵 *Fiyat:* `${price}`\n"
                f"📊 *Hacim Artışı:* `{vol_ratio:.1f}x` *(Son {LOOKBACK_BARS} mum ortalamasının)*\n"
                f"📈 *RSI ({RSI_PERIOD}):* `{rsi:.2f}`\n"
                f"🕯️ *Mum Tipi:* `Yeşil (Yükseliş)`\n"
                f"⚖️ *Long / Short Oranı:* `{ls_ratio}`\n"
                f"💸 *Funding Rate (FR):* `{funding_rate}`\n\n"
                f"💰 *24S Hacim:* `${volume_24h:,.2f}`\n"
                f"🔗 [Binance {market_type.capitalize()} İşlem]({trade_link})"
            )

            send_telegram_msg(msg)
            print(f"-> SİNYAL: {symbol} [{market_type}] - Hacim: {vol_ratio:.1f}x - RSI: {rsi:.1f} - L/S: {ls_ratio} - FR: {funding_rate}", flush=True)

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
        f"📈 *RSI Şartı:* `>= {RSI_THRESHOLD}`\n"
        f"📊 *Ek Göstergeler:* `Long/Short Oranı & Funding Rate (FR)`"
    )

    while True:
        try:
            run_scanner()
        except Exception as e:
            print(f"Döngü Hatası: {e}", flush=True)

        time.sleep(30)
