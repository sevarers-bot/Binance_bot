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
VOLUME_INTERVAL = "5m"        # Hacim patlaması kontrolü yapılan zaman dilimi
VOLUME_MULTIPLIER = 4.0       # Hacim katı şartı (4.0 = 4 Katı)
LOOKBACK_PERIOD = 20          # Ortalaması alınacak geçmiş mum sayısı

# LONG / SHORT ORANI ZAMAN DİLİMİ AYARI
LS_RATIO_INTERVAL = "4h"      # Long/Short oranının çekileceği zaman dilimi

# KATI HACİM FİLTRELERİ (ÖNEMSİZ PATLAMALARI ELER)
MIN_CANDLE_VOL_USDT = 1_000_000   # Mevcut mum hacmi EN AZ 500.000$(0.5M$) olmalı
MIN_AVG_VOL_USDT = 1_000_000      # Ortalama mum hacmi EN AZ 100.000$(0.1M$) olmalı
MIN_24H_VOLUME_USDT = 20_000_000# 24s genel hacmi en az 20M$ olmalı

BINANCE_FUTURES_URL = "https://fapi.binance.com"

# STATIC CATEGORY MAPPING (Sık işlem gören ana projeler için)
CATEGORY_MAP = {
    # Layer 1
    "BTC": "Layer 1 / Store of Value", "ETH": "Layer 1", "SOL": "Layer 1", "ADA": "Layer 1",
    "AVAX": "Layer 1", "NEAR": "Layer 1", "SUI": "Layer 1", "APT": "Layer 1", "SEI": "Layer 1",
    "DOT": "Layer 1", "ATOM": "Layer 1", "FTM": "Layer 1", "INJ": "Layer 1", "ALGO": "Layer 1",
    # Layer 2
    "MATIC": "Layer 2", "POL": "Layer 2", "OP": "Layer 2", "ARB": "Layer 2", "MANTA": "Layer 2",
    "STRK": "Layer 2", "ZK": "Layer 2", "METIS": "Layer 2", "BLAST": "Layer 2",
    # AI / Yapay Zeka
    "FET": "AI (Yapay Zeka)", "AGIX": "AI (Yapay Zeka)", "OCEAN": "AI (Yapay Zeka)", "RENDER": "AI / DePIN",
    "TAO": "AI (Yapay Zeka)", "NEAR": "AI / Layer 1", "ARKM": "AI / Analytics", "WLD": "AI / Identity",
    # DeFi / DEX
    "UNI": "DeFi / DEX", "AAVE": "DeFi / Lending", "MKR": "DeFi", "CRV": "DeFi", "LDO": "DeFi / Staking",
    "PENDLE": "DeFi / Yield", "ENA": "DeFi / Synthetic Dollar", "RAY": "DeFi / DEX",
    # Meme Coins
    "DOGE": "Meme", "SHIB": "Meme", "PEPE": "Meme", "BONK": "Meme", "FLOKI": "Meme", "WIF": "Meme",
    "BOME": "Meme", "MEME": "Meme", "POPCAT": "Meme",
    # Gaming / NFT / Metaverse
    "GALA": "Gaming / Metaverse", "AXS": "Gaming", "SAND": "Metaverse", "MANA": "Metaverse",
    "BEAM": "Gaming", "IMX": "Gaming / Layer 2", "PIXEL": "Gaming",
    # RWA / Oracle / Infrastructure
    "LINK": "Oracle / RWA", "PYTH": "Oracle", "TIA": "Modular Blockchain", "ALT": "Modular / Restaking",
    "ONDO": "RWA (Real World Assets)"
}

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

def get_coin_category(symbol):
    """Coin'in grubunu/kategorisini tespit eder"""
    base_asset = symbol.replace("USDT", "").replace("1000", "")
    
    # 1. Bilinen Haritadan Kontrol Et
    if base_asset in CATEGORY_MAP:
        return CATEGORY_MAP[base_asset]
    
    # 2. Binance ExchangeInfo'dan dinamik kontrol yap (Monitoring / Seed Tag Tespiti)
    try:
        url = f"{BINANCE_FUTURES_URL}/fapi/v1/exchangeInfo"
        res = requests.get(url, timeout=5).json()
        if "symbols" in res:
            for s in res["symbols"]:
                if s["symbol"] == symbol:
                    # Monitoring / Seed Tag Kontrolü
                    for filter_item in s.get("filters", []):
                        if filter_item.get("filterType") == "PERPETUAL_MINT":
                            pass
                    # Eğer Binance bazında özel bir etiket varsa ekle
                    status = s.get("status", "")
                    if status != "TRADING":
                        return f"⚠️ {status}"
    except Exception:
        pass

    return "Diğer / Altcoin"

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
            volumes = [float(k[7]) for k in res] # Index 7 = Toplam USDT Hacmi
            
            current_kline = res[-1]
            open_price = float(current_kline[1])
            close_price = float(current_kline[4])
            current_volume = volumes[-1]  # Şu anki mumun toplam USDT hacmi
            
            # Index 10 = Piyasa emriyle yapılan GERÇEK Alış (Taker Buy) USDT Hacmi
            taker_buy_volume = float(current_kline[10]) 
            taker_sell_volume = current_volume - taker_buy_volume
            
            past_volumes = volumes[:-1]   # Geçmiş N mum hacmi
            avg_volume = sum(past_volumes) / len(past_volumes)
            
            if current_volume < MIN_CANDLE_VOL_USDT or avg_volume < MIN_AVG_VOL_USDT:
                return False, 0.0, 0.0, 0.0, 0.0, 0.0, False
            
            if avg_volume > 0:
                ratio = current_volume / avg_volume
                if ratio >= multiplier:
                    is_green_candle = close_price >= open_price
                    return True, current_volume, avg_volume, ratio, taker_buy_volume, taker_sell_volume, is_green_candle
                    
        return False, 0.0, 0.0, 0.0, 0.0, 0.0, False
    except Exception:
        return False, 0.0, 0.0, 0.0, 0.0, 0.0, False

def get_top_trader_long_short_ratio(symbol, interval):
    """Top Trader Long/Short Hesap Oranını Çeker"""
    try:
        url = f"{BINANCE_FUTURES_URL}/futures/data/topLongShortAccountRatio"
        params = {"symbol": symbol, "period": interval, "limit": 1}
        res = requests.get(url, params=params, timeout=3).json()
        if isinstance(res, list) and len(res) > 0 and isinstance(res[0], dict):
            long_ratio = float(res[0].get('longAccount', 0.5)) * 100
            short_ratio = float(res[0].get('shortAccount', 0.5)) * 100
            return round(long_ratio, 2), round(short_ratio, 2)
        return 50.0, 50.0
    except Exception:
        return 50.0, 50.0

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
        
        is_spike, current_vol, avg_vol, ratio, long_vol, short_vol, is_green = check_volume_spike_and_breakdown(
            symbol, 
            VOLUME_INTERVAL, 
            VOLUME_MULTIPLIER, 
            LOOKBACK_PERIOD
        )

        if is_spike:
            funding_rate = get_funding_info(symbol)
            oi_amount, oi_usdt = get_open_interest(symbol, price)
            long_pct, short_pct = get_top_trader_long_short_ratio(symbol, LS_RATIO_INTERVAL)
            category = get_coin_category(symbol)
            
            current_vol_m = current_vol / 1_000_000
            avg_vol_m = avg_vol / 1_000_000
            long_vol_m = long_vol / 1_000_000
            short_vol_m = short_vol / 1_000_000
            oi_m = oi_usdt / 1_000_000
            match_count += 1

            candle_type = "🟢 LONG (MUM YEŞİL)" if is_green else "🔴 SHORT (MUM KIRMIZI)"

            msg = (
                f"🔥 *HACİM PATLAMASI SİNYALİ ({VOLUME_INTERVAL})*\n"
                f"Yön: *{candle_type}*\n\n"
                f"🪙 *Sembol:* #{symbol}\n"
                f"🏷️ *Kategori:* `{category}`\n"
                f"💵 *Fiyat:* `{price}`\n\n"
                f"⚡ *Hacim Artışı:* `{ratio:.2f} Kat` ({VOLUME_MULTIPLIER}x Üzeri)\n"
                f"📊 *Mevcut ({VOLUME_INTERVAL}) Toplam Hacim:* `${current_vol_m:.2f}M`\n"
                f"🟢 *Gerçek Alış (Long) Hacim:* `${long_vol_m:.2f}M`\n"
                f"🔴 *Gerçek Satış (Short) Hacim:* `${short_vol_m:.2f}M`\n"
                f"📈 *Ortalama Hacim:* `${avg_vol_m:.2f}M`\n\n"
                f"👥 *Trader Oranları ({LS_RATIO_INTERVAL}):*\n"
                f"🟢 *Long Oranı:* `%{long_pct}`\n"
                f"🔴 *Short Oranı:* `%{short_pct}`\n\n"
                f"🔓 *Open Interest:* `${oi_m:.2f}M` ({oi_amount:,.0f} Kontrat)\n"
                f"💸 *Funding Rate:* `{funding_rate}` (%{funding_rate*100:.4f})\n\n"
                f"🔗 [Binance Futures](https://www.binance.com/en/futures/{symbol})"
            )
            send_telegram_msg(msg)
            print(f"-> SİNYAL: {symbol} [{category}] ({ratio:.1f}x Hacim)", flush=True)

    print(f"[{now_str}] Tarama Bitti. Bulunan Sinyal Sayısı: {match_count}", flush=True)

# ==================== ANA DÖNGÜ ====================

if __name__ == "__main__":
    keep_alive()
    time.sleep(2)
    send_telegram_msg(f"🤖 *Hacim & Kategori Destekli Tarayıcı Aktif!*\nHacim Periyodu: `{VOLUME_INTERVAL}` ({VOLUME_MULTIPLIER}x)\nTrader Oran Periyodu: `{LS_RATIO_INTERVAL}`")
    
    while True:
        try:
            run_scanner()
        except Exception as e:
            print(f"Döngü Hatası: {e}", flush=True)
        
        time.sleep(300)
